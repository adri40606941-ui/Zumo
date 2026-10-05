#!/usr/bin/env python3
"""Bot de Telegram para administrar cuentas de Zumo VPN desde el teléfono.

Corre en la VPS como root (crea usuarios SSH reales, igual que el panel) y le manda al
administrador el archivo .zs que abre la app. Solo responde a los IDs de ADMINS.

Configuración: /etc/zumo/bot.env  (BOT_TOKEN, ADMINS, ZS_SECRET)
Datos del servidor (host, puerto, payload...): /etc/zumo/bot.json (se cambian desde el bot)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zs  # noqa: E402

ENV = os.environ.get("ZUMO_BOT_ENV", "/etc/zumo/bot.env")
ESTADO = os.environ.get("ZUMO_BOT_JSON", "/etc/zumo/bot.json")
DB = os.environ.get("ZUMO_DB", "/etc/zumo/usuarios.db")
CLAVES = os.environ.get("ZUMO_CLAVES", "/etc/zumo/claves.db")
LIB = os.environ.get("ZUMO_LIB", "/etc/zumo/zumo-lib.sh")
LIMCONF = os.environ.get("ZUMO_LIMCONF", "/etc/zumo/limit.conf")

AYUDA = """🛡 *Zumo VPN – bot*

*Servidor (lo que lleva el archivo)*
/servidor `host` `[puerto]` `[nombre]` – dominio/IP, puerto (80) y nombre del .zs
/payload `texto` – payload (puede ir en varias líneas; `/payload borrar` lo quita)
/tls `on|off` `[sni]` – TLS (puerto 443)
/ver – muestra lo configurado

*Cuentas*
/crear `usuario` `clave` `días` `[límite]` – crea el usuario y manda el .zs
/exportar `usuario` – vuelve a mandar su .zs
/renovar `usuario` `días` – vence dentro de N días desde hoy y manda el .zs nuevo
/clave `usuario` `nueva` – cambia la contraseña y manda el .zs
/limite `usuario` `n` – cambia el límite de conexiones
/borrar `usuario` – elimina la cuenta
/usuarios – lista con vencimientos

/id – tu ID de Telegram"""


# ---------------------------------------------------------------- configuración
def leer_env(ruta=ENV):
    d = {}
    try:
        for linea in open(ruta, encoding="utf-8"):
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return d


def cargar_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def guardar_estado(d):
    tmp = ESTADO + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


# --------------------------------------------------------------------- usuarios
NOMBRE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,9}$")
CLAVE_RE = re.compile(r"^[A-Za-z0-9]{1,10}$")


def bash_lib(*args):
    """Llama a una función de zumo-lib.sh (toma el lock del usuarios.db)."""
    return subprocess.run(["bash", "-c", 'source "$1"; shift; "$@"', "_", LIB, *args],
                          capture_output=True, text=True)


def run(*cmd, entrada=None):
    return subprocess.run(list(cmd), input=entrada, capture_output=True, text=True)


def usuarios():
    """{usuario: (límite, vence)} desde usuarios.db."""
    out = {}
    try:
        for linea in open(DB, encoding="utf-8"):
            p = linea.strip().split(":")
            if len(p) >= 3 and p[0]:
                out[p[0]] = (p[1], p[2])
    except FileNotFoundError:
        pass
    return out


def clave_de(u):
    try:
        for linea in open(CLAVES, encoding="utf-8"):
            if linea.startswith(u + ":"):
                return linea.rstrip("\n")[len(u) + 1:]
    except FileNotFoundError:
        pass
    return None


def clave_guardar(u, clave):
    clave_borrar(u)
    fd = os.open(CLAVES, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(f"{u}:{clave}\n")


def clave_borrar(u):
    try:
        lineas = [l for l in open(CLAVES, encoding="utf-8") if not l.startswith(u + ":")]
    except FileNotFoundError:
        return
    with open(CLAVES, "w", encoding="utf-8") as f:
        f.writelines(lineas)


def fecha_cuenta(exp):
    """La cuenta de Linux vence un día después: el corte exacto lo hace el limitador."""
    return (date.fromisoformat(exp) + timedelta(days=1)).isoformat()


def crear_usuario(u, clave, dias, limite):
    if not NOMBRE_RE.match(u):
        return "Usuario inválido: empieza con letra, solo letras y números, máx. 10."
    if not CLAVE_RE.match(clave):
        return "Contraseña inválida: solo letras y números, de 1 a 10."
    if run("id", u).returncode == 0:
        return "Ese usuario ya existe."
    exp = (date.today() + timedelta(days=dias)).isoformat()
    badname = [] if re.match(r"^[a-z][a-z0-9]*$", u) else ["--badname"]
    r = run("useradd", *badname, "-M", "-s", "/bin/false", "-e", fecha_cuenta(exp), u)
    if r.returncode != 0:
        return "No se pudo crear el usuario: " + (r.stderr.strip() or "error")
    run("chpasswd", entrada=f"{u}:{clave}\n")
    clave_guardar(u, clave)
    bash_lib("zumo_db_add", u, str(limite), exp)
    return None


def borrar_usuario(u):
    run("pkill", "-9", "-u", u)
    run("userdel", u)
    bash_lib("zumo_db_del", u)
    clave_borrar(u)
    for tabla, campo in (("/etc/zumo/datos.db", 0), ("/etc/zumo/datos-hist.db", 1)):
        try:
            l = [x for x in open(tabla, encoding="utf-8") if x.split(":")[campo] != u]
            open(tabla, "w", encoding="utf-8").writelines(l)
        except (FileNotFoundError, IndexError):
            pass


def renovar_usuario(u, dias):
    exp = (date.today() + timedelta(days=dias)).isoformat()
    run("usermod", "-e", fecha_cuenta(exp), u)
    bash_lib("zumo_db_set", u, "3", exp)
    try:  # contador de datos en 0, como el panel
        l = [x for x in open("/etc/zumo/datos.db", encoding="utf-8") if x.split(":")[0] != u]
        open("/etc/zumo/datos.db", "w", encoding="utf-8").writelines(l)
    except FileNotFoundError:
        pass
    return exp


# ------------------------------------------------------------------------- .zs
def armar_zs(u, secreto):
    """Devuelve (nombre_archivo, bytes) del .zs de un usuario existente, o (None, motivo)."""
    st = cargar_estado()
    if not st.get("host"):
        return None, "Falta el servidor: usá /servidor host [puerto] [nombre]."
    us = usuarios()
    if u not in us:
        return None, "Ese usuario no está en el panel."
    clave = clave_de(u)
    if clave is None:
        return None, "No tengo la contraseña de ese usuario guardada. Cambiala con /clave."
    p = zs.perfil(st["host"], st.get("port", 80), st.get("payload", ""), st.get("name", "Zumo"),
                  u, clave, us[u][1], st.get("tls", False), st.get("sni", ""))
    nombre = re.sub(r"[^A-Za-z0-9_-]", "_", st.get("name", "zumo")) or "zumo"
    return f"{nombre}.zs", zs.cifrar(p, secreto)


# --------------------------------------------------------------------- Telegram
class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"

    def _post(self, metodo, datos, timeout=40):
        req = urllib.request.Request(self.base + metodo, data=json.dumps(datos).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def actualizaciones(self, offset):
        return self._post("getUpdates", {"offset": offset, "timeout": 30,
                                         "allowed_updates": ["message"]}, timeout=45).get("result", [])

    def mensaje(self, chat, texto, md=True):
        d = {"chat_id": chat, "text": texto[:4000], "disable_web_page_preview": True}
        if md:
            d["parse_mode"] = "Markdown"
        try:
            self._post("sendMessage", d)
        except urllib.error.HTTPError:
            d.pop("parse_mode", None)       # si el Markdown falla, va como texto plano
            self._post("sendMessage", d)

    def documento(self, chat, nombre, datos, leyenda=""):
        borde = uuid.uuid4().hex
        cuerpo = b""
        for k, v in (("chat_id", str(chat)), ("caption", leyenda)):
            cuerpo += (f"--{borde}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        cuerpo += (f"--{borde}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{nombre}\"\r\n"
                   "Content-Type: application/octet-stream\r\n\r\n").encode() + datos + f"\r\n--{borde}--\r\n".encode()
        req = urllib.request.Request(self.base + "sendDocument", data=cuerpo,
                                     headers={"Content-Type": f"multipart/form-data; boundary={borde}"})
        urllib.request.urlopen(req, timeout=60).read()


# ---------------------------------------------------------------------- comandos
def esc(s):
    return re.sub(r"([_*`\[])", r"\\\1", s)


class Bot:
    def __init__(self, tg, admins, secreto):
        self.tg, self.admins, self.secreto = tg, admins, secreto

    def enviar_zs(self, chat, u, leyenda):
        nombre, datos = armar_zs(u, self.secreto)
        if nombre is None:
            self.tg.mensaje(chat, "⚠️ " + datos)
        else:
            self.tg.documento(chat, nombre, datos, leyenda)

    def manejar(self, msg):
        chat = msg["chat"]["id"]
        uid = msg.get("from", {}).get("id")
        texto = (msg.get("text") or "").strip()
        if not texto.startswith("/"):
            return
        partes = re.split(r"\s", texto, maxsplit=1)
        cmd, resto = partes[0], (partes[1] if len(partes) > 1 else "")
        cmd = cmd.split("@")[0].lower()
        if cmd == "/id":
            return self.tg.mensaje(chat, f"Tu ID de Telegram: `{uid}`")
        if uid not in self.admins:
            return self.tg.mensaje(chat, "No autorizado. Pasale tu ID (/id) al administrador.")
        try:
            self.comando(chat, cmd, resto.strip())
        except Exception as e:  # nunca tumbar el bot por un comando
            self.tg.mensaje(chat, f"⚠️ Error: {e}", md=False)

    def comando(self, chat, cmd, resto):
        st = cargar_estado()
        a = resto.split()
        if cmd in ("/start", "/ayuda", "/help"):
            return self.tg.mensaje(chat, AYUDA)
        if cmd == "/servidor":
            if not a:
                return self.tg.mensaje(chat, "Uso: /servidor `host` `[puerto]` `[nombre]`")
            st["host"] = a[0]
            if len(a) > 1:
                if not a[1].isdigit() or not 1 <= int(a[1]) <= 65535:
                    return self.tg.mensaje(chat, "Puerto inválido.")
                st["port"] = int(a[1])
            st.setdefault("port", 80)
            if len(a) > 2:
                st["name"] = " ".join(a[2:])[:40]
            st.setdefault("name", "Zumo")
            guardar_estado(st)
            return self.tg.mensaje(chat, f"✅ Servidor: `{st['host']}:{st['port']}` · nombre `{st['name']}`")
        if cmd == "/payload":
            if not resto:
                return self.tg.mensaje(chat, "Payload actual:\n```\n" + (st.get("payload") or "(vacío)") + "\n```")
            st["payload"] = "" if resto.lower() == "borrar" else resto
            guardar_estado(st)
            return self.tg.mensaje(chat, "✅ Payload " + ("borrado." if not st["payload"] else "guardado."))
        if cmd == "/tls":
            if not a or a[0] not in ("on", "off"):
                return self.tg.mensaje(chat, "Uso: /tls `on|off` `[sni]`")
            st["tls"] = a[0] == "on"
            st["sni"] = a[1] if len(a) > 1 else st.get("sni", "")
            guardar_estado(st)
            return self.tg.mensaje(chat, f"✅ TLS {'activado' if st['tls'] else 'desactivado'}")
        if cmd == "/ver":
            return self.tg.mensaje(chat, (
                f"Servidor: {st.get('host') or '(sin definir)'}:{st.get('port', 80)}\n"
                f"Nombre: {st.get('name', 'Zumo')}\nTLS: {'sí' if st.get('tls') else 'no'}"
                f"{' · SNI ' + st['sni'] if st.get('sni') else ''}\n\nPayload:\n{st.get('payload') or '(vacío)'}"), md=False)
        if cmd == "/crear":
            if len(a) < 3 or not a[2].isdigit() or int(a[2]) < 1 or (len(a) > 3 and not (a[3].isdigit() and int(a[3]) >= 1)):
                return self.tg.mensaje(chat, "Uso: /crear `usuario` `clave` `días` `[límite]`")
            if not st.get("host"):
                return self.tg.mensaje(chat, "⚠️ Primero definí el servidor con /servidor.")
            err = crear_usuario(a[0], a[1], int(a[2]), int(a[3]) if len(a) > 3 else 1)
            if err:
                return self.tg.mensaje(chat, "⚠️ " + err, md=False)
            self.tg.mensaje(chat, f"✅ Usuario `{a[0]}` creado ({a[2]} días).")
            return self.enviar_zs(chat, a[0], f"Cuenta {a[0]} · vence {usuarios()[a[0]][1]}")
        if cmd == "/exportar":
            if not a:
                return self.tg.mensaje(chat, "Uso: /exportar `usuario`")
            return self.enviar_zs(chat, a[0], f"Cuenta {a[0]}")
        if cmd == "/renovar":
            if len(a) < 2 or not a[1].isdigit() or int(a[1]) < 1:
                return self.tg.mensaje(chat, "Uso: /renovar `usuario` `días`")
            if a[0] not in usuarios():
                return self.tg.mensaje(chat, "Ese usuario no está en el panel.")
            exp = renovar_usuario(a[0], int(a[1]))
            self.tg.mensaje(chat, f"✅ `{a[0]}` vence el {exp}.")
            return self.enviar_zs(chat, a[0], f"Cuenta {a[0]} renovada · vence {exp}")
        if cmd == "/clave":
            if len(a) < 2 or not CLAVE_RE.match(a[1]):
                return self.tg.mensaje(chat, "Uso: /clave `usuario` `nueva` (letras y números, 1 a 10)")
            if a[0] not in usuarios():
                return self.tg.mensaje(chat, "Ese usuario no está en el panel.")
            run("chpasswd", entrada=f"{a[0]}:{a[1]}\n")
            clave_guardar(a[0], a[1])
            self.tg.mensaje(chat, f"✅ Contraseña de `{a[0]}` cambiada.")
            return self.enviar_zs(chat, a[0], f"Cuenta {a[0]} con la clave nueva")
        if cmd == "/limite":
            if len(a) < 2 or not a[1].isdigit() or int(a[1]) < 1:
                return self.tg.mensaje(chat, "Uso: /limite `usuario` `n`")
            if a[0] not in usuarios():
                return self.tg.mensaje(chat, "Ese usuario no está en el panel.")
            bash_lib("zumo_db_set", a[0], "2", a[1])
            return self.tg.mensaje(chat, f"✅ Límite de `{a[0]}`: {a[1]}")
        if cmd == "/borrar":
            if not a:
                return self.tg.mensaje(chat, "Uso: /borrar `usuario`")
            if a[0] not in usuarios():
                return self.tg.mensaje(chat, "Ese usuario no está en el panel.")
            borrar_usuario(a[0])
            return self.tg.mensaje(chat, f"🗑 `{a[0]}` eliminado.")
        if cmd == "/usuarios":
            us = usuarios()
            if not us:
                return self.tg.mensaje(chat, "No hay usuarios.")
            hoy = date.today().isoformat()
            filas = [f"{'🔴' if e < hoy else '🟢'} {u} · límite {l} · vence {e}" for u, (l, e) in sorted(us.items())]
            return self.tg.mensaje(chat, "\n".join(filas), md=False)
        self.tg.mensaje(chat, "No conozco ese comando. /ayuda")


def main():
    env = leer_env()
    token = os.environ.get("BOT_TOKEN") or env.get("BOT_TOKEN")
    if not token:
        sys.exit("Falta BOT_TOKEN en " + ENV)
    admins = {int(x) for x in re.split(r"[,\s]+", env.get("ADMINS", "")) if x.strip().lstrip("-").isdigit()}
    secreto = env.get("ZS_SECRET") or zs.SECRETO_POR_DEFECTO
    bot = Bot(Telegram(token), admins, secreto)
    print("zumo-bot: listo, admins:", sorted(admins) or "ninguno (usá /id)", flush=True)
    offset = 0
    while True:
        try:
            for u in bot.tg.actualizaciones(offset):
                offset = u["update_id"] + 1
                if "message" in u:
                    bot.manejar(u["message"])
        except Exception as e:
            print("zumo-bot: error de red:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
