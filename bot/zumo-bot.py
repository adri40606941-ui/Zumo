#!/usr/bin/env python3
"""Bot de Telegram para administrar cuentas de Zumo VPN desde el teléfono.

Se maneja solo con botones (inline). Corre en la VPS como root (crea usuarios SSH reales, igual que el panel) y le manda al
administrador el archivo .zs que abre la app. Solo responde a los IDs de ADMINS.

Configuración: /etc/zumo/bot.env  (BOT_TOKEN, ADMINS, ZS_SECRET)
Datos del servidor (host, puerto, payload...): /etc/zumo/bot.json (se cambian desde el bot)
Servidores de la app Android: /etc/zumo/app-servidores.json (y GITHUB_TOKEN / GITHUB_REPO en bot.env para compilar)
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compilar  # noqa: E402
import servidores as srv  # noqa: E402
import zs  # noqa: E402

ENV = os.environ.get("ZUMO_BOT_ENV", "/etc/zumo/bot.env")
ESTADO = os.environ.get("ZUMO_BOT_JSON", "/etc/zumo/bot.json")
APPSRV = os.environ.get("ZUMO_APP_SERVIDORES", "/etc/zumo/app-servidores.json")
DB = os.environ.get("ZUMO_DB", "/etc/zumo/usuarios.db")
CLAVES = os.environ.get("ZUMO_CLAVES", "/etc/zumo/claves.db")
LIB = os.environ.get("ZUMO_LIB", "/etc/zumo/zumo-lib.sh")
LIMCONF = os.environ.get("ZUMO_LIMCONF", "/etc/zumo/limit.conf")

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


def cargar_app():
    """Lista de servidores de la app: [{name, host, port, payload, tls, sni}, ...]."""
    try:
        with open(APPSRV, encoding="utf-8") as f:
            l = json.load(f)
        return l if isinstance(l, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def guardar_app(lista):
    tmp = APPSRV + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)
    os.replace(tmp, APPSRV)


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
                                         "allowed_updates": ["message", "callback_query"]}, timeout=45).get("result", [])

    @staticmethod
    def _teclado(botones):
        """botones: lista de filas, cada fila una lista de (texto, callback_data)."""
        return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in fila] for fila in botones]}

    def mensaje(self, chat, texto, botones=None, md=False):
        d = {"chat_id": chat, "text": texto[:4000], "disable_web_page_preview": True}
        if md:
            d["parse_mode"] = "Markdown"
        if botones:
            d["reply_markup"] = self._teclado(botones)
        try:
            return self._post("sendMessage", d)["result"]["message_id"]
        except urllib.error.HTTPError:
            d.pop("parse_mode", None)
            return self._post("sendMessage", d)["result"]["message_id"]

    def editar(self, chat, mid, texto, botones=None):
        d = {"chat_id": chat, "message_id": mid, "text": texto[:4000], "disable_web_page_preview": True}
        if botones:
            d["reply_markup"] = self._teclado(botones)
        try:
            self._post("editMessageText", d)
        except urllib.error.HTTPError:
            pass  # "message is not modified" o mensaje viejo: no pasa nada

    def responder_cb(self, cb_id, texto=""):
        try:
            self._post("answerCallbackQuery", {"callback_query_id": cb_id, "text": texto})
        except urllib.error.HTTPError:
            pass

    def borrar(self, chat, mid):
        try:
            self._post("deleteMessage", {"chat_id": chat, "message_id": mid})
        except urllib.error.HTTPError:
            pass

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


# ------------------------------------------------------------------------ menús
POR_PAGINA = 20
MENU = [[("➕ Crear usuario", "crear")],
        [("👥 Usuarios", "lista:0")],
        [("⚙️ Servidor y payload", "srv")],
        [("📱 App Android", "app")],
        [("🪪 Mi ID", "id")]]
CANCELAR = [[("✖ Cancelar", "menu")]]


def teclado_dias(prefijo):
    return [[(f"{n} días", f"{prefijo}:{n}") for n in (7, 15, 30)],
            [(f"{n} días", f"{prefijo}:{n}") for n in (60, 90, 180)],
            [("✏️ Otro número", f"{prefijo}:otro")],
            [("✖ Cancelar", "menu")]]


class Bot:
    def __init__(self, tg, admins, secreto, gh=None):
        self.tg, self.admins, self.secreto, self.gh = tg, admins, secreto, gh
        self.estado = {}      # chat -> {"paso": ..., datos}
        self.compilando = threading.Lock()

    # -- pantallas
    def mostrar(self, chat, mid, texto, botones):
        if mid:
            self.tg.editar(chat, mid, texto, botones)
        else:
            self.tg.mensaje(chat, texto, botones)

    def menu(self, chat, mid=None, aviso=""):
        self.estado.pop(chat, None)
        n = len(usuarios())
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") + f"🛡 Zumo VPN · {n} usuario(s)\n¿Qué querés hacer?", MENU)

    def pantalla_lista(self, chat, mid, pag):
        us = sorted(usuarios().items())
        if not us:
            return self.mostrar(chat, mid, "No hay usuarios todavía.", [[("➕ Crear usuario", "crear")], [("◂ Menú", "menu")]])
        hoy = date.today().isoformat()
        total = (len(us) - 1) // POR_PAGINA + 1
        pag = max(0, min(pag, total - 1))
        trozo = us[pag * POR_PAGINA:(pag + 1) * POR_PAGINA]
        botones = [[(f"{'🔴' if e < hoy else '🟢'} {u} · {e[5:] if len(e) == 10 else e}", f"u:{u}")] for u, (_, e) in trozo]
        nav = []
        if pag > 0:
            nav.append(("‹ Anterior", f"lista:{pag - 1}"))
        if pag < total - 1:
            nav.append(("Siguiente ›", f"lista:{pag + 1}"))
        if nav:
            botones.append(nav)
        botones.append([("◂ Menú", "menu")])
        self.mostrar(chat, mid, f"👥 Usuarios (pág. {pag + 1}/{total})\n🟢 vigente · 🔴 vencido · se ve el mes-día de vencimiento", botones)

    def pantalla_usuario(self, chat, mid, u):
        us = usuarios()
        if u not in us:
            return self.menu(chat, mid, "Ese usuario ya no existe.")
        lim, exp = us[u]
        venc = "🔴 vencido" if exp < date.today().isoformat() else "🟢 vigente"
        self.mostrar(chat, mid, f"👤 {u}\nVence: {exp} ({venc})\nLímite de conexiones: {lim}", [
            [("📤 Enviar .zs", f"x:{u}")],
            [("🔄 Renovar", f"r:{u}"), ("🔑 Cambiar clave", f"k:{u}")],
            [("🔢 Límite", f"l:{u}"), ("🗑 Borrar", f"b:{u}")],
            [("◂ Usuarios", "lista:0")]])

    def pantalla_servidor(self, chat, mid):
        st = cargar_estado()
        txt = (f"⚙️ Servidor\nHost: {st.get('host') or '(sin definir)'}\nPuerto: {st.get('port', 80)}\n"
               f"Nombre del archivo: {st.get('name', 'Zumo')}\nTLS: {'sí' if st.get('tls') else 'no'}"
               f"{' · SNI ' + st['sni'] if st.get('sni') else ''}\n\nPayload:\n{st.get('payload') or '(vacío)'}")
        self.mostrar(chat, mid, txt, [
            [("🌐 Host y puerto", "s:host"), ("🏷 Nombre", "s:name")],
            [("📝 Payload", "s:payload"), ("🧹 Borrar payload", "s:nopayload")],
            [(f"🔒 TLS: {'sí' if st.get('tls') else 'no'} (cambiar)", "s:tls"), ("SNI", "s:sni")],
            [("◂ Menú", "menu")]])

    def pantalla_app(self, chat, mid, aviso=""):
        l = cargar_app()
        filas = "\n".join(f"{i + 1}. {s['name']} · {s['host']}:{s['port']}{' · TLS' if s.get('tls') else ''}"
                          f"{'' if s.get('payload') else ' · ⚠️ sin payload'}" for i, s in enumerate(l))
        txt = (aviso + "\n\n" if aviso else "") + "📱 App Android\n" + (
            f"Servidores en la app ({len(l)}):\n{filas}" if l else
            "Todavía no cargaste servidores en el bot.\nAl compilar sin servidores, la app usa lo que ya haya en el secreto ZUMO_SERVIDORES del repo.")
        botones = [[(f"{i + 1}. {s['name']}", f"a:{i}")] for i, s in enumerate(l)]
        botones += [[("➕ Agregar servidor", "aadd"), ("📥 Pegar lista", "apegar")],
                    [("🔨 Compilar y enviarme el APK", "acomp")],
                    [("🔑 Asegurar clave de firma", "aclave")],
                    [("◂ Menú", "menu")]]
        self.mostrar(chat, mid, txt, botones)

    def pantalla_app_servidor(self, chat, mid, i):
        l = cargar_app()
        if not 0 <= i < len(l):
            return self.pantalla_app(chat, mid, "Ese servidor ya no está.")
        s = l[i]
        txt = (f"📡 {s['name']}\nHost: {s['host']}\nPuerto: {s['port']}\n"
               f"TLS: {'sí' if s.get('tls') else 'no'}{' · SNI ' + s['sni'] if s.get('sni') else ''}\n\n"
               f"Payload:\n{s.get('payload') or '(vacío)'}")
        self.mostrar(chat, mid, txt, [
            [("📝 Cambiar payload", f"ap:{i}")],
            [("🌐 Host y puerto", f"ah:{i}"), ("🏷 Nombre", f"an:{i}")],
            [(f"🔒 TLS: {'sí' if s.get('tls') else 'no'} (cambiar)", f"at:{i}"), ("SNI", f"as:{i}")],
            [("🗑 Borrar", f"ab:{i}")],
            [("◂ App Android", "app")]])

    def pedir(self, chat, texto, paso, **datos):
        self.estado[chat] = {"paso": paso, **datos}
        self.tg.mensaje(chat, texto, CANCELAR)

    def enviar_zs(self, chat, u, leyenda):
        nombre, datos = armar_zs(u, self.secreto)
        if nombre is None:
            self.tg.mensaje(chat, "⚠️ " + datos, [[("⚙️ Servidor", "srv")], [("◂ Menú", "menu")]])
        else:
            self.tg.documento(chat, nombre, datos, leyenda)

    # -- entrada
    def manejar(self, msg):
        chat = msg["chat"]["id"]
        uid = msg.get("from", {}).get("id")
        texto = (msg.get("text") or "").strip()
        if texto.split("@")[0].lower() == "/id":
            return self.tg.mensaje(chat, f"Tu ID de Telegram: {uid}")
        if uid not in self.admins:
            return self.tg.mensaje(chat, f"No autorizado. Tu ID es {uid}: pasáselo al administrador.")
        try:
            if texto.startswith("/"):
                return self.menu(chat)
            if chat in self.estado:
                self.estado[chat]["_mid"] = msg.get("message_id")
                r = self.texto_libre(chat, texto)
                return r
            self.menu(chat)
        except Exception as e:
            self.tg.mensaje(chat, f"⚠️ Error: {e}")

    def manejar_cb(self, cb):
        chat = cb["message"]["chat"]["id"]
        mid = cb["message"]["message_id"]
        uid = cb.get("from", {}).get("id")
        self.tg.responder_cb(cb["id"])
        if uid not in self.admins:
            return
        try:
            if cb.get("data") == "id":
                return self.tg.mensaje(chat, f"Tu ID de Telegram: {uid}")
            self.boton(chat, mid, cb.get("data", ""))
        except Exception as e:
            self.tg.mensaje(chat, f"⚠️ Error: {e}")

    # -- botones
    def boton(self, chat, mid, d):
        acc, _, arg = d.partition(":")
        if acc == "menu":
            return self.menu(chat, mid)
        if acc == "lista":
            self.estado.pop(chat, None)
            return self.pantalla_lista(chat, mid, int(arg or 0))
        if acc == "u":
            return self.pantalla_usuario(chat, mid, arg)
        if acc == "srv":
            self.estado.pop(chat, None)
            return self.pantalla_servidor(chat, mid)
        if acc == "crear":
            if not cargar_estado().get("host"):
                return self.mostrar(chat, mid, "⚠️ Primero definí el servidor (host y puerto).", [[("⚙️ Servidor", "srv")], [("◂ Menú", "menu")]])
            return self.pedir(chat, "➕ Nuevo usuario\n\nEscribí el nombre de usuario (empieza con letra, solo letras y números, máx. 10):", "c_user")
        if acc == "cd":      # días elegidos al crear
            if arg == "otro":
                self.estado[chat]["paso"] = "c_dias"
                return self.tg.mensaje(chat, "Escribí la cantidad de días:", CANCELAR)
            return self.crear_limite(chat, int(arg))
        if acc == "cl":      # límite elegido al crear
            return self.crear_final(chat, int(arg))
        if acc == "x":
            return self.enviar_zs(chat, arg, f"Cuenta {arg}")
        if acc == "r":
            return self.mostrar(chat, mid, f"🔄 Renovar {arg}\n¿Por cuántos días desde hoy?", teclado_dias(f"rd:{arg}"))
        if acc == "rd":
            u, _, n = arg.rpartition(":")
            if n == "otro":
                return self.pedir(chat, f"Escribí los días para renovar a {u}:", "r_dias", u=u)
            return self.renovar(chat, u, int(n))
        if acc == "k":
            return self.pedir(chat, f"🔑 Nueva contraseña para {arg} (letras y números, 1 a 10):", "k_clave", u=arg)
        if acc == "l":
            return self.mostrar(chat, mid, f"🔢 Límite de conexiones de {arg}", [
                [(str(n), f"ln:{arg}:{n}") for n in (1, 2, 3, 5)], [("◂ Volver", f"u:{arg}")]])
        if acc == "ln":
            u, _, n = arg.rpartition(":")
            bash_lib("zumo_db_set", u, "2", n)
            return self.pantalla_usuario(chat, mid, u)
        if acc == "b":
            return self.mostrar(chat, mid, f"🗑 ¿Borrar a {arg}? Se corta su conexión y no se puede deshacer.", [
                [("✅ Sí, borrar", f"bs:{arg}"), ("✖ No", f"u:{arg}")]])
        if acc == "bs":
            borrar_usuario(arg)
            return self.pantalla_lista(chat, mid, 0)
        if acc == "s":
            return self.boton_servidor(chat, mid, arg)
        if acc in ("app", "a", "aadd", "apegar", "acomp", "acomp_si", "aclave", "aclave_si", "ap", "ah", "an", "at", "as", "ab", "abs"):
            return self.boton_app(chat, mid, acc, arg)

    def boton_app(self, chat, mid, acc, arg):
        self.estado.pop(chat, None)
        if acc == "app":
            return self.pantalla_app(chat, mid)
        if acc == "aadd":
            return self.pedir(chat, "➕ Nuevo servidor de la app\n\nEscribí el nombre que va a ver el cliente (ej: APP 02):", "a_nombre")
        if acc == "apegar":
            return self.pedir(chat, "📥 Pegá la lista completa en un solo mensaje, con el formato de servidores.txt:\n\n"
                                    "[APP 02]\nhost = dominio.com\npuerto = 80\npayload = GET / HTTP/1.1[crlf]Host: [host][crlf][crlf]\n\n"
                                    "⚠️ Reemplaza TODA la lista que tiene el bot ahora.", "a_pegar")
        if acc == "acomp":
            if not self.gh:
                return self.mostrar(chat, mid, "⚠️ Falta GITHUB_TOKEN en /etc/zumo/bot.env. Corré de nuevo el instalador del bot para cargarlo.", [[("◂ App Android", "app")]])
            n = len(cargar_app())
            donde = f"{self.gh.repo} (rama {self.gh.rama})"
            return self.mostrar(chat, mid, f"🔨 Compilar la app en GitHub\nRepo: {donde}\n"
                                f"{n} servidor(es) de la lista del bot se suben al secreto antes de compilar."
                                f"{'' if n else ' (Lista vacía: no se toca el secreto.)'}\nTarda unos minutos. ¿Compilo?",
                                [[("✅ Compilar ahora", "acomp_si"), ("✖ No", "app")]])
        if acc == "acomp_si":
            return self.compilar_app(chat)
        if acc == "aclave":
            if not self.gh:
                return self.mostrar(chat, mid, "⚠️ Falta GITHUB_TOKEN en /etc/zumo/bot.env.", [[("◂ App Android", "app")]])
            return self.mostrar(chat, mid, "🔑 Asegurar la clave de firma\n\nHoy la clave con la que se firma la app vive en un caché de GitHub que se borra si pasan 7 días sin compilar; "
                                "si se pierde, los clientes no podrían actualizar la app encima de la anterior.\n\n"
                                "Esto hace una compilación, saca la clave actual cifrada, la guarda como secreto fijo del repo y borra el rastro. "
                                "La app no cambia: sigue firmada con la misma clave. Se hace una sola vez.",
                                [[("✅ Hacerlo ahora", "aclave_si"), ("✖ No", "app")]])
        if acc == "aclave_si":
            return self.compilar_app(chat, asegurar=True)
        i = int(arg) if arg.isdigit() else -1
        l = cargar_app()
        if not 0 <= i < len(l):
            return self.pantalla_app(chat, mid, "Ese servidor ya no está.")
        s = l[i]
        if acc == "a":
            return self.pantalla_app_servidor(chat, mid, i)
        if acc == "ap":
            return self.pedir(chat, f"📝 Pegá el payload nuevo de {s['name']} en un solo mensaje (podés usar [crlf], [host], [split]...).\n"
                                    "Si cambiás solo el payload, no le cambies el nombre: así a los clientes les sigue andando al actualizar.", "a_payload", i=i)
        if acc == "ah":
            return self.pedir(chat, "🌐 Escribí el dominio o IP, con el puerto si no es el 80.\nEj: vps.ejemplo.com  o  vps.ejemplo.com:443", "a_host", i=i)
        if acc == "an":
            return self.pedir(chat, "🏷 Escribí el nombre nuevo (los clientes que ya lo usan tendrán que volver a elegir el servidor):", "a_nombre_edit", i=i)
        if acc == "at":
            s["tls"] = not s.get("tls", False)
            guardar_app(l)
            return self.pantalla_app_servidor(chat, mid, i)
        if acc == "as":
            return self.pedir(chat, "Escribí el SNI (o - para dejarlo vacío):", "a_sni", i=i)
        if acc == "ab":
            return self.mostrar(chat, mid, f"🗑 ¿Borrar el servidor {s['name']} de la app?",
                                [[("✅ Sí, borrar", f"abs:{i}"), ("✖ No", f"a:{i}")]])
        if acc == "abs":
            nombre = l.pop(i)["name"]
            guardar_app(l)
            return self.pantalla_app(chat, mid, f"🗑 {nombre} borrado. Compilá para que se vaya de la app.")

    def boton_servidor(self, chat, mid, arg):
        st = cargar_estado()
        if arg == "host":
            return self.pedir(chat, "🌐 Escribí el dominio o IP del servidor, con el puerto si no es el 80.\nEj: vps.ejemplo.com  o  vps.ejemplo.com:443", "s_host")
        if arg == "name":
            return self.pedir(chat, "🏷 Escribí el nombre del archivo (por ejemplo: nombre → nombre.zs):", "s_name")
        if arg == "payload":
            return self.pedir(chat, "📝 Pegá el payload en un solo mensaje (podés usar [crlf], [host], [split]...).", "s_payload")
        if arg == "nopayload":
            st["payload"] = ""
            guardar_estado(st)
        elif arg == "tls":
            st["tls"] = not st.get("tls", False)
            guardar_estado(st)
        elif arg == "sni":
            return self.pedir(chat, "Escribí el SNI (o - para dejarlo vacío):", "s_sni")
        self.pantalla_servidor(chat, mid)

    # -- texto escrito
    def texto_libre(self, chat, t):
        e = self.estado[chat]
        paso = e["paso"]
        if paso == "c_user":
            if not NOMBRE_RE.match(t):
                return self.tg.mensaje(chat, "⚠️ Nombre inválido. Probá de nuevo (empieza con letra, solo letras y números, máx. 10):", CANCELAR)
            if t in usuarios() or run("id", t).returncode == 0:
                return self.tg.mensaje(chat, "⚠️ Ese usuario ya existe. Escribí otro nombre:", CANCELAR)
            e.update(paso="c_clave", u=t)
            return self.tg.mensaje(chat, f"Usuario: {t}\nAhora la contraseña (letras y números, 1 a 10):", CANCELAR)
        if paso == "c_clave":
            if not CLAVE_RE.match(t):
                return self.tg.mensaje(chat, "⚠️ Contraseña inválida. Solo letras y números, de 1 a 10:", CANCELAR)
            e.update(paso="c_dias_btn", clave=t)
            return self.tg.mensaje(chat, "¿Cuántos días de duración?", [[(f"{n} días", f"cd:{n}") for n in (7, 15, 30)],
                                                                    [(f"{n} días", f"cd:{n}") for n in (60, 90, 180)],
                                                                    [("✏️ Otro número", "cd:otro")], [("✖ Cancelar", "menu")]])
        if paso == "c_dias":
            if not (t.isdigit() and 1 <= int(t) <= 3650):
                return self.tg.mensaje(chat, "⚠️ Escribí un número de días (1 a 3650):", CANCELAR)
            return self.crear_limite(chat, int(t))
        if paso == "r_dias":
            if not (t.isdigit() and 1 <= int(t) <= 3650):
                return self.tg.mensaje(chat, "⚠️ Escribí un número de días (1 a 3650):", CANCELAR)
            return self.renovar(chat, e["u"], int(t))
        if paso == "k_clave":
            if not CLAVE_RE.match(t):
                return self.tg.mensaje(chat, "⚠️ Contraseña inválida. Solo letras y números, de 1 a 10:", CANCELAR)
            u = e["u"]
            self.estado.pop(chat, None)
            run("chpasswd", entrada=f"{u}:{t}\n")
            clave_guardar(u, t)
            self.tg.mensaje(chat, f"✅ Contraseña de {u} cambiada. Va el archivo nuevo:")
            self.enviar_zs(chat, u, f"Cuenta {u} con la clave nueva")
            return self.tg.mensaje(chat, "¿Algo más?", [[("👤 " + u, f"u:{u}")], [("◂ Menú", "menu")]])
        if paso.startswith("a_"):
            return self.texto_app(chat, e, paso, t)
        st = cargar_estado()
        if paso == "s_host":
            host, _, puerto = t.partition(":")
            host = host.strip().removeprefix("https://").removeprefix("http://").strip("/")
            if not host or " " in host or (puerto and not (puerto.isdigit() and 1 <= int(puerto) <= 65535)):
                return self.tg.mensaje(chat, "⚠️ Host o puerto inválido. Ej: vps.ejemplo.com:80", CANCELAR)
            st["host"] = host
            st["port"] = int(puerto) if puerto else st.get("port", 80)
        elif paso == "s_name":
            st["name"] = t[:40]
        elif paso == "s_payload":
            st["payload"] = t
        elif paso == "s_sni":
            st["sni"] = "" if t == "-" else t
        else:
            return self.menu(chat)
        guardar_estado(st)
        self.estado.pop(chat, None)
        self.pantalla_servidor(chat, None)

    def borrar_entrada(self, chat, e):
        """Borra del chat el mensaje con el payload que escribiste (para que no quede a la vista)."""
        if e.get("_mid"):
            self.tg.borrar(chat, e["_mid"])

    def texto_app(self, chat, e, paso, t):
        l = cargar_app()
        i = e.get("i")
        if paso in ("a_nombre", "a_nombre_edit"):
            n = srv.limpiar_nombre(t)
            otros = [s["name"] for k, s in enumerate(l) if k != i]
            if not n or n in otros:
                return self.tg.mensaje(chat, "⚠️ Nombre vacío o repetido. Escribí otro:", CANCELAR)
            if paso == "a_nombre_edit":
                l[i]["name"] = n
                guardar_app(l)
                self.estado.pop(chat, None)
                return self.pantalla_app_servidor(chat, None, i)
            e.update(paso="a_host", nombre=n)
            return self.tg.mensaje(chat, f"Nombre: {n}\nAhora el dominio o IP, con el puerto si no es el 80.\nEj: vps.ejemplo.com:443", CANCELAR)
        if paso == "a_host":
            host, _, puerto = t.partition(":")
            host = host.strip().removeprefix("https://").removeprefix("http://").strip("/")
            if srv.error_host(host) or (puerto and not (puerto.isdigit() and 1 <= int(puerto) <= 65535)):
                return self.tg.mensaje(chat, "⚠️ Host o puerto inválido. Ej: vps.ejemplo.com:80", CANCELAR)
            if i is not None:
                l[i]["host"] = host
                if puerto:
                    l[i]["port"] = int(puerto)
                guardar_app(l)
                self.estado.pop(chat, None)
                return self.pantalla_app_servidor(chat, None, i)
            e.update(paso="a_payload_nuevo", host=host, port=int(puerto) if puerto else 80)
            return self.tg.mensaje(chat, "Ahora pegá el payload en un solo mensaje (podés usar [crlf], [host], [split]...).\nSi no usa payload, escribí -", CANCELAR)
        if paso == "a_payload_nuevo":
            l.append(srv.nuevo(e["nombre"], e["host"], e["port"], "" if t == "-" else t))
            guardar_app(l)
            self.estado.pop(chat, None)
            self.borrar_entrada(chat, e)
            return self.pantalla_app_servidor(chat, None, len(l) - 1)
        if paso == "a_payload":
            l[i]["payload"] = srv.limpiar_linea(t)
            guardar_app(l)
            self.estado.pop(chat, None)
            self.borrar_entrada(chat, e)
            return self.pantalla_app_servidor(chat, None, i)
        if paso == "a_sni":
            l[i]["sni"] = "" if t == "-" else t.strip()
            guardar_app(l)
            self.estado.pop(chat, None)
            return self.pantalla_app_servidor(chat, None, i)
        if paso == "a_pegar":
            nueva = srv.desde_texto(t)
            if not nueva:
                return self.tg.mensaje(chat, "⚠️ No encontré ningún servidor válido (cada uno necesita [Nombre] y host). Probá de nuevo:", CANCELAR)
            guardar_app(nueva)
            self.estado.pop(chat, None)
            self.borrar_entrada(chat, e)
            return self.pantalla_app(chat, None, f"✅ Lista cargada: {len(nueva)} servidor(es).")
        return self.menu(chat)

    # -- compilar
    def compilar_app(self, chat, asegurar=False):
        if not self.compilando.acquire(blocking=False):
            return self.tg.mensaje(chat, "⏳ Ya hay una compilación en curso. Esperá a que termine.")
        threading.Thread(target=self._compilar, args=(chat, asegurar), daemon=True).start()

    def _correr(self, chat):
        """Lanza el workflow y espera a que termine. Devuelve el run."""
        gh = self.gh
        antes = gh.ultimo_run()
        gh.lanzar()
        rid = gh.run_nuevo(antes)
        t0 = time.time()
        mid = self.tg.mensaje(chat, "⏳ Compilando en GitHub… 0 min")
        ult = 0
        while True:
            r = gh.run(rid)
            if r.get("status") == "completed":
                r["id"] = rid
                return r
            if time.time() - t0 > 45 * 60:
                raise compilar.ErrorGitHub("La compilación tardó más de 45 minutos. Revisala en GitHub → Actions.")
            m = int((time.time() - t0) // 60)
            if m != ult:
                ult = m
                self.tg.editar(chat, mid, f"⏳ Compilando en GitHub… {m} min")
            time.sleep(15)

    def _asegurar_clave(self, chat):
        gh = self.gh
        self.tg.mensaje(chat, "🔑 Arrancando…")
        clave = uuid.uuid4().hex + uuid.uuid4().hex
        gh.subir_secreto("ZUMO_EXPORT_PASS", clave)
        try:
            r = self._correr(chat)
            if r.get("conclusion") != "success":
                raise compilar.ErrorGitHub(f"La compilación falló ({r.get('conclusion')}); no se tocó la clave. {r.get('html_url', '')}")
            cifrado = gh.artefacto("clave-firma", r["id"])
        finally:
            gh.borrar_secreto("ZUMO_EXPORT_PASS")
        if cifrado is None:
            return self.tg.mensaje(chat, "✅ La clave ya estaba guardada como secreto fijo en el repo. No hay nada que hacer.", [[("📱 App Android", "app")]])
        jks, ks_pass = compilar.abrir_clave_exportada(cifrado, clave)
        gh.subir_secreto("ZUMO_KEYSTORE_B64", base64.b64encode(jks).decode())
        gh.subir_secreto("ZUMO_KS_PASS", ks_pass)
        self.tg.mensaje(chat, "✅ Clave de firma guardada como secreto fijo del repo (ZUMO_KEYSTORE_B64 y ZUMO_KS_PASS). "
                              "La app sigue firmada con la misma clave de siempre. Ya no depende del caché.",
                        [[("📱 App Android", "app")], [("◂ Menú", "menu")]])

    def _compilar(self, chat, asegurar=False):
        try:
            gh = self.gh
            if asegurar:
                return self._asegurar_clave(chat)
            lista = cargar_app()
            self.tg.mensaje(chat, "🔨 Arrancando…")
            if lista:
                gh.subir_secreto("ZUMO_SERVIDORES", srv.a_texto(lista))
                self.tg.mensaje(chat, f"🔐 Lista de {len(lista)} servidor(es) subida al repo (cifrada).")
            r = self._correr(chat)
            if r.get("conclusion") == "success":
                apk = gh.archivo_de_rama("zumo-vpn.apk")
                n = r.get("run_number", "?")
                self.tg.documento(chat, "zumo-vpn.apk", apk, f"✅ Compilación {n} · {len(lista)} servidor(es)")
                self.tg.mensaje(chat, "✅ Listo. Instalá el APK encima de la versión anterior: se actualiza sin perder nada.",
                                [[("📱 App Android", "app")], [("◂ Menú", "menu")]])
            else:
                cola = ""
                try:
                    log = gh.archivo_de_rama("compilacion.log").decode("utf-8", "replace").strip().splitlines()
                    cola = "\n".join(log[-25:])
                except compilar.ErrorGitHub:
                    pass
                self.tg.mensaje(chat, f"❌ La compilación falló ({r.get('conclusion')}).\n{cola}\n\n{r.get('html_url', '')}",
                                [[("📱 App Android", "app")]])
        except compilar.ErrorGitHub as ex:
            self.tg.mensaje(chat, f"⚠️ {ex}", [[("📱 App Android", "app")]])
        except Exception as ex:  # que el bot nunca se caiga por esto
            self.tg.mensaje(chat, f"⚠️ Error inesperado al compilar: {ex}", [[("📱 App Android", "app")]])
        finally:
            self.compilando.release()

    # -- acciones
    def crear_limite(self, chat, dias):
        self.estado[chat].update(paso="c_lim_btn", dias=dias)
        self.tg.mensaje(chat, f"{dias} días. ¿Cuántas conexiones a la vez (dispositivos)?",
                        [[(str(n), f"cl:{n}") for n in (1, 2, 3, 5)], [("✖ Cancelar", "menu")]])

    def crear_final(self, chat, limite):
        e = self.estado.pop(chat, None)
        if not e or "dias" not in e:
            return self.menu(chat)
        err = crear_usuario(e["u"], e["clave"], e["dias"], limite)
        if err:
            return self.tg.mensaje(chat, "⚠️ " + err, [[("◂ Menú", "menu")]])
        u = e["u"]
        self.tg.mensaje(chat, f"✅ Usuario {u} creado ({e['dias']} días, {limite} conexión/es). Va su archivo:")
        self.enviar_zs(chat, u, f"Cuenta {u} · vence {usuarios()[u][1]}")
        self.tg.mensaje(chat, "¿Algo más?", [[("👤 " + u, f"u:{u}")], [("➕ Crear otro", "crear")], [("◂ Menú", "menu")]])

    def renovar(self, chat, u, dias):
        self.estado.pop(chat, None)
        if u not in usuarios():
            return self.menu(chat, None, "Ese usuario no está en el panel.")
        exp = renovar_usuario(u, dias)
        self.tg.mensaje(chat, f"✅ {u} vence el {exp}. Va el archivo nuevo:")
        self.enviar_zs(chat, u, f"Cuenta {u} renovada · vence {exp}")
        self.tg.mensaje(chat, "¿Algo más?", [[("👤 " + u, f"u:{u}")], [("◂ Menú", "menu")]])


def main():
    env = leer_env()
    token = os.environ.get("BOT_TOKEN") or env.get("BOT_TOKEN")
    if not token:
        sys.exit("Falta BOT_TOKEN en " + ENV)
    admins = {int(x) for x in re.split(r"[,\s]+", env.get("ADMINS", "")) if x.strip().lstrip("-").isdigit()}
    secreto = env.get("ZS_SECRET") or zs.SECRETO_POR_DEFECTO
    gh = None
    if env.get("GITHUB_TOKEN"):
        gh = compilar.GitHub(env["GITHUB_TOKEN"], env.get("GITHUB_REPO") or "adri40606941-ui/Zumo",
                             rama=env.get("GITHUB_REF") or "main")
    bot = Bot(Telegram(token), admins, secreto, gh)
    print("zumo-bot: listo, admins:", sorted(admins) or "ninguno (mandá /id al bot)", flush=True)
    offset = 0
    while True:
        try:
            for u in bot.tg.actualizaciones(offset):
                offset = u["update_id"] + 1
                if "message" in u:
                    bot.manejar(u["message"])
                elif "callback_query" in u:
                    bot.manejar_cb(u["callback_query"])
        except Exception as e:
            print("zumo-bot: error de red:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
