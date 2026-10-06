#!/usr/bin/env python3
"""Bot de Telegram para administrar cuentas de Zumo VPN desde el teléfono.

Se maneja solo con botones (inline). Corre en la VPS como root (crea usuarios SSH reales, igual que el panel) y le manda al
administrador el archivo .zs que abre la app. Solo responde a los IDs de ADMINS.

Configuración: /etc/zumo/bot.env  (BOT_TOKEN, ADMINS, ZS_SECRET)
Datos del servidor (host, puerto, payload...): /etc/zumo/bot.json (se cambian desde el bot)
Servidores de la app Android: /etc/zumo/app-servidores.json (y GITHUB_TOKEN / GITHUB_REPO en bot.env para compilar)
Apariencia de la app Android: /etc/zumo/app-marca/ (tema.json, icono.png, fondo.jpg); se cambia desde el bot
"""
import base64
import hashlib
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
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compilar  # noqa: E402
import marca  # noqa: E402
import servidores as srv  # noqa: E402
import tema as T  # noqa: E402
import vista  # noqa: E402
import zs  # noqa: E402

ENV = os.environ.get("ZUMO_BOT_ENV", "/etc/zumo/bot.env")
ESTADO = os.environ.get("ZUMO_BOT_JSON", "/etc/zumo/bot.json")
APPSRV = os.environ.get("ZUMO_APP_SERVIDORES", "/etc/zumo/app-servidores.json")
MARCA = os.environ.get("ZUMO_APP_MARCA", "/etc/zumo/app-marca")
DB = os.environ.get("ZUMO_DB", "/etc/zumo/usuarios.db")
CLAVES = os.environ.get("ZUMO_CLAVES", "/etc/zumo/claves.db")
LIB = os.environ.get("ZUMO_LIB", "/etc/zumo/zumo-lib.sh")
LIMCONF = os.environ.get("ZUMO_LIMCONF", "/etc/zumo/limit.conf")
ZUMOID = os.environ.get("ZUMO_ZUMOID", "/usr/local/bin/zumoid")  # control de dispositivo (Android ID)

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


# ------------------------------------------------------------- apariencia de la app
def tema_guardado():
    """¿El dueño ya personalizó algo? (si no, al compilar no se toca la apariencia del repo)"""
    return os.path.isfile(os.path.join(MARCA, "tema.json"))


def cargar_tema():
    try:
        with open(os.path.join(MARCA, "tema.json"), encoding="utf-8") as f:
            return T.normalizar(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return T.normalizar({})


def _escribir_marca(nombre, datos):
    os.makedirs(MARCA, mode=0o700, exist_ok=True)
    ruta = os.path.join(MARCA, nombre)
    fd = os.open(ruta + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(datos)
    os.replace(ruta + ".tmp", ruta)


def guardar_tema(t):
    _escribir_marca("tema.json", T.a_json(t).encode("utf-8"))


def imagen_marca(nombre):
    """Bytes de icono.png o fondo.jpg guardados por el bot, o None."""
    try:
        with open(os.path.join(MARCA, nombre), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def guardar_imagen_marca(nombre, datos):
    if not tema_guardado():
        guardar_tema(cargar_tema())
    _escribir_marca(nombre, datos)


def borrar_imagen_marca(nombre):
    try:
        os.remove(os.path.join(MARCA, nombre))
    except FileNotFoundError:
        pass


def secretos_marca():
    """{secreto: valor} con la apariencia para subir al repo, o None si nunca se personalizó."""
    if not tema_guardado():
        return None
    return marca.secretos(marca.empaquetar(T.a_json(cargar_tema()), imagen_marca("icono.png"), imagen_marca("fondo.jpg")))


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


# ------------------------------------------------------------ dispositivo (Android ID)
def zid(*args):
    """Llama a zumoid (el servicio que guarda el Android ID de cada usuario). None si no está instalado."""
    if not os.access(ZUMOID, os.X_OK):
        return None
    try:
        return subprocess.run([ZUMOID, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None


def disp_get(u):
    """{'id', 'lock', 'last'} del celular del usuario, o None si todavía no mandó su ID."""
    r = zid("get", u)
    if not r or r.returncode != 0 or not r.stdout.strip():
        return None
    p = r.stdout.rstrip("\n").split("\t")
    if len(p) < 4:
        return None
    return {"id": p[0], "lock": p[1] == "1", "last": int(p[3] or 0)}


def disp_ultimo(u):
    """Último evento bloqueado: {'epoch', 'evento', 'id'} o None."""
    r = zid("last", u)
    if not r or r.returncode != 0:
        return None
    p = r.stdout.rstrip("\n").split("\t")
    return {"epoch": int(p[0] or 0), "evento": p[1], "id": p[2]} if len(p) >= 3 else None


def fecha_corta(epoch):
    return datetime.fromtimestamp(epoch).strftime("%d/%m %H:%M")


def borrar_usuario(u):
    run("pkill", "-9", "-u", u)
    run("userdel", u)
    bash_lib("zumo_db_del", u)
    zid("forget", u)
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

    def _subir(self, metodo, campo, nombre, datos, extras):
        borde = uuid.uuid4().hex
        cuerpo = b""
        for k, v in extras:
            cuerpo += (f"--{borde}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
        cuerpo += (f"--{borde}\r\nContent-Disposition: form-data; name=\"{campo}\"; filename=\"{nombre}\"\r\n"
                   "Content-Type: application/octet-stream\r\n\r\n").encode() + datos + f"\r\n--{borde}--\r\n".encode()
        req = urllib.request.Request(self.base + metodo, data=cuerpo,
                                     headers={"Content-Type": f"multipart/form-data; boundary={borde}"})
        urllib.request.urlopen(req, timeout=60).read()

    def documento(self, chat, nombre, datos, leyenda=""):
        self._subir("sendDocument", "document", nombre, datos, (("chat_id", str(chat)), ("caption", leyenda)))

    def foto(self, chat, datos, leyenda="", botones=None):
        extras = [("chat_id", str(chat)), ("caption", leyenda[:1000])]
        if botones:
            extras.append(("reply_markup", json.dumps(self._teclado(botones))))
        nombre = "vista.jpg" if datos[:3] == b"\xff\xd8\xff" else "vista.png"
        self._subir("sendPhoto", "photo", nombre, datos, extras)

    def bajar(self, file_id):
        """Bytes de una foto o archivo que mandaron al bot (Telegram deja bajar hasta 20 MB)."""
        ruta = self._post("getFile", {"file_id": file_id})["result"]["file_path"]
        url = self.base.replace("/bot", "/file/bot", 1) + ruta
        with urllib.request.urlopen(url, timeout=120) as r:
            return r.read()


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
        txt = f"👤 {u}\nVence: {exp} ({venc})\nLímite de conexiones: {lim}"
        botones = [
            [("📤 Enviar .zs", f"x:{u}")],
            [("🔄 Renovar", f"r:{u}"), ("🔑 Cambiar clave", f"k:{u}")],
            [("🔢 Límite", f"l:{u}"), ("🗑 Borrar", f"b:{u}")]]
        d = disp_get(u)
        if d:
            txt += f"\n\n📱 Android ID: {d['id']}\n" + ("🔒 Vinculado: solo ese celular puede entrar" if d["lock"] else "🔓 Sin vincular: entra desde cualquier celular")
            txt += f"\nÚltimo ingreso: {fecha_corta(d['last'])}"
            ult = disp_ultimo(u)
            if ult and ult["evento"] == "otro-dispositivo":
                txt += f"\n⚠️ Intento bloqueado: otro celular ({ult['id']}) el {fecha_corta(ult['epoch'])}"
            elif ult and ult["evento"] == "sin-verificar":
                txt += f"\n⚠️ Sesión cortada: entró sin mandar el ID (app vieja u otra app) el {fecha_corta(ult['epoch'])}"
            botones.append([("🔓 Desvincular celular" if d["lock"] else "🔒 Vincular a este celular", f"dv:{u}"),
                            ("🗑 Olvidar celular", f"dx:{u}")])
        elif zid("get", u) is not None:
            txt += "\n\n📱 Android ID: todavía no conectó con la app nueva"
        botones.append([("◂ Usuarios", "lista:0")])
        self.mostrar(chat, mid, txt, botones)

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
                    [("🎨 Apariencia de la app", "t")],
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
                if self.estado[chat]["paso"] in ("t_icono", "t_fondo"):
                    return self.imagen_recibida(chat, msg)
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
            if cb["message"].get("photo"):
                mid = None      # el botón está debajo de una foto: no se puede editar, va un mensaje nuevo
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
        if acc == "dv":      # vincular / desvincular al celular
            d = disp_get(arg)
            if not d:
                return self.pantalla_usuario(chat, mid, arg)
            r = zid("unlock" if d["lock"] else "lock", arg)
            if r is not None and r.returncode != 0:
                self.tg.mensaje(chat, "⚠️ " + (r.stderr.strip() or "No se pudo cambiar"))
            return self.pantalla_usuario(chat, mid, arg)
        if acc == "dx":
            return self.mostrar(chat, mid, f"🗑 ¿Olvidar el celular de {arg}?\nSe desvincula y se anota el próximo que conecte con ese usuario.",
                                [[("✅ Sí, olvidar", f"dxs:{arg}"), ("✖ No", f"u:{arg}")]])
        if acc == "dxs":
            zid("forget", arg)
            return self.pantalla_usuario(chat, mid, arg)
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
        if acc == "t":
            return self.boton_tema(chat, mid, arg)
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
                                f"{'' if n else ' (Lista vacía: no se toca el secreto.)'}\n"
                                f"{'La apariencia que armaste también se sube.' + chr(10) if tema_guardado() else ''}Tarda unos minutos. ¿Compilo?",
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
        if paso.startswith("t_"):
            return self.texto_tema(chat, e, paso, t)
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
        mid = self.tg.mensaje(chat, "⏳ En cola en GitHub…")
        ult = ""
        while True:
            r = gh.run(rid)
            if r.get("status") == "completed":
                r["id"] = rid
                return r
            if time.time() - t0 > 45 * 60:
                raise compilar.ErrorGitHub("La compilación tardó más de 45 minutos. Revisala en GitHub → Actions.")
            m = int((time.time() - t0) // 60)
            done = total = 0
            actual = None
            try:
                done, total, actual = gh.pasos(rid)
            except Exception:
                pass
            txt = self._barra_compilacion(r.get("status"), done, total, actual, m)
            if txt != ult:
                ult = txt
                self.tg.editar(chat, mid, txt)
            time.sleep(15)

    @staticmethod
    def _barra_compilacion(status, done, total, actual, minutos):
        """Texto con barra y % según los pasos ya completados del build."""
        if status == "queued" or total == 0:
            return f"⏳ En cola en GitHub… {minutos} min"
        pct = int(done * 100 / total)
        llenos = pct // 10
        barra = "▓" * llenos + "░" * (10 - llenos)
        linea = f"🔨 Compilando… {barra} {pct}%  (paso {done}/{total})"
        if actual:
            linea += f"\n⚙️ {actual}"
        return linea + f"\n⏱ {minutos} min"

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
            apariencia = secretos_marca()
            if apariencia:
                for nombre, valor in apariencia.items():
                    gh.subir_secreto(nombre, valor)
                self.tg.mensaje(chat, "🎨 Apariencia subida al repo (cifrada).")
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

    # -- apariencia de la app
    NOTA_VISTA = "Vista aproximada: en el teléfono los emojis y algunas letras se ven un poco distinto."

    def pantalla_tema(self, chat, mid, aviso=""):
        t = cargar_tema()
        hay_fondo, hay_icono = imagen_marca("fondo.jpg") is not None, imagen_marca("icono.png") is not None
        fondo = "imagen" if hay_fondo else ("degradado" if t["fondo2"] else "color liso")
        txt = (aviso + "\n\n" if aviso else "") + (
            "🎨 Apariencia de la app\n\n"
            f"Nombre: {t['nombre']}\nLema: {t['lema'] or '(sin lema)'}\n"
            f"Plantilla: {T.nombre_plantilla(t)}\nFondo: {fondo}\n"
            f"Ícono: {'el tuyo' if hay_icono else 'el original'}\n"
            f"Letra: {dict(T.FUENTES)[t['fuente']]} · tamaño {dict(T.ESCALAS).get(t['escala'], str(t['escala']) + ' %').lower()}\n\n"
            "Tus clientes ven los cambios cuando compilás y les pasás el APK nuevo.")
        self.mostrar(chat, mid, txt, [
            [("🧩 Plantillas", "t:pl"), ("👁 Vista previa", "t:v")],
            [("🏷 Nombre", "t:n"), ("💬 Lema", "t:le")],
            [("🎨 Colores", "t:co"), ("🌄 Fondo", "t:fo")],
            [("🖼 Ícono y logo", "t:ic"), ("🔤 Letras", "t:lt")],
            [("📋 Menús y secciones", "t:me")],
            [("🔨 Compilar y enviarme el APK", "acomp")],
            [("♻️ Volver al diseño original", "t:rs")],
            [("◂ App Android", "app")]])

    def enviar_vista(self, chat, t=None, leyenda="", botones=None):
        """Manda la imagen de cómo queda la app. Sin Pillow en la VPS, avisa cómo instalarlo."""
        botones = botones or [[("🎨 Seguir cambiando", "t")], [("🔨 Compilar y enviarme el APK", "acomp")]]
        if not vista.HAY_PIL:
            return self.tg.mensaje(chat, "👁 Para ver las vistas previas falta una herramienta en la VPS. Corré de nuevo el instalador del bot "
                                         "(o: apt install -y python3-pil fonts-dejavu-core && systemctl restart zumo-bot).", botones)
        img = vista.captura(t or cargar_tema(), imagen_marca("icono.png"), imagen_marca("fondo.jpg"))
        self.tg.foto(chat, img, (leyenda + "\n\n" if leyenda else "") + self.NOTA_VISTA, botones)

    def pantalla_plantillas(self, chat, mid):
        botones, fila = [], []
        for i, (pid, nombre, _, _) in enumerate(T.PLANTILLAS):
            fila.append((f"{i + 1}. {nombre}", f"t:pv:{pid}"))
            if len(fila) == 2:
                botones.append(fila)
                fila = []
        if fila:
            botones.append(fila)
        botones.append([("◂ Apariencia", "t")])
        if not vista.HAY_PIL:
            lista = "\n".join(f"{i + 1}. {n}: {d}" for i, (_, n, d, _) in enumerate(T.PLANTILLAS))
            return self.mostrar(chat, mid, "🧩 Plantillas\n\n" + lista + "\n\nTocá una para usarla.", botones)
        base = cargar_tema()
        icono, fondo = imagen_marca("icono.png"), imagen_marca("fondo.jpg")
        clave = hashlib.sha256((T.a_json(T.aplicar_plantilla(base, "zumo"))).encode() + (icono or b"") + (fondo or b"")).hexdigest()
        if getattr(self, "_muestrario", (None, None))[0] != clave:
            temas = [(n, T.aplicar_plantilla(base, pid)) for pid, n, _, _ in T.PLANTILLAS]
            self._muestrario = (clave, vista.muestrario(temas, icono, fondo))
        self.tg.foto(chat, self._muestrario[1], "🧩 Plantillas: así quedaría tu app con cada una.\n"
                                                "Tocá una para verla en grande; después podés cambiarle lo que quieras.", botones)

    def pantalla_colores(self, chat, mid, aviso=""):
        t = cargar_tema()
        lineas = "\n".join(f"{nombre}: {t[k]}" for k, nombre in T.COLORES_EDITABLES)
        botones, fila = [], []
        for k, nombre in T.COLORES_EDITABLES:
            fila.append((nombre, f"t:c:{k}"))
            if len(fila) == 2:
                botones.append(fila)
                fila = []
        botones += [[("👁 Vista previa", "t:v")], [("◂ Apariencia", "t")]]
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") + "🎨 Colores\n\n" + lineas +
                     "\n\nTocá el que quieras cambiar. Los bordes y los tonos intermedios se acomodan solos.", botones)

    def pantalla_color(self, chat, mid, k):
        t = cargar_tema()
        nombre = "Segundo color del degradado" if k == "fondo2" else dict(T.COLORES_EDITABLES)[k]
        botones, fila = [], []
        for n, h in T.paleta(k):
            fila.append((n, f"t:cs:{k}:{h[1:]}"))
            if len(fila) == 3:
                botones.append(fila)
                fila = []
        if fila:
            botones.append(fila)
        botones += [[("✏️ Escribir un código de color", f"t:ce:{k}")],
                    [("◂ Fondo", "t:fo")] if k == "fondo2" else [("◂ Colores", "t:co")]]
        self.mostrar(chat, mid, f"🎨 {nombre}\nAhora: {t.get(k) or '(sin definir)'}\n\nElegí uno, o escribí el código exacto (ej: #B388FF).", botones)

    def pantalla_fondo(self, chat, mid, aviso=""):
        t = cargar_tema()
        hay = imagen_marca("fondo.jpg") is not None
        ahora = "una imagen" if hay else (f"degradado {t['fondo']} → {t['fondo2']}" if t["fondo2"] else f"color liso {t['fondo']}")
        txt = (aviso + "\n\n" if aviso else "") + f"🌄 Fondo\nAhora: {ahora}\n"
        botones = [[("🎨 Color", "t:c:fondo"), ("🌗 Degradado", "t:c:fondo2")]]
        if t["fondo2"]:
            botones.append([("▫️ Quitar el degradado", "t:fl")])
        botones.append([("📷 Poner una imagen", "t:fi")] + ([("🗑 Quitar la imagen", "t:fq")] if hay else []))
        if hay:
            txt += f"La imagen se oscurece un {t['velo']} % para que se lean las letras.\n"
            botones.append([(("✓ " if t["velo"] == n else "") + nombre, f"t:fv:{n}") for n, nombre in T.VELOS[:4]])
        txt += f"Tarjetas: {dict(T.OPACIDADES).get(t['opacidad'], str(t['opacidad']) + ' %').lower()} (cuánto se ve el fondo a través de ellas)."
        botones.append([(("✓ " if t["opacidad"] == n else "") + nombre, f"t:op:{n}") for n, nombre in T.OPACIDADES[:3]])
        botones += [[("👁 Vista previa", "t:v")], [("◂ Apariencia", "t")]]
        self.mostrar(chat, mid, txt, botones)

    def pantalla_icono(self, chat, mid, aviso=""):
        t = cargar_tema()
        hay = imagen_marca("icono.png") is not None
        logo = "tu ícono" if (hay and t["logo_imagen"]) else (f"el emoji {t['logo']}" if t["logo"] else "nada")
        botones = [[("📷 Importar un ícono", "t:ii")]]
        if hay:
            botones.append([("🗑 Quitar mi ícono", "t:iq")])
            botones.append([(f"Arriba del título: {'mi ícono' if t['logo_imagen'] else 'el emoji'} (cambiar)", "t:il")])
        botones += [[("😀 Cambiar el emoji del logo", "t:ie")], [("👁 Vista previa", "t:v")], [("◂ Apariencia", "t")]]
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") +
                     f"🖼 Ícono y logo\nÍcono de la app en el teléfono: {'el tuyo' if hay else 'el original'}\n"
                     f"Logo arriba del título: {logo}", botones)

    def pantalla_letras(self, chat, mid):
        t = cargar_tema()
        botones, fila = [], []
        for i, (f, nombre) in enumerate(T.FUENTES):
            fila.append((("✓ " if t["fuente"] == f else "") + nombre, f"t:lf:{i}"))
            if len(fila) == 2:
                botones.append(fila)
                fila = []
        botones.append([(("✓ " if t["escala"] == n else "") + nombre, f"t:ls:{n}") for n, nombre in T.ESCALAS])
        botones += [[(f"Título en MAYÚSCULAS: {'sí' if t['titulo_mayus'] else 'no'} (cambiar)", "t:lm")],
                    [("👁 Vista previa", "t:v")], [("◂ Apariencia", "t")]]
        self.mostrar(chat, mid, f"🔤 Letras\nTipo: {dict(T.FUENTES)[t['fuente']]}\n"
                                f"Tamaño: {dict(T.ESCALAS).get(t['escala'], str(t['escala']) + ' %')}\n"
                                f"Título: {T.titulo(t)}\n\nArriba el tipo de letra, abajo el tamaño.", botones)

    def pantalla_menus(self, chat, mid, aviso=""):
        t = cargar_tema()
        secciones = "\n".join(f"{'✅' if t[k] else '⬜'} {nombre}" for k, nombre in T.SECCIONES)
        contactos = "\n".join(f"{i + 1}. {e['texto']} → {e['url']}" for i, e in enumerate(t["enlaces"])) or "(ninguno)"
        botones = [[(f"{'✅' if t[k] else '⬜'} {nombre}", f"t:ms:{i}")] for i, (k, nombre) in enumerate(T.SECCIONES)]
        botones.append([(("✓ " if t["radio"] == n else "") + nombre, f"t:mr:{n}") for n, nombre in T.RADIOS])
        botones += [[(f"🗑 Quitar: {e['texto']}", f"t:mq:{i}")] for i, e in enumerate(t["enlaces"])]
        if len(t["enlaces"]) < T.MAX_ENLACES:
            botones.append([("➕ Botón de contacto", "t:ma")])
        botones += [[("👁 Vista previa", "t:v")], [("◂ Apariencia", "t")]]
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") +
                     f"📋 Menús y secciones\n\nQué se ve en la app (tocá para mostrar u ocultar):\n{secciones}\n\n"
                     f"Esquinas de las tarjetas y botones: {dict(T.RADIOS).get(t['radio'], str(t['radio']))}\n\n"
                     f"Botones de contacto del menú ☰ (WhatsApp, Telegram, tu web):\n{contactos}", botones)

    def boton_tema(self, chat, mid, arg):
        self.estado.pop(chat, None)
        acc, _, resto = arg.partition(":")
        t = cargar_tema()
        if acc == "":
            return self.pantalla_tema(chat, mid)
        if acc == "pl":
            return self.pantalla_plantillas(chat, mid)
        if acc in ("pv", "pu"):
            p = T.plantilla(resto)
            if p is None:
                return self.pantalla_tema(chat, mid, "Esa plantilla ya no existe.")
            nueva = T.aplicar_plantilla(t, resto)
            if acc == "pu":
                guardar_tema(nueva)
                return self.pantalla_tema(chat, mid, f"✅ Plantilla {p[1]} puesta. Cambiale lo que quieras y compilá.")
            botones = [[("✅ Usar esta plantilla", f"t:pu:{resto}")], [("◂ Plantillas", "t:pl"), ("◂ Apariencia", "t")]]
            if not vista.HAY_PIL:
                return self.mostrar(chat, mid, f"🧩 {p[1]}\n{p[2]}", botones)
            return self.enviar_vista(chat, nueva, f"🧩 {p[1]}: {p[2]}", botones)
        if acc == "v":
            return self.enviar_vista(chat, t, "👁 Así queda tu app ahora.")
        if acc == "n":
            return self.pedir(chat, "🏷 Escribí el nombre de la app (el que aparece debajo del ícono y arriba en la pantalla). Máx. 30 letras.", "t_nombre")
        if acc == "le":
            return self.pedir(chat, "💬 Escribí la frase que va debajo del nombre (o - para no poner nada):", "t_lema")
        if acc == "co":
            return self.pantalla_colores(chat, mid)
        if acc == "c":
            return self.pantalla_color(chat, mid, resto)
        if acc == "ce":
            return self.pedir(chat, "✏️ Escribí el código del color, por ejemplo #B388FF", "t_color", k=resto)
        if acc == "cs":
            k, _, h = resto.partition(":")
            return self.poner_color(chat, mid, k, "#" + h)
        if acc == "fo":
            return self.pantalla_fondo(chat, mid)
        if acc == "fl":
            t["fondo2"] = ""
            guardar_tema(T.marcar_cambio(t))
            return self.pantalla_fondo(chat, mid)
        if acc == "fi":
            return self.pedir(chat, "📷 Mandame la imagen de fondo como foto. Conviene que sea vertical; la app la recorta para llenar la pantalla "
                                    "y la oscurece un poco para que se lean las letras.", "t_fondo")
        if acc == "fq":
            borrar_imagen_marca("fondo.jpg")
            return self.pantalla_fondo(chat, mid, "🗑 Imagen de fondo quitada.")
        if acc in ("fv", "op", "ls", "mr") and resto.isdigit():
            t[{"fv": "velo", "op": "opacidad", "ls": "escala", "mr": "radio"}[acc]] = int(resto)
            guardar_tema(T.marcar_cambio(t) if acc == "mr" else t)
            return {"fv": self.pantalla_fondo, "op": self.pantalla_fondo, "ls": self.pantalla_letras, "mr": self.pantalla_menus}[acc](chat, mid)
        if acc == "ic":
            return self.pantalla_icono(chat, mid)
        if acc == "ii":
            return self.pedir(chat, "📷 Mandame la imagen del ícono. Conviene que sea cuadrada.\n"
                                    "Si tiene fondo transparente, mandala como archivo (PNG) y no como foto, para que no lo pierda.", "t_icono")
        if acc == "iq":
            borrar_imagen_marca("icono.png")
            t["logo_imagen"] = False
            guardar_tema(t)
            return self.pantalla_icono(chat, mid, "🗑 Ícono quitado: vuelve el original.")
        if acc == "il":
            t["logo_imagen"] = not t["logo_imagen"]
            guardar_tema(t)
            return self.pantalla_icono(chat, mid)
        if acc == "ie":
            return self.pedir(chat, "😀 Mandame el emoji que va arriba del título (o - para no poner ninguno):", "t_logo")
        if acc == "lt":
            return self.pantalla_letras(chat, mid)
        if acc == "lf" and resto.isdigit() and int(resto) < len(T.FUENTES):
            t["fuente"] = T.FUENTES[int(resto)][0]
            guardar_tema(T.marcar_cambio(t))
            return self.pantalla_letras(chat, mid)
        if acc == "lm":
            t["titulo_mayus"] = not t["titulo_mayus"]
            guardar_tema(t)
            return self.pantalla_letras(chat, mid)
        if acc == "me":
            return self.pantalla_menus(chat, mid)
        if acc == "ms" and resto.isdigit() and int(resto) < len(T.SECCIONES):
            k = T.SECCIONES[int(resto)][0]
            t[k] = not t[k]
            guardar_tema(t)
            return self.pantalla_menus(chat, mid)
        if acc == "ma":
            if len(t["enlaces"]) >= T.MAX_ENLACES:
                return self.pantalla_menus(chat, mid, f"Ya tenés {T.MAX_ENLACES} botones de contacto. Quitá uno para agregar otro.")
            return self.pedir(chat, "➕ Escribí el texto del botón (ej: Soporte por WhatsApp):", "t_enl_texto")
        if acc == "mq" and resto.isdigit() and int(resto) < len(t["enlaces"]):
            t["enlaces"].pop(int(resto))
            guardar_tema(t)
            return self.pantalla_menus(chat, mid)
        if acc == "rs":
            return self.mostrar(chat, mid, "♻️ ¿Volver al diseño original?\nSe pierden el nombre, los colores, el ícono, el fondo y los botones de contacto que pusiste.",
                                [[("✅ Sí, volver al original", "t:rs_si"), ("✖ No", "t")]])
        if acc == "rs_si":
            borrar_imagen_marca("icono.png")
            borrar_imagen_marca("fondo.jpg")
            guardar_tema(T.normalizar({}))
            return self.pantalla_tema(chat, mid, "♻️ Listo: diseño original. Compilá para que la app vuelva a verse como antes.")
        return self.pantalla_tema(chat, mid)

    def poner_color(self, chat, mid, k, valor):
        h = T.color(valor)
        if h is None or k not in ("fondo2",) + tuple(c for c, _ in T.COLORES_EDITABLES):
            return self.pantalla_colores(chat, mid, "⚠️ Ese color no es válido.")
        t = cargar_tema()
        t[k] = h
        if k != "fondo2":
            T.derivar(t, k)
        guardar_tema(T.marcar_cambio(t))
        if k == "fondo2":
            return self.pantalla_fondo(chat, mid, f"✅ Degradado: {t['fondo']} → {h}")
        return self.pantalla_colores(chat, mid, f"✅ {dict(T.COLORES_EDITABLES)[k]}: {h}")

    def texto_tema(self, chat, e, paso, txt):
        t = cargar_tema()
        if paso == "t_nombre":
            n = T.limpiar_nombre(txt)
            if not n:
                return self.tg.mensaje(chat, "⚠️ Nombre vacío. Escribí el nombre de la app:", CANCELAR)
            t["nombre"] = n
        elif paso == "t_lema":
            t["lema"] = "" if txt == "-" else T.limpiar_texto(txt, 60)
        elif paso == "t_logo":
            t["logo"] = "" if txt == "-" else T.limpiar_texto(txt, 8)
            t["logo_imagen"] = False
        elif paso == "t_color":
            if T.color(txt) is None:
                return self.tg.mensaje(chat, "⚠️ No es un código de color. Son 6 letras o números después del #, por ejemplo #B388FF:", CANCELAR)
            self.estado.pop(chat, None)
            return self.poner_color(chat, None, e.get("k", ""), txt)
        elif paso == "t_enl_texto":
            texto = T.limpiar_texto(txt, 30)
            if not texto:
                return self.tg.mensaje(chat, "⚠️ Texto vacío. Escribí el texto del botón:", CANCELAR)
            e.update(paso="t_enl_url", texto=texto)
            return self.tg.mensaje(chat, f"Botón: {texto}\nAhora mandame a dónde lleva: un link (https://...), tu número de WhatsApp con código de país "
                                         "(ej: 5491122334455) o tu @usuario de Telegram.", CANCELAR)
        elif paso == "t_enl_url":
            url = T.limpiar_enlace(txt)
            if url is None:
                return self.tg.mensaje(chat, "⚠️ No lo entendí. Mandame un link que empiece con https://, un número de WhatsApp o un @usuario:", CANCELAR)
            t["enlaces"] = (t["enlaces"] + [{"texto": e["texto"], "url": url}])[:T.MAX_ENLACES]
        else:
            return self.menu(chat)
        guardar_tema(t)
        self.estado.pop(chat, None)
        if paso in ("t_enl_url",):
            return self.pantalla_menus(chat, None, "✅ Botón de contacto agregado.")
        if paso == "t_logo":
            return self.pantalla_icono(chat, None)
        return self.pantalla_tema(chat, None)

    def imagen_recibida(self, chat, msg):
        """Llegó algo mientras se esperaba el ícono o el fondo."""
        es_icono = self.estado[chat]["paso"] == "t_icono"
        limite = None if vista.HAY_PIL else (vista.MAX_ICONO if es_icono else vista.MAX_FONDO)
        if msg.get("photo"):
            fotos = sorted(msg["photo"], key=lambda p: p.get("width", 0) * p.get("height", 0))
            if limite:      # sin Pillow no se puede achicar: la más grande que entre
                entran = [p for p in fotos if 0 < p.get("file_size", 0) <= limite]
                elegida = entran[-1] if entran else fotos[0]
            elif es_icono:  # para el ícono alcanza con ~432 px
                elegida = next((p for p in fotos if min(p.get("width", 0), p.get("height", 0)) >= 432), fotos[-1])
            else:
                elegida = fotos[-1]
            fid = elegida["file_id"]
        elif msg.get("document"):
            d = msg["document"]
            if not d.get("mime_type", "").startswith("image/"):
                return self.tg.mensaje(chat, "⚠️ Ese archivo no es una imagen. Mandá un PNG o un JPG:", CANCELAR)
            if d.get("file_size", 0) > vista.MAX_ENTRADA:
                return self.tg.mensaje(chat, "⚠️ Esa imagen pesa demasiado. Mandala como foto o achicala:", CANCELAR)
            fid = d["file_id"]
        else:
            return self.tg.mensaje(chat, "Estoy esperando una imagen. Mandala como foto o como archivo (PNG o JPG).", CANCELAR)
        try:
            datos = self.tg.bajar(fid)
            lista = vista.preparar_icono(datos) if es_icono else vista.preparar_fondo(datos)
        except vista.ErrorImagen as ex:
            return self.tg.mensaje(chat, f"⚠️ {ex}", CANCELAR)
        self.estado.pop(chat, None)
        if es_icono:
            guardar_imagen_marca("icono.png", lista)
            t = cargar_tema()
            t["logo_imagen"] = True
            guardar_tema(t)
            self.pantalla_icono(chat, None, "✅ Ícono guardado. También lo puse arriba del título; si preferís el emoji, cambialo acá abajo.")
        else:
            guardar_imagen_marca("fondo.jpg", lista)
            self.pantalla_fondo(chat, None, "✅ Imagen de fondo guardada.")
        if vista.HAY_PIL:
            self.enviar_vista(chat, None, "👁 Así queda.")

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
