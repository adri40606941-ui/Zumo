"""Compilar la app Android en la VPS del bot, sin depender de GitHub.

La VPS guarda:
- una copia del repo (git) en REPO, que se baja de GitHub cada día y antes de compilar; si GitHub no
  responde, se compila con la copia que ya está (y se puede editar ahí mismo por SSH);
- lo necesario para compilar (Java 17, Android SDK y NDK, Gradle), que instala instalar-compilador.sh.

Hace lo mismo que .github/workflows/android.yml: pone la lista de servidores y la apariencia que armó
el bot, firma con la clave de firma del centro y arma el APK. El bot lo manda por Telegram.
"""
import collections
import glob
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager

import marca

REPO = os.environ.get("ZUMO_REPO_DIR", "/opt/zumo-repo")
SDK = os.environ.get("ZUMO_SDK_DIR", "/opt/android-sdk")
GRADLE = os.environ.get("ZUMO_GRADLE", "/opt/gradle/bin/gradle")
SALIDA = os.environ.get("ZUMO_APK_DIR", "/opt/zumo-bot/apk")
INSTALADOR = "/opt/zumo-bot/instalar-compilador.sh"
NDK = "27.0.12077973"
TIMEOUT_COMPILAR = 40 * 60
MANTENER_APK = 3

# versionCode = minutos desde 2025-01-01 (UTC). GitHub usa la misma cuenta (ver android.yml): así un APK
# compilado en la VPS y otro compilado en GitHub siempre se pueden instalar uno encima del otro.
BASE_VERSION = 1735689600


class ErrorVps(Exception):
    """Algo no anduvo al compilar en la VPS (se muestra tal cual en el chat)."""


def version_code(ahora=None):
    return int(((time.time() if ahora is None else ahora) - BASE_VERSION) // 60)


# ------------------------------------------------------------------ estado de la instalación
def _hay_java():
    return shutil.which("java") is not None


def faltantes():
    """Lo que todavía no está instalado para poder compilar (lista vacía = todo listo)."""
    f = []
    if not os.path.isdir(os.path.join(REPO, ".git")):
        f.append("la copia del repo")
    elif not os.path.isfile(os.path.join(REPO, "android", "app", "hev-socks5-tunnel", "Android.mk")):
        f.append("el submódulo hev-socks5-tunnel")
    if not _hay_java():
        f.append("Java 17")
    if not os.path.isfile(GRADLE):
        f.append("Gradle")
    for ruta, nombre in ((os.path.join(SDK, "platforms", "android-34"), "Android SDK 34"),
                         (os.path.join(SDK, "build-tools", "34.0.0"), "build-tools 34"),
                         (os.path.join(SDK, "ndk", NDK), "Android NDK")):
        if not os.path.isdir(ruta):
            f.append(nombre)
    return f


# ------------------------------------------------------------------ git
def _git(*args, token="", timeout=300):
    cmd = ["git", "-C", REPO]
    if token:
        import base64
        cred = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {cred}"]
    try:
        return subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "tiempo agotado")


def commit_info():
    r = _git("log", "-1", "--format=%h %s (%cr)")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "sin datos"


def sincronizar(token="", rama="main"):
    """Baja los cambios de GitHub a la copia de la VPS. Nunca falla: devuelve (ok, mensaje).

    Si GitHub no responde, o la copia tiene cambios propios que no se unen solos con los de GitHub,
    no se toca nada y se sigue con la copia de la VPS.
    """
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return False, "La copia del repo no está instalada en la VPS."
    f = _git("fetch", "--quiet", "origin", rama, token=token, timeout=180)
    if f.returncode != 0:
        return False, f"No pude bajar de GitHub (¿está caído?). Sigo con la copia de la VPS: {commit_info()}"
    actual = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if actual != rama:
        return False, f"La copia de la VPS está en la rama «{actual}», no la toco: {commit_info()}"
    m = _git("merge", "--ff-only", "FETCH_HEAD")
    if m.returncode != 0:
        return False, ("La copia de la VPS tiene cambios propios que no se pueden unir solos con los de GitHub. "
                       f"Compilo con la de la VPS: {commit_info()}")
    s = _git("submodule", "update", "--init", "--recursive", token=token, timeout=600)
    if s.returncode != 0:
        return False, f"Copia al día, pero no pude actualizar un submódulo: {commit_info()}"
    return True, f"Copia al día con GitHub: {commit_info()}"


def subir(token="", rama="main"):
    """Sube a GitHub los cambios hechos en la copia de la VPS (los guarda en un commit si hace falta).
    Nunca fuerza: si GitHub tiene cambios que la copia no tiene, no sube nada. Devuelve (ok, mensaje)."""
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return False, "La copia del repo no está instalada en la VPS."
    if not token:
        return False, "Falta GITHUB_TOKEN en /etc/zumo/bot.env (con permiso de escritura: Contents → Read and write)."
    actual = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if actual != rama:
        return False, f"La copia de la VPS está en la rama «{actual}», no en «{rama}»: no subo nada."
    guardado = False
    if _git("status", "--porcelain").stdout.strip():
        _git("add", "-A")
        c = _git("-c", "user.name=Zumo VPS", "-c", "user.email=vps@zumo.local", "commit", "-q", "-m", "Cambios hechos en la VPS")
        if c.returncode != 0:
            return False, "No pude guardar los cambios: " + (c.stderr or c.stdout).strip()[-300:]
        guardado = True
    f = _git("fetch", "--quiet", "origin", rama, token=token, timeout=180)
    if f.returncode != 0:
        return False, "No pude hablar con GitHub (¿está caído, o el token venció?). Los cambios quedan guardados en la VPS."
    if _git("merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD").returncode != 0:
        return False, ("GitHub tiene cambios que esta copia no tiene. Primero 🔄 Sincronizar con GitHub "
                       "(si no se unen solos, hay que resolverlo por SSH en /opt/zumo-repo).")
    n = _git("rev-list", "--count", "FETCH_HEAD..HEAD").stdout.strip()
    if n in ("", "0"):
        return True, "No hay nada nuevo para subir: GitHub ya tiene todo."
    p = _git("push", "--quiet", "origin", f"HEAD:refs/heads/{rama}", token=token, timeout=300)
    if p.returncode != 0:
        err = (p.stderr or p.stdout).strip()
        if "403" in err or "denied" in err.lower() or "Permission" in err:
            return False, "GitHub no deja subir: el token solo puede leer. Creá uno con Contents → Read and write."
        return False, "No pude subir a GitHub: " + err[-300:]
    return True, f"Subí {n} cambio(s) a GitHub ({rama}): {commit_info()}" + (" (se guardaron en un commit nuevo)" if guardado else "")


# ------------------------------------------------------------------ compilar
@contextmanager
def _preservar(rutas):
    """Deja todo como estaba al salir (incluidos los cambios sin guardar que haya en la copia)."""
    tmp = tempfile.mkdtemp(prefix="zumo-comp-")
    guardado = []
    try:
        for i, r in enumerate(rutas):
            copia = os.path.join(tmp, str(i))
            if os.path.isdir(r):
                shutil.copytree(r, copia, symlinks=True)
                guardado.append((r, copia, "dir"))
            elif os.path.isfile(r):
                shutil.copy2(r, copia)
                guardado.append((r, copia, "file"))
            else:
                guardado.append((r, None, None))
        yield
    finally:
        for r, copia, tipo in guardado:
            if os.path.isdir(r) and not os.path.islink(r):
                shutil.rmtree(r, ignore_errors=True)
            elif os.path.lexists(r):
                os.remove(r)
            if tipo == "dir":
                shutil.copytree(copia, r, symlinks=True)
            elif tipo == "file":
                shutil.copy2(copia, r)
        shutil.rmtree(tmp, ignore_errors=True)


ERRORES = re.compile(r"^e: |FAILED|error:|Exception|BUILD|What went wrong|Execution failed", re.I)
TAREA = re.compile(r"^> Task (\S+)")


def compilar(lista_texto, paquete_marca, clave, url_actualizar="", progreso=None, token="", rama="main"):
    """Compila el APK y lo devuelve: {"apk": bytes, "codigo": int, "commit": str, "sync": (ok, msg)}.

    lista_texto: texto de la lista de servidores (formato servidores.txt) o "" para usar la del repo.
    paquete_marca: zip de apariencia (marca.empaquetar) o None para usar la del repo.
    clave: (ruta_jks, contraseña) de la clave de firma.
    """
    progreso = progreso or (lambda texto: None)
    f = faltantes()
    if f:
        raise ErrorVps("Falta instalar en la VPS: " + ", ".join(f) + f".\nCorré: bash {INSTALADOR}")
    if not clave:
        raise ErrorVps("Falta la clave de firma en la VPS. Sin ella el APK saldría con otra firma y los clientes "
                       "no podrían instalarlo encima del anterior. Traela de GitHub desde el bot (🔑).")
    jks, contrasena = clave
    sync = sincronizar(token, rama)
    progreso("🔄 " + sync[1])
    codigo = version_code()
    android = os.path.join(REPO, "android")
    env = {**os.environ,
           "ANDROID_HOME": SDK, "ANDROID_SDK_ROOT": SDK,
           "ZUMO_KEYSTORE": jks, "ZUMO_KS_PASS": contrasena,
           "ZUMO_VERSION_CODE": str(codigo), "ZUMO_ACTUALIZAR_URL": url_actualizar or "",
           "GRADLE_OPTS": "-Xmx1536m", "TERM": "dumb"}
    cmd = [GRADLE, "-p", android, "assembleRelease", "--no-daemon", "--console=plain", "--max-workers=2",
           "-Dorg.gradle.jvmargs=-Xmx1536m"]
    ultimas = collections.deque(maxlen=80)
    with _preservar([os.path.join(android, "servidores.txt"), os.path.join(android, "marca")]):
        if lista_texto:
            with open(os.path.join(android, "servidores.txt"), "w", encoding="utf-8") as fh:
                fh.write(lista_texto if lista_texto.endswith("\n") else lista_texto + "\n")
        if paquete_marca:
            try:
                marca.desempacar(paquete_marca, os.path.join(android, "marca"))
            except marca.ErrorMarca as e:
                raise ErrorVps(f"La apariencia guardada en el bot está dañada: {e}.") from None
        for viejo in glob.glob(os.path.join(android, "app", "build", "outputs", "apk", "release", "*.apk")):
            os.remove(viejo)
        progreso("🔨 Compilando en la VPS… (la primera vez tarda más)")
        p = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        reloj = threading.Timer(TIMEOUT_COMPILAR, p.kill)
        reloj.start()
        t0 = ultimo = time.time()
        try:
            for linea in p.stdout:
                linea = linea.rstrip()
                ultimas.append(linea)
                t = TAREA.match(linea)
                if t and time.time() - ultimo > 20:
                    ultimo = time.time()
                    progreso(f"🔨 Compilando en la VPS… {int((ultimo - t0) // 60)} min\n⚙️ {t.group(1)}")
            p.wait()
        finally:
            reloj.cancel()
            p.stdout.close()
        if p.returncode != 0:
            importantes = [l for l in ultimas if ERRORES.search(l)] or list(ultimas)
            motivo = "tardó más de 40 minutos y se cortó" if p.returncode in (-9, 137) else "falló"
            raise ErrorVps(f"La compilación en la VPS {motivo}.\n" + "\n".join(importantes[-25:]))
        apks = [a for a in glob.glob(os.path.join(android, "app", "build", "outputs", "apk", "release", "*.apk"))
                if "unsigned" not in os.path.basename(a)]
        if not apks:
            raise ErrorVps("La compilación terminó pero no dejó un APK firmado (¿la clave de firma es válida?).")
        apk = max(apks, key=os.path.getmtime)
        os.makedirs(SALIDA, exist_ok=True)
        destino = os.path.join(SALIDA, f"zumo-vpn-{codigo}.apk")
        shutil.copy2(apk, destino)
    viejos = sorted(glob.glob(os.path.join(SALIDA, "zumo-vpn-*.apk")), key=os.path.getmtime)[:-MANTENER_APK]
    for v in viejos:
        os.remove(v)
    with open(destino, "rb") as fh:
        datos = fh.read()
    return {"apk": datos, "codigo": codigo, "commit": commit_info(), "sync": sync}
