"""Códigos de un solo uso para instalar el panel en una VPS nueva (solo en la VPS "centro").

El bot genera un código aleatorio; el comando que ve el admin es
    bash <(curl -fsSL https://dominio/i/<código>/install.sh)
El código sirve UNA vez (la primera descarga lo gasta) y vence a los 15 minutos. Nginx manda /i/ a
un servidorcito local (127.0.0.1) que corre dentro del bot y entrega el install.sh publicado.
El resto de la instalación sigue usando la dirección con el código secreto del centro, como antes.
"""
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CODIGOS = os.environ.get("ZUMO_CODIGOS", "/etc/zumo/codigos.json")
CENTRO_ENV = os.environ.get("ZUMO_CENTRO_ENV", "/etc/zumo/centro.env")
BASE_URL = os.environ.get("ZUMO_BASE_URL", "/etc/zumo/base.url")
PUBLICADO = os.environ.get("ZUMO_PUBLICADO", "/var/www/zumo")
PUERTO = int(os.environ.get("ZUMO_INSTALAR_PUERTO", "7391"))
VIDA = 15 * 60
RUTA = re.compile(r"^/i/([0-9a-f]{20})/install\.sh$")


class Codigos:
    """Lista de códigos pendientes en un archivo 0600: {código: vence (epoch)}."""

    def __init__(self, ruta=None, vida=VIDA, ahora=time.time):
        self.ruta, self.vida, self.ahora = ruta or CODIGOS, vida, ahora
        self.cerrojo = threading.Lock()

    def _leer(self):
        try:
            with open(self.ruta, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _guardar(self, d):
        tmp = self.ruta + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.ruta)

    def crear(self):
        with self.cerrojo:
            d = {k: v for k, v in self._leer().items() if v > self.ahora()}
            c = secrets.token_hex(10)
            d[c] = self.ahora() + self.vida
            self._guardar(d)
            return c

    def usar(self, codigo):
        """True si el código existe y no venció; lo gasta (no sirve de nuevo)."""
        with self.cerrojo:
            d = self._leer()
            vence = d.pop(codigo, None)
            self._guardar({k: v for k, v in d.items() if v > self.ahora()})
            return vence is not None and vence > self.ahora()


def _secreto():
    try:
        with open(CENTRO_ENV, encoding="utf-8") as f:
            for l in f:
                if l.startswith("SECRETO="):
                    return l.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def base_publica():
    """https://dominio/<secreto> de este centro, o "" si todavía no hay dominio con HTTPS."""
    try:
        with open(BASE_URL, encoding="utf-8") as f:
            return f.read().strip().rstrip("/")
    except OSError:
        return ""


def comando(codigo):
    b = base_publica()
    if not b:
        return ""
    dominio = b.rsplit("/", 1)[0]          # sin el código secreto del centro
    return f"bash <(curl -fsSL {dominio}/i/{codigo}/install.sh)"


def hacer_servidor(codigos, al_usar=None, puerto=PUERTO):
    class H(BaseHTTPRequestHandler):
        server_version = "zumo"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _no(self):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            m = RUTA.match(self.path.split("?")[0])
            sec = _secreto()
            if not m or not sec or not codigos.usar(m.group(1)):
                return self._no()
            try:
                with open(os.path.join(PUBLICADO, sec, "install.sh"), "rb") as f:
                    cuerpo = f.read()
            except OSError:
                return self._no()
            self.send_response(200)
            self.send_header("Content-Type", "text/x-shellscript; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)
            if al_usar:
                ip = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Real-IP") or self.client_address[0]
                try:
                    al_usar(ip)
                except Exception:
                    pass

        do_HEAD = _no       # un HEAD no gasta el código

    return ThreadingHTTPServer(("127.0.0.1", puerto), H)


class InstalacionMixin:
    codigos = None

    def boton_instalar(self, chat, mid, acc):
        """Devuelve True si manejó el botón."""
        if acc != "ivps":
            return False
        if not base_publica():
            self.mostrar(chat, mid, "🖥 Instalar VPS nueva\n\nEsta VPS todavía no tiene dominio con HTTPS. "
                                    "Corré de nuevo instalar-centro.sh cuando el dominio ya apunte acá.", [[("◂ Menú", "menu")]])
            return True
        if self.codigos is None:
            self.codigos = Codigos()
        c = self.codigos.crear()
        self.tg.mensaje(chat, "🖥 Instalar el panel en una VPS nueva (Ubuntu 22.04 o Debian 12, como root):\n\n```\n" + comando(c) + "\n```\n"
                              "Instala solo el panel (usuarios, protocolos y limitador). No instala el bot ni la app.\n"
                              "⏱ Sirve una sola vez y vence en 15 minutos. Si no lo usás, tocá el botón de nuevo para otro.",
                        [[("🖥 Otro código", "ivps")], [("◂ Menú", "menu")]], md=True)
        return True

    def aviso_codigo_usado(self, ip):
        for a in sorted(self.admins):
            self.tg.mensaje(a, f"✅ Se usó un código de instalación desde {ip}. Ya no sirve.")
