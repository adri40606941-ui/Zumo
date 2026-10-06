"""Compilar la app Android en esta misma VPS (sin GitHub Actions).

Usa el código en /opt/zumo-src (clon del repo), la clave de firma de /etc/zumo/firma y Gradle.
Lo mismo que hace .github/workflows/android.yml, pero sin límite de minutos.
"""
import glob
import os
import re
import subprocess
import threading
import time

import respaldo

SRC = os.environ.get("ZUMO_SRC", "/opt/zumo-src")
CONTADOR = os.environ.get("ZUMO_BUILD_N", "/etc/zumo/build.n")
ANDROID_HOME = os.environ.get("ANDROID_HOME", "/opt/android-sdk")
GRADLE = os.environ.get("ZUMO_GRADLE", "/opt/gradle/bin/gradle")
MAX_SEG = 60 * 60


class ErrorLocal(Exception):
    pass


def _siguiente():
    try:
        n = int(open(CONTADOR).read().strip())
    except (OSError, ValueError):
        n = 0
    n += 1
    tmp = CONTADOR + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(n))
    os.replace(tmp, CONTADOR)
    return n


def _git(*args, timeout=180):
    return subprocess.run(["git", "-C", SRC, *args], capture_output=True, text=True, timeout=timeout)


def actualizar_codigo():
    """git pull del repo. Si no hay red o no es un repo, se compila con lo que haya."""
    if not os.path.isdir(os.path.join(SRC, ".git")):
        return "sin repo git: se compila con los archivos que hay"
    _git("checkout", "--", "android/servidores.txt")
    r = _git("pull", "--ff-only", "--recurse-submodules")
    if r.returncode != 0:
        return "no se pudo actualizar desde el origen (" + (r.stderr.strip().splitlines() or ["?"])[-1][:120] + "); se compila lo que hay"
    return ""


def contar_tareas(env, timeout=240):
    """Cuántas tareas va a correr Gradle (simulacro, tarda unos segundos). 0 si no se pudo saber."""
    try:
        r = subprocess.run([GRADLE, "-p", os.path.join(SRC, "android"), "assembleRelease", "--dry-run",
                            "--no-daemon", "--console=plain"], capture_output=True, text=True,
                           env=env, timeout=timeout, errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return 0
    if r.returncode != 0:
        return 0
    return len(re.findall(r"^:\S+", r.stdout, re.M))


def compilar(texto_servidores, progreso=None, actualizar=True):
    """Devuelve dict: ok, numero, apk (bytes), log (últimas líneas), aviso."""
    c = respaldo.clave_firma()
    if not c:
        raise ErrorLocal("Falta la clave de firma. Importala (💾 Respaldo → Importar clave) o creala con "
                         "la opción de clave nueva.")
    if not os.path.isdir(os.path.join(SRC, "android")):
        raise ErrorLocal(f"No encuentro el código de la app en {SRC}.")
    aviso = actualizar_codigo() if actualizar else ""
    srv = os.path.join(SRC, "android", "servidores.txt")
    if texto_servidores:
        with open(srv, "w", encoding="utf-8") as f:
            f.write(texto_servidores.rstrip("\n") + "\n")
    n = _siguiente()
    env = {**os.environ, "ANDROID_HOME": ANDROID_HOME, "ANDROID_SDK_ROOT": ANDROID_HOME,
           "ZUMO_KEYSTORE": c[0], "ZUMO_KS_PASS": c[1], "ZUMO_VERSION_CODE": str(n),
           "GRADLE_OPTS": os.environ.get("GRADLE_OPTS", "-Xmx2g -Dorg.gradle.daemon=false")}
    for viejo in glob.glob(os.path.join(SRC, "android/app/build/outputs/apk/release/*.apk")):
        os.remove(viejo)
    log = []
    t0 = time.time()
    if progreso:
        progreso(0, "Preparando…", 0, 0)
    total = contar_tareas(env) if progreso else 0
    p = subprocess.Popen([GRADLE, "-p", os.path.join(SRC, "android"), "assembleRelease", "--no-daemon",
                          "--console=plain"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, env=env, errors="replace")
    ultimo = {"tarea": "", "hechas": 0}

    def leer():
        for linea in p.stdout:
            log.append(linea.rstrip())
            m = re.match(r"> Task (\S+)", linea)
            if m:
                ultimo["tarea"] = m.group(1)
                ultimo["hechas"] += 1
    h = threading.Thread(target=leer, daemon=True)
    h.start()
    try:
        while p.poll() is None:
            if time.time() - t0 > MAX_SEG:
                p.kill()
                raise ErrorLocal("La compilación tardó más de 60 minutos y se cortó.")
            if progreso:
                progreso(int((time.time() - t0) // 60), ultimo["tarea"], min(ultimo["hechas"], total), total)
            time.sleep(6 if progreso else 1)
    finally:
        h.join(timeout=5)
        if texto_servidores:
            _git("checkout", "--", "android/servidores.txt")     # que la lista no quede en el repo
    cola = "\n".join([l for l in log if re.search(r"^e: |FAILED|BUILD|error:|Exception", l)][-25:] or log[-25:])
    apks = glob.glob(os.path.join(SRC, "android/app/build/outputs/apk/release/*.apk"))
    if p.returncode == 0 and apks:
        r16 = subprocess.run(["python3", "-I", os.path.join(SRC, "android", "comprobar-16kb.py"), apks[0]],
                             capture_output=True, text=True)
        if r16.returncode != 0 and os.path.isfile(os.path.join(SRC, "android", "comprobar-16kb.py")):
            aviso = (aviso + " " if aviso else "") + "La app no quedó lista para celulares con páginas de 16 KB: " + \
                    (r16.stdout.strip().splitlines() or ["?"])[0][:160]
        with open(apks[0], "rb") as f:
            return {"ok": True, "numero": n, "apk": f.read(), "log": cola, "aviso": aviso}
    return {"ok": False, "numero": n, "apk": b"", "log": cola, "aviso": aviso}
