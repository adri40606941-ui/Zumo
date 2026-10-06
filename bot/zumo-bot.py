#!/usr/bin/env python3
"""Bot de Telegram para administrar cuentas de Zumo VPN desde el teléfono.

Se maneja solo con botones (inline). Corre en la VPS como root y crea usuarios SSH reales, igual que el panel:
normales, HWID y temporales. Al crear o renovar manda el mensaje con los datos para pasarle al cliente.
Solo responde a los IDs de ADMINS.

Configuración: /etc/zumo/bot.env  (BOT_TOKEN, ADMINS)
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
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import centro  # noqa: E402
import compilar  # noqa: E402
import marca  # noqa: E402
import servidores as srv  # noqa: E402
import tema as T  # noqa: E402
import vista  # noqa: E402

ENV = os.environ.get("ZUMO_BOT_ENV", "/etc/zumo/bot.env")
APPSRV = os.environ.get("ZUMO_APP_SERVIDORES", "/etc/zumo/app-servidores.json")
MARCA = os.environ.get("ZUMO_APP_MARCA", "/etc/zumo/app-marca")
DB = os.environ.get("ZUMO_DB", "/etc/zumo/usuarios.db")
CLAVES = os.environ.get("ZUMO_CLAVES", "/etc/zumo/claves.db")
LIB = os.environ.get("ZUMO_LIB", "/etc/zumo/zumo-lib.sh")
LIMCONF = os.environ.get("ZUMO_LIMCONF", "/etc/zumo/limit.conf")
PASSWD = os.environ.get("ZUMO_PASSWD", "/etc/passwd")
TEMPDB = os.environ.get("ZUMO_TEMPDB", "/etc/zumo/temporales.db")
BORRADOR = os.environ.get("ZUMO_BORRADOR", "/etc/zumo/borrar-temporal.sh")
PDIRECT_ENV = os.environ.get("ZUMO_PDIRECT_ENV", "/etc/zumo/pdirect.env")

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


# Modo HWID (igual que el panel): el usuario y la contraseña de Linux son el HWID del cliente, y el
# nombre del cliente va en el GECOS como "hwid,<nombre>".
HWID_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
MIN_MAX = 1440          # un temporal dura como mucho un día
DIAS_TEMPORAL = 2       # vencimiento "de papel" del temporal: lo borra el timer mucho antes


def hwids():
    """{hwid: nombre del cliente} de los usuarios creados en modo HWID."""
    out = {}
    try:
        for linea in open(PASSWD, encoding="utf-8", errors="replace"):
            p = linea.rstrip("\n").split(":")
            if len(p) >= 5 and p[4].startswith("hwid,"):
                out[p[0]] = p[4][5:]
    except FileNotFoundError:
        pass
    return out


def limpiar_hwid(t):
    """Deja solo letras y números (lo que pegan suele traer espacios o guiones)."""
    return re.sub(r"[^A-Za-z0-9]", "", t)


def limpiar_etiqueta(t):
    """El nombre del cliente va en el GECOS: sin ':' ni caracteres de control, máx. 48."""
    return re.sub(r"[\x00-\x1f\x7f:]", "", t).strip()[:48] or "cliente"


def existe(u):
    return u in usuarios() or run("id", u).returncode == 0


def temporales():
    """{usuario: epoch en que se borra} de los usuarios temporales."""
    out = {}
    try:
        for linea in open(TEMPDB, encoding="utf-8"):
            u, _, ep = linea.strip().partition(":")
            if u and ep.isdigit():
                out[u] = int(ep)
    except FileNotFoundError:
        pass
    return out


def temp_quitar(u):
    try:
        l = [x for x in open(TEMPDB, encoding="utf-8") if x.split(":")[0] != u]
    except FileNotFoundError:
        return
    with open(TEMPDB, "w", encoding="utf-8") as f:
        f.writelines(l)


def minutos_restantes(epoch):
    return max(0, (epoch - int(time.time()) + 59) // 60)


def texto_minutos(n):
    return "1 minuto" if n == 1 else f"{n} minutos"


# El mismo script que deja install.sh; el bot lo repone si falta (VPS sin actualizar).
BORRADOR_SH = """#!/bin/bash
# Borra un usuario temporal: lo saca del sistema, de la base y del registro temporal.
u="$1"
[ -z "$u" ] && exit 0
[ -f /etc/zumo/zumo-lib.sh ] && source /etc/zumo/zumo-lib.sh
pkill -9 -u "$u" 2>/dev/null
userdel "$u" 2>/dev/null
if command -v zumo_db_del >/dev/null 2>&1; then zumo_db_del "$u"; else sed -i "/^$u:/d" /etc/zumo/usuarios.db 2>/dev/null; fi
if [ -f /etc/zumo/temporales.db ]; then grep -v "^$u:" /etc/zumo/temporales.db > /etc/zumo/temporales.db.tmp 2>/dev/null && mv /etc/zumo/temporales.db.tmp /etc/zumo/temporales.db; fi
exit 0
"""


def programar_borrado(u, minutos):
    """Anota el temporal y agenda su borrado con systemd-run, como el panel. False si no se pudo agendar
    (igual queda anotado: el limitador borra los temporales vencidos aunque el timer se pierda)."""
    if not os.access(BORRADOR, os.X_OK):
        with open(BORRADOR, "w", encoding="utf-8") as f:
            f.write(BORRADOR_SH)
        os.chmod(BORRADOR, 0o755)
    temp_quitar(u)
    with open(TEMPDB, "a", encoding="utf-8") as f:
        f.write(f"{u}:{int(time.time()) + minutos * 60}\n")
    run("systemctl", "reset-failed", f"zumo-temp-{u}.timer")
    r = run("systemd-run", "--quiet", "--collect", f"--unit=zumo-temp-{u}", f"--on-active={minutos}min",
            "--timer-property=AccuracySec=5s", BORRADOR, u)
    return r.returncode == 0


class Aviso(str):
    """El usuario quedó creado, pero hay algo para avisar (no es un error de alta)."""


def _alta(u, clave, limite, dias, minutos, gecos=None):
    """Crea la cuenta de Linux y la anota en el panel. Con minutos es temporal.
    Devuelve None, un error (no se creó) o un Aviso (se creó, con una advertencia)."""
    exp = (date.today() + timedelta(days=DIAS_TEMPORAL if minutos else dias)).isoformat()
    args = ["useradd"]
    if gecos or not re.match(r"^[a-z][a-z0-9]*$", u):
        args.append("--badname")
    args += ["-M", "-s", "/bin/false", "-e", fecha_cuenta(exp)]
    if gecos:
        args += ["-c", gecos]
    r = run(*args, u)
    if r.returncode != 0:
        return "No se pudo crear el usuario: " + (r.stderr.strip() or "error")
    run("chpasswd", entrada=f"{u}:{clave}\n")
    if not gecos:
        clave_guardar(u, clave)   # la de un HWID es el mismo HWID: no se guarda
    bash_lib("zumo_db_add", u, str(limite), exp)
    if minutos and not programar_borrado(u, minutos):
        return Aviso("No se pudo agendar el borrado automático (systemd-run). Lo va a borrar el limitador "
                     "cuando se cumpla el tiempo; si no, borralo vos desde su ficha.")
    return None


def crear_usuario(u, clave, dias, limite, minutos=None):
    """Usuario común (usuario y contraseña). Con minutos es temporal: se borra solo al cumplirse."""
    if not NOMBRE_RE.match(u):
        return "Usuario inválido: empieza con letra, solo letras y números, máx. 10."
    if not CLAVE_RE.match(clave):
        return "Contraseña inválida: solo letras y números, de 1 a 10."
    if existe(u):
        return "Ese usuario ya existe."
    return _alta(u, clave, limite, dias, minutos)


def crear_hwid(hwid, etiqueta, dias, limite, minutos=None):
    """Usuario en modo HWID: entra con su HWID. Con minutos es temporal."""
    if not HWID_RE.match(hwid):
        return "HWID inválido: de 8 a 32 letras y números."
    if existe(hwid):
        return "Ese HWID ya está registrado."
    return _alta(hwid, hwid, limite, dias, minutos, gecos="hwid," + limpiar_etiqueta(etiqueta))


def banner_pdirect():
    """El banner del 101 (PDirect): es la "máquina" que ve el cliente."""
    try:
        for linea in open(PDIRECT_ENV, encoding="utf-8"):
            if linea.startswith("PDIRECT_BANNER="):
                return linea.split("=", 1)[1].strip() or "ZUMO"
    except FileNotFoundError:
        pass
    return "ZUMO"


def fecha_larga(exp, corta=False):
    try:
        return date.fromisoformat(exp).strftime("%d/%m" if corta else "%d/%m/%Y")
    except ValueError:
        return exp


def mensaje_cliente(u):
    """El mismo mensaje que arma el panel para copiar y mandarle al cliente. None si el usuario no existe."""
    us = usuarios()
    if u not in us:
        return None
    lim, exp = us[u]
    temp = temporales().get(u)
    cliente = hwids().get(u)
    if cliente is not None:
        vence = texto_minutos(minutos_restantes(temp)) if temp else fecha_larga(exp)
        return ("🔐 DATOS DE ACCESO\n├ ☁️ Plan: Privado\n"
                f"├ ⚙️ Máquina: {banner_pdirect()}\n├ 👤 Usuario: {cliente}\n├ ⏳ Vence: {vence}")
    vence = texto_minutos(minutos_restantes(temp)) if temp else fecha_larga(exp, corta=True)
    n = int(lim) if str(lim).isdigit() else 1
    return (f"👤 {u}\n🔒 {clave_de(u) or '(su clave)'}\n📅 {vence}\n"
            f"🔌 {'1 dispositivo' if n == 1 else f'{n} dispositivos'}\n📄 {banner_pdirect()}")


def borrar_usuario(u):
    run("pkill", "-9", "-u", u)
    run("userdel", u)
    bash_lib("zumo_db_del", u)
    clave_borrar(u)
    run("systemctl", "stop", f"zumo-temp-{u}.timer")
    temp_quitar(u)
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
MENU = [[("📱 App Android", "app")],
        [("🔗 Enlazar GitHub", "ghenl")],
        [("💾 Respaldo", "resp")],
        [("🪪 Mi ID", "id")]]
CANCELAR = [[("✖ Cancelar", "menu")]]


TIPOS = {"n": "👤 Normal", "h": "🔑 HWID", "t": "⏳ Temporal", "th": "⏳ Temporal HWID"}
VOLVER = [[("◂ Menú", "menu")]]


def teclado_minutos():
    return [[(f"{n} min", f"cm:{n}") for n in (10, 30, 60)],
            [(f"{n // 60} horas", f"cm:{n}") for n in (120, 180, 360)],
            [("✏️ Otro número", "cm:otro")],
            [("✖ Cancelar", "menu")]]


def teclado_dias(prefijo):
    return [[(f"{n} días", f"{prefijo}:{n}") for n in (7, 15, 30)],
            [(f"{n} días", f"{prefijo}:{n}") for n in (60, 90, 180)],
            [("✏️ Otro número", f"{prefijo}:otro")],
            [("✖ Cancelar", "menu")]]


class Bot(centro.CentroMixin):
    def __init__(self, tg, admins, gh=None):
        self.tg, self.admins, self.gh = tg, admins, gh
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
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") + "🛡 Zumo VPN\n¿Qué querés hacer?",
                    MENU)

    def pantalla_crear(self, chat, mid):
        self.estado.pop(chat, None)
        self.mostrar(chat, mid, "➕ Crear usuario\n¿De qué tipo?\n\n"
                                "👤 Normal: usuario y contraseña, por días.\n"
                                "🔑 HWID: entra con el HWID de su celular, por días.\n"
                                "⏳ Temporal: se borra solo cuando pasan los minutos que elijas.",
                     [[(TIPOS["n"], "ct:n"), (TIPOS["h"], "ct:h")],
                      [(TIPOS["t"], "ct:t"), (TIPOS["th"], "ct:th")],
                      [("◂ Menú", "menu")]])

    def pantalla_lista(self, chat, mid, pag):
        hw, tm = hwids(), temporales()
        us = sorted(usuarios().items(), key=lambda x: hw.get(x[0], x[0]).lower())
        if not us:
            return self.mostrar(chat, mid, "No hay usuarios todavía.", [[("➕ Crear usuario", "crear")], [("◂ Menú", "menu")]])
        hoy = date.today().isoformat()
        total = (len(us) - 1) // POR_PAGINA + 1
        pag = max(0, min(pag, total - 1))
        trozo = us[pag * POR_PAGINA:(pag + 1) * POR_PAGINA]
        botones = [[("➕ Crear usuario", "crear")]]
        for u, (_, e) in trozo:
            nombre = f"🔑 {hw[u]}" if u in hw else u
            if u in tm:
                botones.append([(f"⏳ {nombre} · {minutos_restantes(tm[u])} min", f"u:{u}")])
            else:
                botones.append([(f"{'🔴' if e < hoy else '🟢'} {nombre} · {e[5:] if len(e) == 10 else e}", f"u:{u}")])
        nav = []
        if pag > 0:
            nav.append(("‹ Anterior", f"lista:{pag - 1}"))
        if pag < total - 1:
            nav.append(("Siguiente ›", f"lista:{pag + 1}"))
        if nav:
            botones.append(nav)
        botones.append([("◂ Menú", "menu")])
        self.mostrar(chat, mid, f"👥 Usuarios (pág. {pag + 1}/{total})\n🟢 vigente · 🔴 vencido (mes-día en que vence) · 🔑 HWID · ⏳ temporal", botones)

    def pantalla_usuario(self, chat, mid, u):
        us = usuarios()
        if u not in us:
            return self.menu(chat, mid, "Ese usuario ya no existe.")
        lim, exp = us[u]
        cliente = hwids().get(u)
        temp = temporales().get(u)
        txt = f"👤 {cliente} (HWID)\nHWID: {u}" if cliente is not None else f"👤 {u}"
        if temp:
            txt += f"\n⏳ Temporal: se borra en {texto_minutos(minutos_restantes(temp))}"
        else:
            txt += f"\nVence: {exp} ({'🔴 vencido' if exp < date.today().isoformat() else '🟢 vigente'})"
        txt += f"\nLímite de conexiones: {lim}"
        botones = [[("📋 Datos para el cliente", f"d:{u}")]]
        fila = [] if temp else [("🔄 Renovar", f"r:{u}")]          # un temporal no se renueva: se borra solo
        if cliente is None:
            fila.append(("🔑 Cambiar clave", f"k:{u}"))          # la clave de un HWID es el mismo HWID
        if fila:
            botones.append(fila)
        botones.append([("🔢 Límite", f"l:{u}"), ("🗑 Borrar", f"b:{u}")])
        botones.append([("◂ Usuarios", "lista:0")])
        self.mostrar(chat, mid, txt, botones)

    def pantalla_app(self, chat, mid, aviso=""):
        l = cargar_app()
        filas = "\n".join(f"{i + 1}. {s['name']} · {s['host']}:{s['port']}{' · TLS' if s.get('tls') else ''}"
                          f"{'' if s.get('payload') else ' · directo'}" for i, s in enumerate(l))
        txt = (aviso + "\n\n" if aviso else "") + "📱 App Android\n" + (
            f"Servidores en la app ({len(l)}):\n{filas}" if l else
            "Todavía no cargaste servidores en el bot.\nAl compilar sin servidores, la app usa lo que ya haya en el secreto ZUMO_SERVIDORES del repo.")
        botones = [[(f"{i + 1}. {s['name']}", f"a:{i}")] for i, s in enumerate(l)]
        botones += [[("➕ Agregar servidor", "aadd"), ("📥 Pegar lista", "apegar")],
                    [("🎨 Apariencia de la app", "t")],
                    [(self.etiqueta_compilar(), "acomp")],
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
        self.tg.mensaje(chat, texto, datos.get("_extra", []) + CANCELAR)

    def enviar_datos(self, chat, u):
        """Manda, en un mensaje aparte, los datos listos para reenviarle al cliente."""
        m = mensaje_cliente(u)
        if m:
            self.tg.mensaje(chat, m)

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
            if msg.get("document") and self.documento_resp(chat, msg):
                return
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
        if self.boton_resp(chat, mid, acc):
            return
        if acc == "lista":
            self.estado.pop(chat, None)
            return self.pantalla_lista(chat, mid, int(arg or 0))
        if acc == "u":
            return self.pantalla_usuario(chat, mid, arg)
        if acc == "crear":
            return self.pantalla_crear(chat, mid)
        if acc == "ct":      # tipo de usuario elegido
            if arg not in TIPOS:
                return self.pantalla_crear(chat, mid)
            if arg in ("h", "th"):
                return self.pedir(chat, f"➕ Nuevo usuario · {TIPOS[arg]}\n\nEscribí el nombre del cliente (solo para identificarlo, no es lo que usa para entrar):", "c_etq", tipo=arg)
            return self.pedir(chat, f"➕ Nuevo usuario · {TIPOS[arg]}\n\nEscribí el nombre de usuario (empieza con letra, solo letras y números, máx. 10):", "c_user", tipo=arg)
        if acc in ("cd", "cm", "cl"):      # días, minutos o límite elegidos al crear
            if chat not in self.estado or "tipo" not in self.estado[chat]:
                return self.menu(chat, mid, "Eso quedó a medias. Empezá de nuevo desde Crear usuario.")
            if arg == "otro" and acc != "cl":
                self.estado[chat]["paso"] = "c_dias" if acc == "cd" else "c_min"
                return self.tg.mensaje(chat, "Escribí la cantidad de días:" if acc == "cd" else f"Escribí los minutos (1 a {MIN_MAX}):", CANCELAR)
            temporal = self.estado[chat]["tipo"] in ("t", "th")
            if not arg.isdigit() or (acc == "cd" and temporal) or (acc == "cm" and not temporal):
                return self.menu(chat, mid)      # botón de otro alta anterior
            if acc == "cd":
                return self.crear_limite(chat, dias=int(arg))
            if acc == "cm":
                return self.crear_minutos(chat, int(arg))
            return self.crear_final(chat, int(arg))
        if acc == "d":
            if arg not in usuarios():
                return self.menu(chat, mid, "Ese usuario ya no existe.")
            return self.enviar_datos(chat, arg)
        if acc == "r":
            return self.mostrar(chat, mid, f"🔄 Renovar {arg}\n¿Por cuántos días desde hoy?", teclado_dias(f"rd:{arg}"))
        if acc == "rd":
            u, _, n = arg.rpartition(":")
            if n == "otro":
                return self.pedir(chat, f"Escribí los días para renovar a {u}:", "r_dias", u=u)
            return self.renovar(chat, u, int(n))
        if acc == "k":
            if arg in hwids():
                return self.pantalla_usuario(chat, mid, arg)
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
        if acc == "t":
            return self.boton_tema(chat, mid, arg)
        if acc in ("app", "a", "aadd", "apegar", "acomp", "acomp_si", "aclave", "aclave_si", "ap", "ah", "an", "at", "as", "ab", "abs", "asp", "aqp"):
            return self.boton_app(chat, mid, acc, arg)
        return self.menu(chat, mid)      # botón de una versión anterior del bot

    def boton_app(self, chat, mid, acc, arg):
        e = self.estado.pop(chat, None)
        if acc == "asp":      # servidor nuevo sin payload: se conecta directo por IP y puerto
            if not e or e.get("paso") != "a_payload_nuevo":
                return self.pantalla_app(chat, mid)
            l = cargar_app()
            l.append(srv.nuevo(e["nombre"], e["host"], e["port"], ""))
            guardar_app(l)
            return self.pantalla_app_servidor(chat, None, len(l) - 1)
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
                                [[("✅ Compilar en GitHub", "acomp_si"), ("✖ No", "app")]])
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
        if acc == "aqp":
            l[i]["payload"] = ""
            guardar_app(l)
            return self.pantalla_app_servidor(chat, mid, i)
        if acc == "ap":
            return self.pedir(chat, f"📝 Pegá el payload nuevo de {s['name']} en un solo mensaje (podés usar [crlf], [host], [split]...).\n"
                                    "Si cambiás solo el payload, no le cambies el nombre: así a los clientes les sigue andando al actualizar.\n"
                                    "Para quitarlo (conexión directa por IP y puerto) tocá el botón o escribí -", "a_payload", i=i, _extra=[[("🚫 Quitar payload", f"aqp:{i}")]])
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

    # -- texto escrito
    def texto_libre(self, chat, t):
        e = self.estado[chat]
        paso = e["paso"]
        if paso == "c_etq":
            e.update(paso="c_hwid", etq=limpiar_etiqueta(t))
            return self.tg.mensaje(chat, f"Cliente: {e['etq']}\nAhora pegá el HWID del cliente (8 a 32 letras y números):", CANCELAR)
        if paso == "c_hwid":
            h = limpiar_hwid(t)
            if not HWID_RE.match(h):
                return self.tg.mensaje(chat, f"⚠️ HWID inválido: tienen que quedar de 8 a 32 letras y números (quedaron {len(h)}). Pegalo de nuevo:", CANCELAR)
            if existe(h):
                return self.tg.mensaje(chat, "⚠️ Ese HWID ya está registrado. Pegá otro:", CANCELAR)
            e.update(hwid=h)
            return self.pedir_duracion(chat, e, f"HWID: {h}\n")
        if paso == "c_user":
            if not NOMBRE_RE.match(t):
                return self.tg.mensaje(chat, "⚠️ Nombre inválido. Probá de nuevo (empieza con letra, solo letras y números, máx. 10):", CANCELAR)
            if existe(t):
                return self.tg.mensaje(chat, "⚠️ Ese usuario ya existe. Escribí otro nombre:", CANCELAR)
            e.update(paso="c_clave", u=t)
            return self.tg.mensaje(chat, f"Usuario: {t}\nAhora la contraseña (letras y números, 1 a 10):", CANCELAR)
        if paso == "c_clave":
            if not CLAVE_RE.match(t):
                return self.tg.mensaje(chat, "⚠️ Contraseña inválida. Solo letras y números, de 1 a 10:", CANCELAR)
            e.update(clave=t)
            return self.pedir_duracion(chat, e)
        if paso == "c_dias":
            if not (t.isdigit() and 1 <= int(t) <= 3650):
                return self.tg.mensaje(chat, "⚠️ Escribí un número de días (1 a 3650):", CANCELAR)
            return self.crear_limite(chat, dias=int(t))
        if paso == "c_min":
            if not (t.isdigit() and 1 <= int(t) <= MIN_MAX):
                return self.tg.mensaje(chat, f"⚠️ Escribí un número de minutos (1 a {MIN_MAX}):", CANCELAR)
            return self.crear_minutos(chat, int(t))
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
            self.tg.mensaje(chat, f"✅ Contraseña de {u} cambiada. Los datos para el cliente:")
            self.enviar_datos(chat, u)
            return self.tg.mensaje(chat, "¿Algo más?", [[("👤 " + u, f"u:{u}")], [("◂ Menú", "menu")]])
        if self.texto_resp(chat, e, paso, t):
            return
        if paso.startswith("a_"):
            return self.texto_app(chat, e, paso, t)
        if paso.startswith("t_"):
            return self.texto_tema(chat, e, paso, t)
        return self.menu(chat)

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
            return self.tg.mensaje(chat, "Ahora pegá el payload en un solo mensaje (podés usar [crlf], [host], [split]...).\n"
                                         "Si no usa payload, la app se conecta directo por IP y puerto: tocá el botón.",
                                   [[("➡️ Sin payload (directo)", "asp")]] + CANCELAR)
        if paso == "a_payload_nuevo":
            l.append(srv.nuevo(e["nombre"], e["host"], e["port"], "" if t.strip() in ("-", "") else t))
            guardar_app(l)
            self.estado.pop(chat, None)
            self.borrar_entrada(chat, e)
            return self.pantalla_app_servidor(chat, None, len(l) - 1)
        if paso == "a_payload":
            l[i]["payload"] = "" if t.strip() == "-" else srv.limpiar_linea(t)
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

    def etiqueta_compilar(self):
        return "🔨 Compilar en GitHub y enviarme el APK"

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
        linea = f"🔨 Compilando en GitHub… {barra} {pct}%  (paso {done}/{total})"
        if actual:
            linea += f"\n⚙️ {actual}"
        return linea + f"\n⏱ {minutos} min"

    def _asegurar_clave(self, chat, solo_traer=False):
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
        if solo_traer:
            if centro.respaldo.clave_firma():
                os.rename(centro.respaldo.FIRMA, centro.respaldo.FIRMA + ".ant-" + time.strftime("%Y%m%d%H%M%S"))
            centro.respaldo.guardar_clave_firma(jks, ks_pass)
            return self.tg.mensaje(chat, "✅ Clave de firma traída de GitHub y guardada en este servidor. "
                                         "Ahora podés exportarla (📤) y mandarla a la VPS nueva.",
                                   [[("📤 Exportar clave", "kexp")], [("◂ Respaldo", "resp")]])
        gh.subir_secreto("ZUMO_KEYSTORE_B64", base64.b64encode(jks).decode())
        gh.subir_secreto("ZUMO_KS_PASS", ks_pass)
        self.tg.mensaje(chat, "✅ Clave de firma guardada como secreto fijo del repo (ZUMO_KEYSTORE_B64 y ZUMO_KS_PASS). "
                              "La app sigue firmada con la misma clave de siempre. Ya no depende del caché.",
                        [[("📱 App Android", "app")], [("◂ Menú", "menu")]])

    def _compilar(self, chat, asegurar=False):
        try:
            gh = self.gh
            if asegurar:
                return self._asegurar_clave(chat, solo_traer=(asegurar == "traer"))
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
                self.tg.documento(chat, "zumo-vpn.apk", apk, f"✅ Compilación {n} en GitHub · {len(lista)} servidor(es)")
                self.tg.mensaje(chat, "✅ Listo. Instalá el APK encima de la versión anterior: se actualiza sin perder nada.",
                                [[("📱 App Android", "app")], [("◂ Menú", "menu")]])
            else:
                cola = ""
                try:
                    log = gh.archivo_de_rama("compilacion.log").decode("utf-8", "replace").strip().splitlines()
                    cola = "\n".join(log[-25:])
                except compilar.ErrorGitHub:
                    pass
                self.tg.mensaje(chat, f"❌ La compilación en GitHub falló ({r.get('conclusion')}).\n{cola}\n\n{r.get('html_url', '')}",
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
            [(self.etiqueta_compilar(), "acomp")],
            [("♻️ Volver al diseño original", "t:rs")],
            [("◂ App Android", "app")]])

    def enviar_vista(self, chat, t=None, leyenda="", botones=None):
        """Manda la imagen de cómo queda la app. Sin Pillow en la VPS, avisa cómo instalarlo."""
        botones = botones or [[("🎨 Seguir cambiando", "t")], [(self.etiqueta_compilar(), "acomp")]]
        if not vista.HAY_PIL:
            return self.tg.mensaje(chat, "👁 Para ver las vistas previas falta una herramienta en la VPS. Corré de nuevo el instalador del bot "
                                         "(o: apt install -y python3-pil fonts-dejavu-core && systemctl restart zumo-bot).", botones)
        t = t or cargar_tema()
        icono = imagen_marca("icono.png")
        img = vista.captura(t, icono, imagen_marca("fondo.jpg"))
        self.tg.foto(chat, img, (leyenda + "\n\n" if leyenda else "") + self.notas_vista(t, icono), botones)

    def notas_vista(self, t, icono=None, menu=False):
        """Pie de la vista previa: lo que esta imagen no puede mostrar tal cual, para que no parezca que no cambió."""
        notas = [self.NOTA_VISTA]
        if t["fuente"] in vista.APROXIMADAS:
            notas.append(f"ℹ️ La letra «{dict(T.FUENTES)[t['fuente']]}» acá es solo una aproximación (inclinada o más ancha): en el teléfono se ve con su letra real.")
        if t["logo"] and not (t["logo_imagen"] and icono) and not vista.HAY_EMOJI and not menu:
            notas.append(f"ℹ️ Tu emoji {t['logo']} no se puede dibujar acá: se muestra un escudo de muestra. En la app sí se ve tu emoji. "
                         "Para verlo en la vista previa: apt install -y fonts-noto-color-emoji && systemctl restart zumo-bot")
        return "\n".join(notas)

    def enviar_vista_menu(self, chat):
        """Manda cómo queda el menú ☰ de la app (contactos, importar cuenta, evitar desconexiones)."""
        botones = [[("📋 Menús y secciones", "t:me")], [("🎨 Apariencia", "t")]]
        if not vista.HAY_PIL:
            return self.tg.mensaje(chat, "👁 Para ver las vistas previas falta una herramienta en la VPS. Corré de nuevo el instalador del bot "
                                         "(o: apt install -y python3-pil fonts-dejavu-core && systemctl restart zumo-bot).", botones)
        t = cargar_tema()
        img = vista.captura_menu(t, imagen_marca("icono.png"), imagen_marca("fondo.jpg"))
        hay = "con tus botones de contacto" if t["enlaces"] else "sin botones de contacto (agregalos en «Menús y secciones»)"
        self.tg.foto(chat, img, f"👁 El menú ☰ de la app, {hay}.\n\n" + self.notas_vista(t, menu=True), botones)

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
        botones += [[("👁 Pantalla principal", "t:v"), ("👁 Menú ☰", "t:vm")], [("◂ Apariencia", "t")]]
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") +
                     f"📋 Menús y secciones\n\nQué se ve en la app (tocá para mostrar u ocultar):\n{secciones}\n\n"
                     f"Esquinas de las tarjetas y botones: {dict(T.RADIOS).get(t['radio'], str(t['radio']))}\n\n"
                     f"Botones de contacto del menú ☰ (WhatsApp, Telegram, tu web):\n{contactos}\n\n"
                     "Los contactos y «Importar .zs» están en el menú ☰, no en la pantalla principal: para verlos tocá «Menú ☰».", botones)

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
        if acc == "vm":
            return self.enviar_vista_menu(chat)
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
    def pedir_duracion(self, chat, e, antes=""):
        """Los temporales duran minutos; el resto, días."""
        if e["tipo"] in ("t", "th"):
            e["paso"] = "c_min_btn"
            return self.tg.mensaje(chat, antes + "¿Cuántos minutos dura?", teclado_minutos())
        e["paso"] = "c_dias_btn"
        self.tg.mensaje(chat, antes + "¿Cuántos días de duración?", teclado_dias("cd"))

    def crear_minutos(self, chat, minutos):
        if not 1 <= minutos <= MIN_MAX:
            return self.tg.mensaje(chat, f"⚠️ Los minutos van de 1 a {MIN_MAX}.", CANCELAR)
        if self.estado[chat]["tipo"] == "th":      # temporal HWID: una sola conexión, como en el panel
            self.estado[chat]["minutos"] = minutos
            return self.crear_final(chat, 1)
        self.crear_limite(chat, minutos=minutos)

    def crear_limite(self, chat, dias=None, minutos=None):
        e = self.estado[chat]
        e.update(paso="c_lim_btn", dias=dias, minutos=minutos)
        cuanto = texto_minutos(minutos) if minutos else f"{dias} días"
        nota = "\n(En HWID lo habitual es 2.)" if e["tipo"] == "h" else ""
        self.tg.mensaje(chat, f"{cuanto}. ¿Cuántas conexiones a la vez (dispositivos)?{nota}",
                        [[(str(n), f"cl:{n}") for n in (1, 2, 3, 5)], [("✖ Cancelar", "menu")]])

    def crear_final(self, chat, limite):
        e = self.estado.pop(chat, None)
        if not e or not (e.get("dias") or e.get("minutos")):
            return self.menu(chat)
        dias, minutos = e.get("dias"), e.get("minutos")
        if e["tipo"] in ("h", "th"):
            u = e["hwid"]
            err = crear_hwid(u, e["etq"], dias, limite, minutos)
            quien = f"{limpiar_etiqueta(e['etq'])} (HWID {u})"
        else:
            u = e["u"]
            err = crear_usuario(u, e["clave"], dias, limite, minutos)
            quien = u
        if err and not isinstance(err, Aviso):
            return self.tg.mensaje(chat, "⚠️ " + err, VOLVER)
        cuanto = f"temporal, {texto_minutos(minutos)}" if minutos else f"{dias} días"
        self.tg.mensaje(chat, f"✅ Usuario {quien} creado ({cuanto}, {limite} conexión/es). Los datos para el cliente:")
        self.enviar_datos(chat, u)
        if err:      # quedó creado, pero no se pudo agendar el borrado
            self.tg.mensaje(chat, "⚠️ " + err)
        self.tg.mensaje(chat, "¿Algo más?", [[("👤 " + (e.get("etq") or u), f"u:{u}")], [("➕ Crear otro", "crear")], [("◂ Menú", "menu")]])

    def renovar(self, chat, u, dias):
        self.estado.pop(chat, None)
        if u not in usuarios():
            return self.menu(chat, None, "Ese usuario no está en el panel.")
        exp = renovar_usuario(u, dias)
        nombre = hwids().get(u, u)
        self.tg.mensaje(chat, f"✅ {nombre} vence el {exp}. Los datos para el cliente:")
        self.enviar_datos(chat, u)
        self.tg.mensaje(chat, "¿Algo más?", [[("👤 " + nombre, f"u:{u}")], [("◂ Menú", "menu")]])


def main():
    env = leer_env()
    token = os.environ.get("BOT_TOKEN") or env.get("BOT_TOKEN")
    if not token:
        sys.exit("Falta BOT_TOKEN en " + ENV)
    admins = {int(x) for x in re.split(r"[,\s]+", env.get("ADMINS", "")) if x.strip().lstrip("-").isdigit()}
    gh = None
    if env.get("GITHUB_TOKEN"):
        gh = compilar.GitHub(env["GITHUB_TOKEN"], env.get("GITHUB_REPO") or "adri40606941-ui/Zumo",
                             rama=env.get("GITHUB_REF") or "main")
    bot = Bot(Telegram(token), admins, gh)
    bot.leer_env_fn = leer_env
    centro.ENV = ENV
    threading.Thread(target=bot.respaldo_diario, daemon=True).start()
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
