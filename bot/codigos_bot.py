"""Pantallas del bot para 🔑 Instalar en VPS nueva: códigos de un solo uso y las VPS que ya instalaron.

Se mezcla en la clase Bot de zumo-bot.py (usa self.tg, self.mostrar, self.accesos, self.dominio_lista).
La lógica de códigos y pases está en accesos.py; el servidor que los recibe, en publico.py."""
import time

import accesos as ac

VOLVER = [[("◂ Instalar en VPS nueva", "inst")]]


class CodigosMixin:

    def boton_codigos(self, chat, mid, acc, arg):
        """True si el botón era de esta sección."""
        if acc == "inst":
            self.pantalla_instalar(chat, mid)
        elif acc == "inst_n":
            self.nuevo_codigo(chat, mid, arg)
        elif acc == "inst_l":
            self.pantalla_vps_instaladas(chat, mid)
        elif acc == "inst_r":
            self.pantalla_revocar(chat, mid, arg)
        elif acc == "inst_rs":
            self.revocar_vps(chat, mid, arg)
        else:
            return False
        return True

    def pantalla_instalar(self, chat, mid, aviso=""):
        if not self.dominio_lista():
            return self.mostrar(chat, mid, "🔑 Instalar en VPS nueva\n\nPrimero hace falta el dominio del centro: poné "
                                "ZUMO_DOMINIO=tu.dominio.com en /etc/zumo/bot.env y reiniciá el bot (systemctl restart zumo-bot).",
                                [[("◂ Menú", "menu")]])
        n = len(self.accesos.listar())
        self.mostrar(chat, mid, (aviso + "\n\n" if aviso else "") + "🔑 Instalar en VPS nueva\n\n"
                     "Te doy un código que sirve una sola vez y vence en 15 minutos. En la VPS nueva corrés un comando corto, "
                     "te pide el código y se instala todo desde tu dominio, sin GitHub.\n\n"
                     f"VPS instaladas con este sistema: {n}",
                     [[("🆕 Código para el panel", "inst_n:panel")], [("🆕 Código para el bot", "inst_n:bot")],
                      [("📋 VPS instaladas", "inst_l")], [("◂ Menú", "menu")]])

    def nuevo_codigo(self, chat, mid, tipo):
        if tipo not in ac.TIPOS or not self.dominio_lista():
            return self.pantalla_instalar(chat, mid)
        codigo = self.accesos.crear_codigo(tipo)
        self.tg.mensaje(chat, f"🔑 Código para instalar: {ac.TIPOS[tipo].lower()}\n\n`{codigo}`\n\n"
                              "Sirve una sola vez y vence en 15 minutos. En la VPS nueva, como root:\n\n"
                              f"`curl -fsSL https://{self.dominio_lista()}/i | bash`\n\n"
                              "Cuando lo pida, pegá el código.", [[("◂ Instalar en VPS nueva", "inst")]], md=True)

    def pantalla_vps_instaladas(self, chat, mid):
        vps = self.accesos.listar()
        if not vps:
            return self.mostrar(chat, mid, "📋 Todavía no instalaste ninguna VPS con un código.", VOLVER)
        filas, botones = [], []
        for x in vps:
            fecha = time.strftime("%d/%m/%Y %H:%M", time.localtime(x["fecha"]))
            filas.append(f"#{x['id']} · {ac.TIPOS.get(x['tipo'], x['tipo'])} · {fecha}" + (f" · {x['ip']}" if x["ip"] else ""))
            botones.append([(f"🗑 Anular #{x['id']}", f"inst_r:{x['id']}")])
        self.mostrar(chat, mid, "📋 VPS instaladas con código\n\n" + "\n".join(filas) +
                     "\n\nAnular le corta a esa VPS el acceso a las actualizaciones desde tu dominio (lo ya instalado sigue andando).",
                     botones + VOLVER)

    def pantalla_revocar(self, chat, mid, arg):
        if not arg.isdigit():
            return self.pantalla_vps_instaladas(chat, mid)
        self.mostrar(chat, mid, f"🗑 ¿Anular el acceso de la VPS #{arg}? No podrá bajar más actualizaciones de tu dominio.",
                     [[("✅ Sí, anular", f"inst_rs:{arg}"), ("✖ No", "inst_l")]])

    def revocar_vps(self, chat, mid, arg):
        ok = arg.isdigit() and self.accesos.revocar(int(arg))
        self.pantalla_instalar(chat, mid, f"✅ Acceso #{arg} anulado." if ok else "Esa VPS ya no estaba en la lista.")
