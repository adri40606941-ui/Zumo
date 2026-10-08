"""Servidor web mínimo del bot: reparte desde la VPS la lista de servidores de la app (servidores.bin) y los
instaladores del panel (los mismos archivos del repo, de la copia /opt/zumo-repo), para instalar con
`ZUMO_BASE=https://dominio` sin pasar por GitHub.

Solo sirve rutas de una lista fija: /servidores.bin y, de la copia del repo, los scripts del panel, sus
fuentes/configuración, binarios/, scripts/ y bot/. Cualquier otra ruta da 404 (no se listan carpetas, no se
sigue ningún enlace que salga de la copia). La lista va cifrada (ZL1/AES-GCM), así que no hay nada legible. Escucha en el puerto 80 (Cloudflare "Flexible") y,
si existe un certificado en /etc/zumo/web/, también en el 443 (Cloudflare "Full"). Si un puerto no se
puede abrir (ocupado, sin permiso) el bot sigue andando: la app cae a GitHub."""
import os
import re
import shutil
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIR = "/opt/zumo-bot/publico"
CERT = "/etc/zumo/web/cert.pem"
CLAVE = "/etc/zumo/web/key.pem"
PERMITIDOS = {"/servidores.bin": "application/octet-stream"}
ARCHIVO = "servidores.bin"
REPO = "/opt/zumo-repo"
# Archivos del repo que bajan los instaladores (install.sh, actualizar.sh, panel.sh…). Nada más.
REPO_EXACTOS = {"install.sh", "actualizar.sh", "actualizar-panel.sh", "panel.sh", "zumo-lib.sh",
                "fuentes/zumo-limit.c", "config/limit.conf"}
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


def publicar(datos, directorio=None):
    """Guarda la lista (bytes) de una vez: o queda entera o queda la anterior, nunca a medias."""
    d = directorio or DIR
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".servidores.tmp")
    with open(tmp, "wb") as f:
        f.write(datos)
    os.replace(tmp, os.path.join(d, ARCHIVO))
    return os.path.join(d, ARCHIVO)


def hay_lista(directorio=None):
    return os.path.isfile(os.path.join(directorio or DIR, ARCHIVO))


def _handler(directorio, repo=None):
    class H(BaseHTTPRequestHandler):
        server_version = "zumo"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _responder(self, con_cuerpo):
            ruta = self.path.split("?", 1)[0]
            archivo = None
            if ruta in PERMITIDOS:
                archivo = os.path.join(directorio, ARCHIVO)
                tipo = PERMITIDOS[ruta]
                if not os.path.isfile(archivo):
                    archivo = None
            else:
                archivo = ruta_repo(ruta, repo)
                tipo = "text/plain; charset=utf-8" if archivo and archivo.endswith((".sh", ".py", ".c", ".conf")) else "application/octet-stream"
            if archivo is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                f = open(archivo, "rb")
            except OSError:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            with f:
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
                self.send_header("Cache-Control", "no-cache")
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
        do_POST = do_PUT = do_DELETE = do_PATCH = _no
    return H


def servir(puerto, directorio=None, cert=None, clave=None, host="0.0.0.0", repo=None):
    """Abre un puerto en un hilo. Devuelve el servidor, o None si no se pudo abrir (nunca lanza)."""
    try:
        s = ThreadingHTTPServer((host, puerto), _handler(directorio or DIR, repo))
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


def iniciar(directorio=None, http=80, https=443):
    """Arranca los puertos que se puedan. Devuelve la lista de servidores abiertos."""
    abiertos = [servir(http, directorio)]
    if os.path.isfile(CERT) and os.path.isfile(CLAVE):
        abiertos.append(servir(https, directorio, CERT, CLAVE))
    return [s for s in abiertos if s]
