"""Servidor web mínimo del bot: reparte la lista de servidores de la app (servidores.bin) desde la VPS.

Solo sirve archivos de una lista fija (hoy, /servidores.bin); cualquier otra ruta da 404. El archivo ya
va cifrado (ZL1/AES-GCM), así que no hay nada legible. Escucha en el puerto 80 (Cloudflare "Flexible") y,
si existe un certificado en /etc/zumo/web/, también en el 443 (Cloudflare "Full"). Si un puerto no se
puede abrir (ocupado, sin permiso) el bot sigue andando: la app cae a GitHub."""
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIR = "/opt/zumo-bot/publico"
CERT = "/etc/zumo/web/cert.pem"
CLAVE = "/etc/zumo/web/key.pem"
PERMITIDOS = {"/servidores.bin": "application/octet-stream"}
ARCHIVO = "servidores.bin"


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


def _handler(directorio):
    class H(BaseHTTPRequestHandler):
        server_version = "zumo"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _responder(self, con_cuerpo):
            ruta = self.path.split("?", 1)[0]
            tipo = PERMITIDOS.get(ruta)
            archivo = os.path.join(directorio, ARCHIVO)
            if tipo is None or not os.path.isfile(archivo):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            with open(archivo, "rb") as f:
                datos = f.read()
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(datos)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if con_cuerpo:
                self.wfile.write(datos)

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


def servir(puerto, directorio=None, cert=None, clave=None, host="0.0.0.0"):
    """Abre un puerto en un hilo. Devuelve el servidor, o None si no se pudo abrir (nunca lanza)."""
    try:
        s = ThreadingHTTPServer((host, puerto), _handler(directorio or DIR))
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
