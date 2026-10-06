"""Compilar la app Android en GitHub Actions desde el bot.

Pasos: subir la lista de servidores al secreto ZUMO_SERVIDORES (cifrada con la clave pública del
repo, formato "sealed box" de libsodium), lanzar el workflow, esperar que termine y bajar el APK
de la rama "apk". El token de GitHub (fine-grained, solo este repo, permisos Actions: write y
Secrets: write, y Contents: read) vive en /etc/zumo/bot.env y nunca va al repo.
"""
import base64
import ctypes
import ctypes.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import zipfile
import urllib.request

API = "https://api.github.com"


class ErrorGitHub(Exception):
    pass


# ------------------------------------------------------------------ secreto cifrado
def sellar(clave_publica_b64, texto):
    """crypto_box_seal(texto, clave_publica) → base64. PyNaCl si está; si no, libsodium por ctypes."""
    pk = base64.b64decode(clave_publica_b64)
    datos = texto.encode("utf-8")
    try:
        from nacl.public import PublicKey, SealedBox
        return base64.b64encode(SealedBox(PublicKey(pk)).encrypt(datos)).decode()
    except ImportError:
        pass
    nombre = ctypes.util.find_library("sodium")
    if not nombre:
        raise ErrorGitHub("Falta libsodium: apt install python3-nacl")
    lib = ctypes.CDLL(nombre)
    if lib.sodium_init() < 0:
        raise ErrorGitHub("No arranca libsodium")
    salida = ctypes.create_string_buffer(len(datos) + lib.crypto_box_sealbytes())
    if lib.crypto_box_seal(salida, datos, ctypes.c_ulonglong(len(datos)), pk) != 0:
        raise ErrorGitHub("No se pudo cifrar el secreto")
    return base64.b64encode(salida.raw).decode()


# ------------------------------------------------------------ clave de firma exportada
def abrir_clave_exportada(cifrado, contrasena):
    """Descifra clave-firma.enc (openssl aes-256-cbc -pbkdf2 -iter 200000 de un tar con zumo.jks y pass).
    Devuelve (keystore_bytes, contraseña_del_keystore)."""
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, "c.enc")
        with open(ruta, "wb") as f:
            f.write(cifrado)
        r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                            "-pass", "env:ZUMO_EXPORT", "-in", ruta],
                           capture_output=True, env={**os.environ, "ZUMO_EXPORT": contrasena})
        if r.returncode != 0:
            raise ErrorGitHub("No se pudo descifrar la clave exportada.")
        try:
            with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
                jks = t.extractfile("zumo.jks").read()
                clave = t.extractfile("pass").read().decode().strip()
        except Exception:
            raise ErrorGitHub("La clave exportada no tiene el formato esperado.") from None
    if len(jks) < 500 or not clave:
        raise ErrorGitHub("La clave exportada está vacía.")
    return jks, clave


# --------------------------------------------------------------------------- GitHub
class GitHub:
    def __init__(self, token, repo, workflow="android.yml", rama="main"):
        self.token, self.repo, self.workflow, self.rama = token, repo, workflow, rama
        # main publica en "apk"; cualquier otra rama en "apk-prueba" (ver android.yml)
        self.rama_apk = "apk" if rama == "main" else "apk-prueba"

    def _pedir(self, metodo, ruta, datos=None, raw=False, timeout=60):
        cab = {"Authorization": "Bearer " + self.token, "User-Agent": "zumo-bot",
               "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
        cuerpo = None
        if datos is not None:
            cuerpo = json.dumps(datos).encode()
            cab["Content-Type"] = "application/json"
        req = urllib.request.Request(API + ruta, data=cuerpo, headers=cab, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
        except urllib.error.HTTPError as e:
            detalle = ""
            try:
                detalle = json.loads(e.read().decode()).get("message", "")
            except Exception:
                pass
            raise ErrorGitHub(self._explicar(e.code, detalle)) from None
        except urllib.error.URLError as e:
            raise ErrorGitHub(f"Sin conexión con GitHub: {e.reason}") from None
        if raw:
            return b
        return json.loads(b) if b else {}

    @staticmethod
    def _explicar(codigo, detalle):
        if codigo in (401, 403):
            return f"GitHub rechazó el token ({codigo}). Revisá que no haya vencido y que tenga permisos Actions, Secrets y Contents. {detalle}".strip()
        if codigo == 404:
            return "GitHub no encontró el repo, el workflow o la rama (o el token no tiene acceso a este repo)."
        if codigo == 422:
            return f"GitHub no aceptó el pedido: {detalle}"
        return f"GitHub respondió {codigo}: {detalle}"

    def subir_secreto(self, nombre, valor):
        pk = self._pedir("GET", f"/repos/{self.repo}/actions/secrets/public-key")
        self._pedir("PUT", f"/repos/{self.repo}/actions/secrets/{nombre}",
                    {"encrypted_value": sellar(pk["key"], valor), "key_id": pk["key_id"]})

    def borrar_secreto(self, nombre):
        try:
            self._pedir("DELETE", f"/repos/{self.repo}/actions/secrets/{nombre}")
        except ErrorGitHub as e:
            if "no encontró" not in str(e):
                raise

    def artefacto(self, nombre, run_id):
        """Bytes del primer archivo del artefacto 'nombre' de ese run, o None si no existe."""
        r = self._pedir("GET", f"/repos/{self.repo}/actions/runs/{run_id}/artifacts?name={nombre}")
        arts = r.get("artifacts") or []
        if not arts:
            return None

        class SinRedireccion(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        req = urllib.request.Request(f"{API}/repos/{self.repo}/actions/artifacts/{arts[0]['id']}/zip",
                                     headers={"Authorization": "Bearer " + self.token, "User-Agent": "zumo-bot",
                                              "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.build_opener(SinRedireccion).open(req, timeout=60) as resp:
                zbytes = resp.read()
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
                raise ErrorGitHub(self._explicar(e.code, "")) from None
            # la URL firmada de descarga no lleva el token de GitHub
            with urllib.request.urlopen(urllib.request.Request(e.headers["Location"], headers={"User-Agent": "zumo-bot"}), timeout=120) as resp:
                zbytes = resp.read()
        with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
            return z.read(z.namelist()[0])

    def ultimo_run(self):
        r = self._pedir("GET", f"/repos/{self.repo}/actions/workflows/{self.workflow}/runs?per_page=1")
        runs = r.get("workflow_runs") or []
        return runs[0]["id"] if runs else 0

    def lanzar(self):
        self._pedir("POST", f"/repos/{self.repo}/actions/workflows/{self.workflow}/dispatches", {"ref": self.rama})

    def run_nuevo(self, despues_de, espera=90):
        """Id del run que acaba de crear el dispatch (el primero con id mayor al anterior)."""
        fin = time.time() + espera
        while time.time() < fin:
            r = self._pedir("GET", f"/repos/{self.repo}/actions/workflows/{self.workflow}/runs"
                                   f"?event=workflow_dispatch&branch={self.rama}&per_page=5")
            nuevos = [x for x in r.get("workflow_runs", []) if x["id"] > despues_de]
            if nuevos:
                return min(x["id"] for x in nuevos)
            time.sleep(4)
        raise ErrorGitHub("GitHub no arrancó la compilación (no apareció el run).")

    def run(self, run_id):
        return self._pedir("GET", f"/repos/{self.repo}/actions/runs/{run_id}")

    def pasos(self, run_id):
        """Progreso real del build por pasos: (completados, total, nombre_del_paso_actual).
        Devuelve (0, 0, None) si GitHub todavía no publicó los pasos (en cola)."""
        r = self._pedir("GET", f"/repos/{self.repo}/actions/runs/{run_id}/jobs")
        total = done = 0
        actual = None
        for job in r.get("jobs", []):
            for s in job.get("steps", []):
                total += 1
                if s.get("status") == "completed":
                    done += 1
                elif s.get("status") == "in_progress" and actual is None:
                    actual = s.get("name")
        return done, total, actual

    def archivo_de_rama(self, ruta, rama=None):
        return self._pedir("GET", f"/repos/{self.repo}/contents/{ruta}?ref={rama or self.rama_apk}", raw=True, timeout=180)
