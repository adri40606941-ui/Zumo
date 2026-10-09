"""Respaldo cifrado de la configuración del bot y de la clave de firma de la app.

Qué guarda (todo lo que solo existe en esta VPS y no se puede volver a bajar de ningún lado):
  /etc/zumo/firma/        clave de firma de la app (zumo.jks + pass)
  /etc/zumo/bot.env       token del bot, admins, token de GitHub
  /etc/zumo/bot.json, app-servidores.json, base.url, centro.env, claves.db, usuarios.db, ...
No guarda los respaldos viejos (respaldos/) ni nada que se regenere solo.

Formato: tar.gz cifrado con "openssl enc -aes-256-cbc -pbkdf2 -iter 200000" (el mismo que usa la
exportación de la clave de firma), contraseña elegida por vos. Sin la contraseña no se abre.
"""
import io
import os
import subprocess
import tarfile
import tempfile
import time

DIR = os.environ.get("ZUMO_DIR", "/etc/zumo")
FIRMA = os.environ.get("ZUMO_FIRMA", os.path.join(DIR, "firma"))
EXCLUIR = {"respaldos", "lock", "limit.lock"}
MAX_BYTES = 40 * 1024 * 1024        # tope de Telegram para bots: 50 MB; dejamos margen
MARCA = ".zumo-centro"              # archivo de control dentro del tar


class ErrorRespaldo(Exception):
    pass


def _openssl(modo, datos, clave):
    r = subprocess.run(["openssl", "enc", modo, "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                        "-pass", "env:ZUMO_RESP"],
                       input=datos, capture_output=True, env={**os.environ, "ZUMO_RESP": clave})
    return r.returncode, r.stdout


def crear(clave, dir_=None):
    """Devuelve los bytes del respaldo cifrado."""
    dir_ = dir_ or DIR
    if len(clave) < 8:
        raise ErrorRespaldo("La contraseña del respaldo tiene que tener al menos 8 caracteres.")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        info = tarfile.TarInfo(MARCA)
        marca = f"zumo-centro 1 {int(time.time())}\n".encode()
        info.size = len(marca)
        t.addfile(info, io.BytesIO(marca))
        for nombre in sorted(os.listdir(dir_)):
            if nombre in EXCLUIR or nombre.endswith((".tmp", ".lock")):
                continue
            t.add(os.path.join(dir_, nombre), arcname=nombre)
    rc, salida = _openssl("-e", buf.getvalue(), clave)
    if rc != 0 or not salida:
        raise ErrorRespaldo("openssl no pudo cifrar el respaldo.")
    if len(salida) > MAX_BYTES:
        raise ErrorRespaldo("El respaldo es demasiado grande para mandarlo por Telegram.")
    return salida


REPO = os.environ.get("ZUMO_REPO_LOCAL", "/opt/zumo-repo")
PARTE = 40 * 1024 * 1024            # tamaño máximo de cada archivo que se manda por Telegram


def paquete_repo(clave, repo=None):
    """Copia del repo (con .git) en tar.gz cifrado, partida en pedazos de PARTE bytes.
    Devuelve la lista de bytes de cada parte. Se vuelve a armar con `cat parte* | openssl ... | tar xz`."""
    repo = repo or REPO
    if len(clave) < 8:
        raise ErrorRespaldo("La contraseña del respaldo tiene que tener al menos 8 caracteres.")
    if not os.path.isdir(os.path.join(repo, "bot")):
        raise ErrorRespaldo("No hay copia del repo en esta VPS (" + repo + "). Repetí el instalador del bot con el dominio puesto.")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        t.add(repo, arcname=os.path.basename(repo.rstrip("/")))
    rc, salida = _openssl("-e", buf.getvalue(), clave)
    if rc != 0 or not salida:
        raise ErrorRespaldo("openssl no pudo cifrar el paquete.")
    return [salida[i:i + PARTE] for i in range(0, len(salida), PARTE)]


def _miembros_seguros(t):
    for m in t.getmembers():
        n = m.name
        if n.startswith("/") or ".." in n.split("/"):
            raise ErrorRespaldo("El respaldo trae rutas inválidas.")
        if not (m.isfile() or m.isdir()):
            continue            # sin enlaces ni dispositivos
        yield m


def restaurar(cifrado, clave, dir_=None):
    """Descifra y vuelca el contenido en /etc/zumo. Devuelve la lista de archivos restaurados."""
    dir_ = dir_ or DIR
    rc, claro = _openssl("-d", cifrado, clave)
    if rc != 0 or not claro:
        raise ErrorRespaldo("Contraseña incorrecta o archivo dañado.")
    try:
        t = tarfile.open(fileobj=io.BytesIO(claro), mode="r:gz")
    except tarfile.TarError:
        raise ErrorRespaldo("Contraseña incorrecta o archivo dañado.") from None
    with t:
        if MARCA not in t.getnames():
            raise ErrorRespaldo("Ese archivo no es un respaldo de Zumo.")
        miembros = [m for m in _miembros_seguros(t) if m.name != MARCA]
        hechos = []
        with tempfile.TemporaryDirectory() as tmp:
            t.extractall(tmp, members=miembros)          # primero a un lado, por si falla a mitad
            for raiz, _, archivos in os.walk(tmp):
                for a in archivos:
                    origen = os.path.join(raiz, a)
                    rel = os.path.relpath(origen, tmp)
                    destino = os.path.join(dir_, rel)
                    os.makedirs(os.path.dirname(destino), exist_ok=True)
                    modo = os.stat(origen).st_mode & 0o777
                    with open(origen, "rb") as f:
                        datos = f.read()
                    fd = os.open(destino + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, modo)
                    with os.fdopen(fd, "wb") as g:
                        g.write(datos)
                    os.replace(destino + ".tmp", destino)
                    hechos.append(rel)
    return sorted(hechos)


# ------------------------------------------------------------------ clave de firma local
def clave_firma():
    """(ruta_jks, contraseña) de la clave de firma local, o None si todavía no hay."""
    jks = os.path.join(FIRMA, "zumo.jks")
    pw = os.path.join(FIRMA, "pass")
    if os.path.isfile(jks) and os.path.isfile(pw):
        with open(pw, encoding="utf-8") as f:
            p = f.read().strip()
        if p:
            return jks, p
    return None


def guardar_clave_firma(jks_bytes, contrasena):
    os.makedirs(FIRMA, mode=0o700, exist_ok=True)
    os.chmod(FIRMA, 0o700)
    for nombre, datos in (("zumo.jks", jks_bytes), ("pass", (contrasena + "\n").encode())):
        ruta = os.path.join(FIRMA, nombre)
        fd = os.open(ruta + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(datos)
        os.replace(ruta + ".tmp", ruta)


def crear_clave_firma():
    """Crea una clave nueva si no hay (solo para instalaciones nuevas sin app publicada)."""
    import secrets
    if clave_firma():
        return False
    p = secrets.token_hex(16)
    with tempfile.TemporaryDirectory() as d:
        ruta = os.path.join(d, "zumo.jks")
        r = subprocess.run(["keytool", "-genkeypair", "-keystore", ruta, "-storepass", p, "-keypass", p,
                            "-alias", "zumo", "-keyalg", "RSA", "-keysize", "2048", "-validity", "36500",
                            "-dname", "CN=Zumo VPN"], capture_output=True, text=True)
        if r.returncode != 0:
            raise ErrorRespaldo("keytool no pudo crear la clave: " + r.stderr.strip()[-200:])
        guardar_clave_firma(open(ruta, "rb").read(), p)
    return True


def exportar_clave(contrasena):
    """clave-firma.enc: mismo formato que usa el bot al sacar la clave de GitHub."""
    c = clave_firma()
    if not c:
        raise ErrorRespaldo("Todavía no hay clave de firma en esta VPS.")
    if len(contrasena) < 8:
        raise ErrorRespaldo("La contraseña tiene que tener al menos 8 caracteres.")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for nombre, datos in (("zumo.jks", open(c[0], "rb").read()), ("pass", (c[1] + "\n").encode())):
            i = tarfile.TarInfo(nombre)
            i.size = len(datos)
            t.addfile(i, io.BytesIO(datos))
    rc, salida = _openssl("-e", buf.getvalue(), contrasena)
    if rc != 0 or not salida:
        raise ErrorRespaldo("openssl no pudo cifrar la clave.")
    return salida


def huella_clave():
    """SHA-256 (primeros 16 hex) del certificado de la clave, para comparar entre VPS."""
    c = clave_firma()
    if not c:
        return ""
    r = subprocess.run(["keytool", "-list", "-keystore", c[0], "-storepass", c[1], "-alias", "zumo"],
                       capture_output=True, text=True)
    for l in r.stdout.splitlines():
        if "(SHA" in l or "SHA-256" in l or "SHA256" in l:
            return l.split(":", 1)[-1].strip()[:23]
    return ""
