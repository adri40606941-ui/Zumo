"""Servidor web mínimo del bot, para dos cosas:

1. Repartir lo que bajan tus clientes: la lista de servidores de la app (/servidores.bin, cifrada) y el APK
   (/zumo-vpn.apk). Son públicos, sin código.
2. Instalar el panel (o el bot) en una VPS nueva desde tu dominio, sin GitHub y con un código de un solo uso
   que da el bot (ver accesos.py): `/i` es el cargador, `/canje` cambia el código por un pase y
   `/s/<pase>/<archivo>` sirve los archivos del instalador, de la copia del repo en /opt/zumo-repo.

3. Panel web de revendedores en /r (ver panel_web.py), solo si el bot lo activa.

Todo sale de una lista fija de rutas; lo demás da 404 (no se listan carpetas ni se sigue ningún enlace que
salga de la copia). Escucha en el puerto 80 (Cloudflare "Flexible") y, si hay certificado en /etc/zumo/web/,
también en el 443 (Cloudflare "Full"). Si un puerto no se puede abrir el bot sigue andando."""
import os
import re
import shutil
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIR = "/opt/zumo-bot/publico"
CERT = "/etc/zumo/web/cert.pem"
CLAVE = "/etc/zumo/web/key.pem"
# Públicos (sin código): la lista de la app y el APK, que bajan tus clientes.
PERMITIDOS = {"/servidores.bin": ("servidores.bin", "application/octet-stream"),
              "/zumo-vpn.apk": ("zumo-vpn.apk", "application/vnd.android.package-archive")}
ARCHIVO = "servidores.bin"
REPO = "/opt/zumo-repo"
# Archivos del repo que bajan los instaladores (install.sh, actualizar.sh, panel.sh…). Nada más.
REPO_EXACTOS = {"install.sh", "actualizar.sh", "actualizar-panel.sh", "panel.sh", "zumo-lib.sh",
                "fuentes/zumo-limit.c", "fuentes/pdirect2.c", "fuentes/badvpn.tar.gz", "config/limit.conf"}
REPO_PATRONES = (
    re.compile(r"^binarios/[A-Za-z0-9._-]+$"),
    re.compile(r"^scripts/[A-Za-z0-9._-]+\.sh$"),
    re.compile(r"^bot/(?!test_)[A-Za-z0-9._-]+\.(py|sh)$"),
)


def ruta_repo(ruta, repo=None):
    """Ruta absoluta del archivo del repo que corresponde a la URL, o None si no está permitido."""
    rel = ruta.lstrip("/")
    if rel not in REPO_EXACTOS and not any(p.match(rel) for p in REPO_PATRONES):
        return None
    base = os.path.realpath(repo or REPO)
    real = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([base, real]) != base or not os.path.isfile(real):
        return None
    return real


def publicar(datos, directorio=None, nombre=ARCHIVO):
    """Guarda un archivo público (bytes) de una vez: o queda entero o queda el anterior, nunca a medias."""
    d = directorio or DIR
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".publicando.tmp")
    with open(tmp, "wb") as f:
        f.write(datos)
    os.replace(tmp, os.path.join(d, nombre))
    return os.path.join(d, nombre)


def hay_lista(directorio=None):
    return os.path.isfile(os.path.join(directorio or DIR, ARCHIVO))


CARGADOR = r"""#!/bin/bash
# Instalador Zumo: pide el código de un solo uso que te da el bot.
D="%(dominio)s"
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
C="${ZUMO_CODIGO:-}"
[ -n "$C" ] || read -rp "Código de instalación: " C </dev/tty
R=$(curl -fsS --get --data-urlencode "c=$C" "https://$D/canje") || { echo "✘ Código inválido o vencido."; exit 1; }
P=$(printf '%%s\n' "$R" | sed -n 1p); K=$(printf '%%s\n' "$R" | sed -n 2p)
case "$P" in ""|*[!0-9a-f]*) echo "✘ Respuesta inesperada."; exit 1;; esac
[ "${#P}" -eq 32 ] || { echo "✘ Respuesta inesperada."; exit 1; }
case "$K" in panel) S=install.sh;; bot) S=bot/instalar-bot.sh;; actualizar) S=actualizar.sh;; *) echo "✘ Respuesta inesperada."; exit 1;; esac
export ZUMO_BASE="https://$D/s/$P"
curl -fsSL "$ZUMO_BASE/$S" | bash
"""


TOKEN_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
MAX_CUENTA_POR_MIN = 30          # consultas de /cuenta por IP y por minuto
MAX_POST = 8192                  # tamaño máximo de un formulario del panel de revendedores (/r)


def _handler(directorio, repo=None, accesos=None, dominio="", cuenta=None, reloj=time.time, web=None):
    pedidos = {}                 # ip -> [momentos] de las consultas de /cuenta del último minuto

    def _frenado(ip):
        ahora = reloj()
        v = [t for t in pedidos.get(ip, []) if ahora - t < 60]
        v.append(ahora)
        pedidos[ip] = v
        if len(pedidos) > 5000:
            pedidos.clear()
        return len(v) > MAX_CUENTA_POR_MIN

    class H(BaseHTTPRequestHandler):
        server_version = "zumo"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _texto(self, codigo, cuerpo=b"", tipo="text/plain; charset=utf-8", con_cuerpo=True):
            self.send_response(codigo)
            if cuerpo:
                self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if cuerpo and con_cuerpo:
                self.wfile.write(cuerpo)

        def _ip(self):
            return self.headers.get("CF-Connecting-IP") or self.client_address[0]

        def _https(self):
            """¿El visitante llegó por https? (directo con TLS, o por Cloudflare que avisa con cabeceras)."""
            return (isinstance(self.connection, ssl.SSLSocket)
                    or self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                    or '"https"' in self.headers.get("CF-Visitor", ""))

        def _web(self, ruta, cuerpo=b"", consulta=""):
            """Panel web de revendedores (/r): lo resuelve panel_web.PanelWeb."""
            codigo, cab, datos = web.manejar(self.command, ruta, dict(self.headers.items()), cuerpo, self._ip(), self._https(), consulta)
            self.send_response(codigo)
            for k, v in cab.items():
                self.send_header(k, v)
            self.end_headers()
            if datos and self.command != "HEAD":
                self.wfile.write(datos)

        def _responder(self, con_cuerpo):
            ruta, _, consulta = self.path.partition("?")
            if web and (ruta == "/r" or ruta.startswith("/r/")):
                return self._web(ruta, b"", consulta)
            archivo, tipo, adjunto = None, "application/octet-stream", ""
            if ruta in PERMITIDOS:
                nombre, tipo = PERMITIDOS[ruta]
                archivo = os.path.join(directorio, nombre)
                if nombre.endswith(".apk"):
                    adjunto = f'attachment; filename="{nombre}"'
            elif ruta == "/cuenta" and cuenta:
                # La app, ya conectada, pregunta con su token cómo se llama el cliente y cuándo vence.
                if _frenado(self._ip()):
                    return self._texto(429)
                t = urllib.parse.parse_qs(consulta).get("t", [""])[0]
                datos = cuenta(t) if TOKEN_RE.match(t) else None
                if not datos:
                    return self._texto(404)
                nombre, vence = datos
                nombre = re.sub(r"[\r\n]+", " ", nombre or "").strip()
                return self._texto(200, f"{nombre}\n{vence or ''}\n".encode("utf-8"), con_cuerpo=con_cuerpo)
            elif ruta == "/i" and dominio and accesos:
                return self._texto(200, (CARGADOR % {"dominio": dominio}).encode(), con_cuerpo=con_cuerpo)
            elif ruta == "/canje" and accesos:
                q = urllib.parse.parse_qs(consulta).get("c", [""])[0]
                pase, kind = accesos.canjear(q, self._ip())
                if not pase:
                    return self._texto(403)
                return self._texto(200, f"{pase}\n{kind}\n".encode(), con_cuerpo=con_cuerpo)
            elif ruta.startswith("/s/") and accesos:
                pase, _, resto = ruta[3:].partition("/")
                if accesos.valido(pase):
                    archivo = ruta_repo(resto, repo)
                    if archivo and archivo.endswith((".sh", ".py", ".c", ".conf")):
                        tipo = "text/plain; charset=utf-8"
            if archivo is None or not os.path.isfile(archivo):
                return self._texto(404)
            try:
                f = open(archivo, "rb")
            except OSError:
                return self._texto(404)
            with f:
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
                self.send_header("Cache-Control", "no-cache")
                if adjunto:
                    self.send_header("Content-Disposition", adjunto)
                self.end_headers()
                if con_cuerpo:
                    shutil.copyfileobj(f, self.wfile)

        def do_GET(self):
            self._responder(True)

        def do_HEAD(self):
            self._responder(False)

        def _no(self):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            ruta = self.path.partition("?")[0]
            if not (web and (ruta == "/r" or ruta.startswith("/r/"))):
                return self._no()
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if not 0 <= n <= MAX_POST:
                self.send_response(413)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            self._web(ruta, self.rfile.read(n))
        do_PUT = do_DELETE = do_PATCH = _no
    return H


def servir(puerto, directorio=None, cert=None, clave=None, host="0.0.0.0", repo=None, accesos=None, dominio="", cuenta=None, web=None):
    """Abre un puerto en un hilo. Devuelve el servidor, o None si no se pudo abrir (nunca lanza)."""
    try:
        s = ThreadingHTTPServer((host, puerto), _handler(directorio or DIR, repo, accesos, dominio, cuenta, web=web))
        s.daemon_threads = True
        if cert:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, clave)
            s.socket = ctx.wrap_socket(s.socket, server_side=True)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return s
    except Exception as e:
        print(f"zumo-bot: no pude abrir el puerto {puerto} para la lista de servidores: {e}", flush=True)
        return None


def iniciar(directorio=None, http=80, https=443, accesos=None, dominio="", cuenta=None, web=None):
    """Arranca los puertos que se puedan. Devuelve la lista de servidores abiertos."""
    extra = dict(accesos=accesos, dominio=dominio, cuenta=cuenta, web=web)
    abiertos = [servir(http, directorio, **extra)]
    if os.path.isfile(CERT) and os.path.isfile(CLAVE):
        abiertos.append(servir(https, directorio, CERT, CLAVE, **extra))
    return [s for s in abiertos if s]
