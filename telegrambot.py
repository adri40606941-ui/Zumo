#!/usr/bin/env python3
# Bot de Telegram para administrar usuarios del panel ZUMO.
# Solo responde a los IDs de Telegram listados en ADMIN_IDS (/etc/zumo/telegram.conf).
import json
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

CONF_PATH = "/etc/zumo/telegram.conf"
DB_PATH = "/etc/zumo/usuarios.db"

def cargar_config():
    conf = {}
    for line in Path(CONF_PATH).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        conf[k.strip()] = v.strip()
    conf["ADMIN_IDS"] = {int(x) for x in conf.get("ADMIN_IDS", "").split(",") if x.strip()}
    return conf

CONF = cargar_config()
TOKEN = CONF["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

# --- estado de conversaciones en curso (crear usuario paso a paso) ---
ESTADOS = {}  # chat_id -> dict con el paso actual y datos acumulados

def api(method, **params):
    r = requests.post(f"{API}/{method}", json=params, timeout=35)
    r.raise_for_status()
    return r.json()

def enviar(chat_id, texto, teclado=None, reply_markup=None):
    kwargs = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}
    if reply_markup is not None:
        kwargs["reply_markup"] = json.dumps(reply_markup)
    elif teclado is not None:
        kwargs["reply_markup"] = json.dumps(teclado)
    return api("sendMessage", **kwargs)

def responder_callback(callback_id, texto=None):
    kwargs = {"callback_query_id": callback_id}
    if texto:
        kwargs["text"] = texto
    api("answerCallbackQuery", **kwargs)

def menu_principal():
    return {
        "keyboard": [
            [{"text": "/crear"}, {"text": "/eliminar"}],
            [{"text": "/usuarios"}, {"text": "/vencidos"}],
        ],
        "resize_keyboard": True,
    }

def es_admin(user_id):
    return user_id in CONF["ADMIN_IDS"]

def leer_usuarios():
    if not Path(DB_PATH).exists():
        return []
    out = []
    for line in Path(DB_PATH).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        partes = line.split(":")
        if len(partes) != 3:
            continue
        out.append({"usuario": partes[0], "limite": partes[1], "exp": partes[2]})
    return out

def etiqueta_de(usuario):
    # Si el usuario fue creado en modo HWID, el campo GECOS guarda "hwid,<cliente>".
    try:
        r = subprocess.run(["getent", "passwd", usuario], capture_output=True, text=True, timeout=5)
        gecos = r.stdout.strip().split(":")[4] if r.returncode == 0 else ""
    except Exception:
        gecos = ""
    if gecos.startswith("hwid,"):
        return gecos.split(",", 1)[1], True
    return usuario, False

def en_linea(usuario):
    try:
        r = subprocess.run(["ps", "-u", usuario, "-o", "comm="], capture_output=True, text=True, timeout=5)
    except Exception:
        return 0
    return sum(1 for l in r.stdout.splitlines() if l.strip() == "sshd")

def dias_restantes(exp):
    try:
        d = (datetime.strptime(exp, "%Y-%m-%d") - datetime.now()).days
    except ValueError:
        return "?"
    if d < 0:
        return "vencido"
    if d == 0:
        return "vence hoy"
    return f"{d}d"

def crear_usuario(usuario, password, dias, limite, etiqueta=None):
    """Si etiqueta no es None, crea en modo HWID: 'usuario' es el ID pegado por el
    cliente (se usa como username y password), y 'etiqueta' es el nombre del cliente
    guardado en el GECOS (-c hwid,<etiqueta>) solo para mostrarlo en el panel/bot."""
    if etiqueta is None and not re.match(r"^[a-z_][a-z0-9_-]*$", usuario):
        return False, "Nombre de usuario inválido (minúsculas, sin espacios)."
    r = subprocess.run(["id", usuario], capture_output=True)
    if r.returncode == 0:
        return False, "Ese usuario ya existe."
    exp = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
    cmd = ["useradd", "-M", "-s", "/bin/false", "-e", exp]
    if etiqueta is not None:
        cmd += ["--badname", "-c", f"hwid,{etiqueta}"]
    cmd.append(usuario)
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        subprocess.run(
            ["chpasswd"], input=f"{usuario}:{password}\n".encode(), check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        return False, f"Falló al crear el usuario: {e.stderr.decode(errors='ignore')[:200]}"
    with open(DB_PATH, "a") as f:
        f.write(f"{usuario}:{limite}:{exp}\n")
    return True, exp

def eliminar_usuario(usuario):
    subprocess.run(["pkill", "-9", "-u", usuario], capture_output=True)
    subprocess.run(["userdel", usuario], capture_output=True)
    if Path(DB_PATH).exists():
        lineas = [l for l in Path(DB_PATH).read_text().splitlines() if not l.startswith(usuario + ":")]
        Path(DB_PATH).write_text("\n".join(lineas) + ("\n" if lineas else ""))

# ---------- comandos ----------

def cmd_start(chat_id):
    enviar(
        chat_id,
        "🟣 <b>Panel ZUMO</b>\nElegí una opción:",
        teclado=menu_principal(),
    )

def texto_usuarios():
    usuarios = leer_usuarios()
    if not usuarios:
        return "No hay usuarios registrados."
    lineas = ["📋 <b>Usuarios registrados</b>\n"]
    for u in usuarios:
        etiqueta, es_hwid = etiqueta_de(u["usuario"])
        on = en_linea(u["usuario"])
        estado = f"🟢 online({on})" if on > 0 else "⚪ offline"
        tag = f"{etiqueta} (HWID)" if es_hwid else etiqueta
        lineas.append(f"• <code>{tag}</code> — {estado} — límite {u['limite']} — {dias_restantes(u['exp'])}")
    return "\n".join(lineas)

def cmd_usuarios(chat_id):
    enviar(
        chat_id,
        texto_usuarios(),
        reply_markup={"inline_keyboard": [[{"text": "🔄 Actualizar", "callback_data": "refrescar_usuarios"}]]},
    )

def cmd_vencidos(chat_id):
    usuarios = [u for u in leer_usuarios() if dias_restantes(u["exp"]) == "vencido"]
    if not usuarios:
        enviar(chat_id, "✅ No hay usuarios vencidos.")
        return
    botones = [[{"text": f"✖ Borrar {u['usuario']}", "callback_data": f"delv:{u['usuario']}"}] for u in usuarios]
    nombres = "\n".join(f"• {u['usuario']} (venció {u['exp']})" for u in usuarios)
    enviar(chat_id, f"⚠️ <b>Usuarios vencidos</b>\n{nombres}", reply_markup={"inline_keyboard": botones})

def cmd_eliminar(chat_id):
    usuarios = leer_usuarios()
    if not usuarios:
        enviar(chat_id, "No hay usuarios registrados.")
        return
    botones = [[{"text": u["usuario"], "callback_data": f"del:{u['usuario']}"}] for u in usuarios]
    enviar(chat_id, "Elegí el usuario a eliminar:", reply_markup={"inline_keyboard": botones})

def cmd_crear_iniciar(chat_id):
    ESTADOS[chat_id] = {"paso": "modo", "datos": {}}
    enviar(
        chat_id,
        "¿Qué modo de usuario querés crear?",
        reply_markup={
            "inline_keyboard": [
                [{"text": "● Normal", "callback_data": "modo:normal"}],
                [{"text": "🔑 HWID (ID único = usuario y contraseña)", "callback_data": "modo:hwid"}],
            ]
        },
    )

def continuar_creacion(chat_id, texto):
    estado = ESTADOS[chat_id]
    paso = estado["paso"]
    datos = estado["datos"]

    if paso == "usuario":
        if not re.match(r"^[a-z_][a-z0-9_-]*$", texto):
            enviar(chat_id, "Nombre inválido. Usá minúsculas, sin espacios. Probá de nuevo:")
            return
        datos["usuario"] = texto
        estado["paso"] = "password"
        enviar(chat_id, "Ahora la <b>contraseña</b>:")
    elif paso == "password":
        if not texto:
            enviar(chat_id, "La contraseña no puede estar vacía. Probá de nuevo:")
            return
        datos["password"] = texto
        estado["paso"] = "dias"
        enviar(chat_id, "¿Cuántos <b>días</b> de duración?")
    elif paso == "etiqueta":
        datos["etiqueta"] = texto.strip() or "cliente"
        estado["paso"] = "hwid"
        enviar(chat_id, "Pegá el <b>HWID</b> del cliente (8 a 32 caracteres):")
    elif paso == "hwid":
        hwid = re.sub(r"[^A-Za-z0-9]", "", texto)
        if not (8 <= len(hwid) <= 32):
            enviar(chat_id, f"HWID inválido (8 a 32 caracteres alfanuméricos; quedaron {len(hwid)}). Probá de nuevo:")
            return
        datos["usuario"] = hwid
        datos["password"] = hwid
        estado["paso"] = "dias"
        enviar(chat_id, "¿Cuántos <b>días</b> de duración?")
    elif paso == "dias":
        if not texto.isdigit():
            enviar(chat_id, "Tiene que ser un número. ¿Cuántos días?")
            return
        datos["dias"] = int(texto)
        estado["paso"] = "limite"
        enviar(chat_id, "¿<b>Límite</b> de conexiones simultáneas? (ej: 1)")
    elif paso == "limite":
        if not texto.isdigit() or int(texto) < 1:
            enviar(chat_id, "Tiene que ser un número mayor a 0. ¿Límite de conexiones?")
            return
        datos["limite"] = int(texto)
        ok, resultado = crear_usuario(
            datos["usuario"], datos["password"], datos["dias"], datos["limite"],
            etiqueta=datos.get("etiqueta"),
        )
        if ok:
            if datos.get("etiqueta"):
                enviar(
                    chat_id,
                    "✅ <b>Usuario HWID creado</b>\n"
                    f"Cliente: <code>{datos['etiqueta']}</code>\n"
                    f"HWID (usuario y contraseña): <code>{datos['usuario']}</code>\n"
                    f"Vence: {resultado}\n"
                    f"Límite: {datos['limite']}",
                    teclado=menu_principal(),
                )
            else:
                enviar(
                    chat_id,
                    "✅ <b>Usuario creado</b>\n"
                    f"Usuario: <code>{datos['usuario']}</code>\n"
                    f"Contraseña: <code>{datos['password']}</code>\n"
                    f"Vence: {resultado}\n"
                    f"Límite: {datos['limite']}",
                    teclado=menu_principal(),
                )
        else:
            enviar(chat_id, f"✘ {resultado}", teclado=menu_principal())
        del ESTADOS[chat_id]

# ---------- loop principal ----------

def manejar_mensaje(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    if not es_admin(user_id):
        enviar(chat_id, "No autorizado.")
        return
    texto = (msg.get("text") or "").strip()

    if chat_id in ESTADOS and not texto.startswith("/"):
        continuar_creacion(chat_id, texto)
        return

    if texto in ("/start", "/ayuda", "/help"):
        cmd_start(chat_id)
    elif texto == "/crear":
        cmd_crear_iniciar(chat_id)
    elif texto == "/eliminar":
        cmd_eliminar(chat_id)
    elif texto == "/usuarios":
        cmd_usuarios(chat_id)
    elif texto == "/vencidos":
        cmd_vencidos(chat_id)
    else:
        enviar(chat_id, "No entendí ese comando.", teclado=menu_principal())

def manejar_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    user_id = cq["from"]["id"]
    data = cq.get("data", "")
    if not es_admin(user_id):
        responder_callback(cq["id"], "No autorizado")
        return
    if data.startswith("del:") or data.startswith("delv:"):
        usuario = data.split(":", 1)[1]
        eliminar_usuario(usuario)
        responder_callback(cq["id"], f"{usuario} eliminado")
        enviar(chat_id, f"✅ Usuario <code>{usuario}</code> eliminado.", teclado=menu_principal())
    elif data == "modo:normal":
        responder_callback(cq["id"])
        ESTADOS[chat_id] = {"paso": "usuario", "datos": {}}
        enviar(chat_id, "Escribí el <b>nombre de usuario</b> (minúsculas, sin espacios):")
    elif data == "modo:hwid":
        responder_callback(cq["id"])
        ESTADOS[chat_id] = {"paso": "etiqueta", "datos": {}}
        enviar(chat_id, "Nombre del cliente (solo para identificarlo en el panel):")
    elif data == "refrescar_usuarios":
        responder_callback(cq["id"], "Actualizado")
        try:
            api(
                "editMessageText",
                chat_id=chat_id,
                message_id=cq["message"]["message_id"],
                text=texto_usuarios(),
                parse_mode="HTML",
                reply_markup=json.dumps(
                    {"inline_keyboard": [[{"text": "🔄 Actualizar", "callback_data": "refrescar_usuarios"}]]}
                ),
            )
        except Exception:
            pass
    else:
        responder_callback(cq["id"])

def main():
    print(f"Bot ZUMO iniciado. Admins: {CONF['ADMIN_IDS']}")
    offset = 0
    while True:
        try:
            resp = api("getUpdates", offset=offset, timeout=30)
        except Exception as e:
            print(f"Error consultando Telegram: {e}")
            time.sleep(5)
            continue
        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            try:
                if "message" in update:
                    manejar_mensaje(update["message"])
                elif "callback_query" in update:
                    manejar_callback(update["callback_query"])
            except Exception as e:
                print(f"Error procesando update: {e}")

if __name__ == "__main__":
    main()
