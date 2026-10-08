"""Pantallas del bot para la sección 🖥 Máquinas (las VPS que el bot maneja por SSH).

Se mezcla en la clase Bot de zumo-bot.py (usa self.tg, self.estado, self.mostrar, self.pedir...).
La lógica de SSH, cifrado y protocolos está en maquinas.py.
"""
import re

import maquinas as mq

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USUARIO_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
NOMBRE_RE = re.compile(r"^[\w .-]{1,20}$")
ICONO = {"activo": "🟢", "parcial": "🟡", "inactivo": "🔴", "no instalado": "⚪"}
VOLVER_MAQ = [[("◂ Máquinas", "maq")]]


def _barra(parte, total, ancho=10):
    if not total:
        return "░" * ancho
    n = max(0, min(ancho, round(ancho * parte / total)))
    return "█" * n + "░" * (ancho - n)


def _gb(mb):
    return f"{mb / 1024:.1f}"


class MaquinasMixin:

    # ------------------------------------------------------------------ pantallas
    def pantalla_maquinas(self, chat, mid, aviso=""):
        try:
            lista = mq.cargar()
        except mq.ErrorMaquina as e:
            return self.mostrar(chat, mid, f"⚠️ {e}", [[("◂ Menú", "menu")]])
        txt = (aviso + "\n\n" if aviso else "") + f"🖥 Máquinas · {len(lista)}\n"
        if not lista:
            txt += "\nTodavía no agregaste ninguna VPS. Agregá una con ➕ (IP, puerto, usuario y contraseña)."
        else:
            txt += "Tocá una para ver sus recursos y prender o apagar sus protocolos."
        botones = [[(f"🖥 {m['nombre']} · {m['host']}", f"maq:{m['id']}")] for m in lista]
        botones.append([("➕ Agregar VPS", "maq_add")])
        botones.append([("◂ Menú", "menu")])
        self.mostrar(chat, mid, txt, botones)

    def pantalla_maquina(self, chat, mid, mid_maq, aviso=""):
        try:
            m = mq.buscar(mid_maq)
        except mq.ErrorMaquina as e:
            return self.mostrar(chat, mid, f"⚠️ {e}", VOLVER_MAQ)
        if not m:
            return self.pantalla_maquinas(chat, mid, "Esa máquina ya no está.")
        cab = (aviso + "\n\n" if aviso else "") + f"🖥 {m['nombre']} · {m['host']}:{m['puerto']} · {m['usuario']}\n"
        try:
            r = mq.recursos(m)
        except mq.ErrorMaquina as e:
            cab += f"\n⚠️ {e}"
            return self.mostrar(chat, mid, cab, [[("🔄 Reintentar", f"maq:{m['id']}")],
                                                 [("🗑 Quitar", f"maqq:{m['id']}")], [("◂ Máquinas", "maq")]])
        lineas = [cab, f"🕒 {r['activo'] or 'uptime desconocido'}"]
        if r["cpu"] is not None:
            cores = f" ({r['cores']} núcleos)" if r["cores"] else ""
            lineas.append(f"CPU    {_barra(r['cpu'], 100)}  {r['cpu']:.0f}%{cores}")
        if r["ram"]:
            usada, total = r["ram"]
            lineas.append(f"RAM    {_barra(usada, total)}  {_gb(usada)} / {_gb(total)} GB")
        if r["disco"]:
            usado, total = r["disco"]
            lineas.append(f"Disco  {_barra(usado, total)}  {_gb(usado)} / {_gb(total)} GB")
        if r["carga"]:
            lineas.append(f"Carga  {r['carga']}")
        if r["sesiones"] is not None:
            lineas.append(f"Sesiones SSH abiertas: {r['sesiones']}")
        botones = [[("🔄 Actualizar", f"maq:{m['id']}"), ("🔌 Protocolos", f"maqp:{m['id']}")],
                   [("🗑 Quitar", f"maqq:{m['id']}")],
                   [("◂ Máquinas", "maq")]]
        self.mostrar(chat, mid, "\n".join(lineas), botones)

    def pantalla_protocolos(self, chat, mid, mid_maq, aviso=""):
        try:
            m = mq.buscar(mid_maq)
            if not m:
                return self.pantalla_maquinas(chat, mid, "Esa máquina ya no está.")
            estado = mq.estado_protocolos(m)
        except mq.ErrorMaquina as e:
            return self.mostrar(chat, mid, f"⚠️ {e}", [[("🔄 Reintentar", f"maqp:{mid_maq}")],
                                                       [("◂ Máquina", f"maq:{mid_maq}")]])
        txt = (aviso + "\n\n" if aviso else "") + f"🔌 Protocolos de {m['nombre']}\n"
        txt += "Tocá un protocolo para prenderlo o apagarlo. Los que dicen 'no instalado' se instalan desde el panel.\n"
        botones = []
        for i, (nombre, e) in enumerate(estado):
            txt += f"\n{ICONO.get(e, '⚪')} {nombre}: {e}"
            if e == "no instalado":
                continue
            if e == "activo":
                botones.append([(f"⏹ Apagar {nombre}", f"maqt:{m['id']}:{i}:off")])
            else:
                botones.append([(f"▶️ Prender {nombre}", f"maqt:{m['id']}:{i}:on")])
        botones.append([("◂ Máquina", f"maq:{m['id']}")])
        self.mostrar(chat, mid, txt, botones)

    # ------------------------------------------------------------------ botones
    def boton_maquinas(self, chat, mid, acc, arg=""):
        """Devuelve True si manejó el botón."""
        if acc == "maq":
            if not arg:
                self.estado.pop(chat, None)
                self.pantalla_maquinas(chat, mid)
            else:
                self.estado.pop(chat, None)
                self.pantalla_maquina(chat, mid, arg)
        elif acc == "maq_add":
            self.pedir(chat, "➕ Nueva VPS\n\nEscribí un nombre para identificarla (por ejemplo app02):", "m_nombre")
        elif acc == "maqp":
            self.pantalla_protocolos(chat, mid, arg)
        elif acc == "maqq":
            m = mq.buscar(arg) if arg else None
            if not m:
                self.pantalla_maquinas(chat, mid, "Esa máquina ya no está.")
            else:
                self.mostrar(chat, mid, f"🗑 ¿Quitar {m['nombre']} ({m['host']}) del bot?\n\n"
                                        "Se borran los datos guardados (contraseña y huella). "
                                        "El servidor no se toca.",
                             [[("✅ Sí, quitar", f"maqx:{m['id']}"), ("No", f"maq:{m['id']}")]])
        elif acc == "maqx":
            m = mq.buscar(arg) if arg else None
            if m:
                mq.quitar(arg)
                return self.pantalla_maquinas(chat, mid, f"✅ {m['nombre']} quitada del bot.")
            self.pantalla_maquinas(chat, mid, "Esa máquina ya no está.")
        elif acc == "maqt":
            self.boton_protocolo(chat, mid, arg)
        else:
            return False
        return True

    def boton_protocolo(self, chat, mid, arg):
        partes = arg.split(":")
        if len(partes) != 3 or not partes[1].isdigit() or partes[2] not in ("on", "off"):
            return self.pantalla_maquinas(chat, mid, "Eso no se pudo hacer. Probá de nuevo.")
        mid_maq, i, accion = partes[0], int(partes[1]), partes[2]
        try:
            m = mq.buscar(mid_maq)
            if not m:
                return self.pantalla_maquinas(chat, mid, "Esa máquina ya no está.")
            if i >= len(mq.PROTOCOLOS):
                raise mq.ErrorMaquina("Ese protocolo no existe.")
            nombre = mq.PROTOCOLOS[i][0]
            mq.cambiar_protocolo(m, nombre, encender=(accion == "on"))
            aviso = f"✅ {nombre} {'prendido' if accion == 'on' else 'apagado'}."
        except mq.ErrorMaquina as e:
            aviso = f"⚠️ {e}"
        self.pantalla_protocolos(chat, mid, mid_maq, aviso)

    # ------------------------------------------------------------------ pasos de texto
    def texto_maquinas(self, chat, e, paso, t):
        """Devuelve True si manejó el paso. Los pasos de la VPS empiezan con 'm_'."""
        if not paso.startswith("m_"):
            return False
        if paso == "m_nombre":
            if not NOMBRE_RE.match(t):
                self.tg.mensaje(chat, "⚠️ El nombre tiene que tener de 1 a 20 letras, números, espacios, puntos o guiones. Probá otro:", self.CANC_MAQ)
                return True
            e.update(paso="m_host", nombre=t)
            self.tg.mensaje(chat, f"VPS: {t}\n\nAhora la IP o el dominio del servidor:", self.CANC_MAQ)
        elif paso == "m_host":
            if not HOST_RE.match(t):
                self.tg.mensaje(chat, "⚠️ Eso no parece una IP ni un dominio. Escribilo de nuevo:", self.CANC_MAQ)
                return True
            e.update(paso="m_puerto", host=t)
            self.tg.mensaje(chat, "Puerto SSH (si es el 22, mandá 22):", self.CANC_MAQ)
        elif paso == "m_puerto":
            if not (t.isdigit() and 1 <= int(t) <= 65535):
                self.tg.mensaje(chat, "⚠️ Puerto inválido (tiene que ser un número de 1 a 65535):", self.CANC_MAQ)
                return True
            e.update(paso="m_usuario", puerto=int(t))
            self.tg.mensaje(chat, "Usuario (normalmente root, mandá root):", self.CANC_MAQ)
        elif paso == "m_usuario":
            if not USUARIO_RE.match(t):
                self.tg.mensaje(chat, "⚠️ Usuario inválido (letras minúsculas, números, guion o guion bajo):", self.CANC_MAQ)
                return True
            e.update(paso="m_clave", usuario=t)
            self.tg.mensaje(chat, "Contraseña de ese usuario.\n\nLa guardo cifrada y borro tu mensaje en cuanto la leo.", self.CANC_MAQ)
        elif paso == "m_clave":
            # la contraseña no se queda en el chat: se borra antes de cualquier otra cosa
            self.borrar_entrada(chat, e)
            if not t or len(t) > 200:
                self.tg.mensaje(chat, "⚠️ Contraseña inválida. Escribila de nuevo:", self.CANC_MAQ)
                return True
            self.tg.mensaje(chat, "🔌 Probando la conexión…")
            try:
                huella = mq.probar(e["host"], e["puerto"], e["usuario"], t)
                m = mq.agregar(e["nombre"], e["host"], e["puerto"], e["usuario"], t, huella)
            except mq.ErrorMaquina as err:
                self.estado.pop(chat, None)
                return self.tg.mensaje(chat, f"⚠️ No la pude agregar: {err}", [[("➕ Reintentar", "maq_add")], [("◂ Máquinas", "maq")]])
            self.estado.pop(chat, None)
            self.tg.mensaje(chat, f"✅ {m['nombre']} agregada.", [[("🖥 Ver máquina", f"maq:{m['id']}")], [("◂ Máquinas", "maq")]])
        else:
            return False
        return True

    CANC_MAQ = [[("✖ Cancelar", "maq")]]
