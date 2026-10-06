#!/usr/bin/env python3
"""Bot de Telegram para administrar cuentas de Zumo VPN desde el teléfono.

Se maneja solo con botones (inline). Corre en la VPS como root y crea usuarios SSH reales, igual que el panel:
normales, HWID y temporales. Al crear o renovar manda el mensaje con los datos para pasarle al cliente.
Solo responde a los IDs de ADMINS.

Configuración: /etc/zumo/bot.env  (BOT_TOKEN, ADMINS)
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
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compilar  # noqa: E402
import servidores as srv  # noqa: E402

ENV = os.environ.get("ZUMO_BOT_ENV", "/etc/zumo/bot.env")
APPSRV = os.environ.get("ZUMO_APP_SERVIDORES", "/etc/zumo/app-servidores.json")
DB = os.environ.get("ZUMO_DB", "/etc/zumo/usuarios.db")
CLAVES = os.environ.get("ZUMO_CLAVES", "/etc/zumo/claves.db")
LIB = os.environ.get("ZUMO_LIB", "/etc/zumo/zumo-lib.sh")
LIMCONF = os.environ.get("ZUMO_LIMCONF", "/etc/zumo/limit.conf")
ZUMOID = os.environ.get("ZUMO_ZUMOID", "/usr/local/bin/zumoid")  # control de dispositivo (Android ID)
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
command -v zumo_disp_forget >/dev/null 2>&1 && zumo_disp_forget "$u"
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
        [("📱 App Android", "app")],
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


class Bot:
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
        n = len(usuarios())
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") + f"🛡 Zumo VPN · {n} usuario(s)\n¿Qué querés hacer?", MENU)

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
        if acc in ("app", "a", "aadd", "apegar", "acomp", "acomp_si", "aclave", "aclave_si", "ap", "ah", "an", "at", "as", "ab", "abs"):
            return self.boton_app(chat, mid, acc, arg)
        return self.menu(chat, mid)      # botón de una versión anterior del bot

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
        if paso.startswith("a_"):
            return self.texto_app(chat, e, paso, t)
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
