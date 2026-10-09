"""Pantallas del bot para 🧑‍💼 Revendedores: crear su acceso al panel web, asignarle una VPS y cargarle monedas.

Se mezcla en la clase Bot de zumo-bot.py (usa self.tg, self.estado, self.mostrar, self.revs y self.dominio_lista()).
Los datos y las reglas están en revendedores.py; el panel web en panel_web.py.
"""
import time

import maquinas as mq
import revendedores as rv

VOLVER = [[("◂ Revendedores", "rv")]]
CANCELAR = [[("✖ Cancelar", "rv")]]
RANGO = 999


def _monedas(r):
    return " ".join(f"{rv.EMOJI[t]}{r['monedas'].get(t, 0)}" for t in rv.MONEDAS)


class RevendedoresMixin:

    def url_panel(self):
        d = self.dominio_lista()
        return f"https://{d}/r" if d else ""

    def _nombre_maquina(self, mid):
        try:
            m = mq.buscar(mid)
        except mq.ErrorMaquina:
            m = None
        return f"{m['nombre']} ({m['host']})" if m else "⚠️ (sin VPS)"

    # ------------------------------------------------------------------ pantallas
    def pantalla_revendedores(self, chat, mid, aviso=""):
        lista = sorted(self.revs.listar(), key=lambda r: r["usuario"])
        txt = (aviso + "\n\n" if aviso else "") + f"🧑‍💼 Revendedores · {len(lista)}\n"
        txt += "\nMonedas: 🥉 bronce = 7 días · 🥈 plata = 15 · 🥇 oro = 30\n"
        url = self.url_panel()
        txt += f"\n🔗 Panel web: {url}" if url else ("\n⚠️ Para usar el panel web falta el dominio de esta VPS "
                                                    "(ZUMO_DOMINIO en bot.env, lo pide el instalador del bot).")
        if lista:
            txt += "\n\nTocá uno para cargarle monedas, cambiarle la contraseña o la VPS."
        else:
            txt += "\n\nTodavía no tenés revendedores. Creá uno con ➕."
        botones = [[(("⛔ " if not r["activo"] else "🧑‍💼 ") + f"{r['usuario']} · {_monedas(r)}", f"rv:{r['id']}")] for r in lista]
        botones.append([("➕ Agregar revendedor", "rv_add")])
        botones.append([("◂ Menú", "menu")])
        self.mostrar(chat, mid, txt, botones)

    def pantalla_revendedor(self, chat, mid, rid, aviso=""):
        r = self.revs.buscar(rid)
        if not r:
            return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.")
        n = len(self.revs.cuentas_de(rid))
        txt = (aviso + "\n\n" if aviso else "") + (
            f"🧑‍💼 {r['usuario']}{'' if r['activo'] else '  ⛔ acceso bloqueado'}\n\n"
            f"🖥 VPS: {self._nombre_maquina(r['maquina'])}\n"
            f"👥 Usuarios creados: {n}\n\n"
            f"🥉 Bronce (7 días): {r['monedas'].get('bronce', 0)}\n"
            f"🥈 Plata (15 días): {r['monedas'].get('plata', 0)}\n"
            f"🥇 Oro (30 días): {r['monedas'].get('oro', 0)}\n\n"
            "Tocá una moneda para agregarle (o quitarle).")
        botones = [[(f"{rv.EMOJI[t]} {t.capitalize()} · {d} días", f"rvc:{rid}:{t}")] for t, d in rv.MONEDAS.items()]
        botones += [[("🔑 Contraseña", f"rvk:{rid}"), ("🖥 Cambiar VPS", f"rvmq:{rid}")],
                    [("▶️ Activar acceso" if not r["activo"] else "⏸ Bloquear acceso", f"rvb:{rid}"),
                     ("📜 Movimientos", f"rvh:{rid}")],
                    [("🗑 Eliminar revendedor", f"rvx:{rid}")],
                    [("◂ Revendedores", "rv")]]
        self.mostrar(chat, mid, txt, botones)

    def _datos_acceso(self, usuario, clave):
        url = self.url_panel() or "(falta el dominio de la VPS)"
        return f"🔗 {url}\n👤 Usuario: {usuario}\n🔒 Contraseña: {clave}"

    def _elegir_maquina(self, chat, mid, destino, aviso=""):
        """Lista de máquinas para asignar. destino: 'rvm' (alta, sin id) o 'rvmq2:<id>' (cambiar)."""
        try:
            maqs = mq.cargar()
        except mq.ErrorMaquina as e:
            return self.tg.mensaje(chat, f"⚠️ {e}", CANCELAR)
        if not maqs:
            self.estado.pop(chat, None)
            return self.tg.mensaje(chat, "⚠️ Todavía no hay ninguna VPS enlazada. Primero agregá una en 🖥 Máquinas "
                                         "(IP, puerto, usuario y contraseña) y después volvé acá.",
                                   [[("🖥 Máquinas", "maq")], [("◂ Revendedores", "rv")]])
        botones = [[(f"🖥 {m['nombre']} · {m['host']}", f"{destino}:{m['id']}")] for m in maqs]
        botones.append([("✖ Cancelar", "rv")])
        txt = (aviso + "\n\n" if aviso else "") + "¿En qué VPS se van a crear los usuarios de este revendedor?"
        if mid:
            self.mostrar(chat, mid, txt, botones)
        else:
            self.tg.mensaje(chat, txt, botones)

    # ------------------------------------------------------------------ botones
    def boton_revendedores(self, chat, mid, acc, arg=""):
        """Devuelve True si manejó el botón."""
        if not (acc == "rv" or acc.startswith("rv")):
            return False
        partes = arg.split(":")
        if acc == "rv":
            self.estado.pop(chat, None)
            if arg:
                return self.pantalla_revendedor(chat, mid, arg) or True
            self.pantalla_revendedores(chat, mid)
        elif acc == "rv_add":
            self.estado[chat] = {"paso": "rv_usuario"}
            self.tg.mensaje(chat, "➕ Nuevo revendedor\n\nEscribí el usuario con el que va a entrar al panel "
                                  "(de 3 a 20 letras minúsculas, números, punto o guion):", CANCELAR)
        elif acc == "rvg":            # contraseña generada, en el alta
            e = self.estado.get(chat)
            if not e or e.get("paso") != "rv_clave":
                return True
            e["clave"] = rv.clave_nueva()
            e["paso"] = "rv_maquina"
            self._elegir_maquina(chat, None, "rvm")
        elif acc == "rvm":            # VPS elegida, en el alta
            e = self.estado.get(chat)
            if not e or e.get("paso") != "rv_maquina":
                return True
            try:
                r = self.revs.crear(e["usuario"], e["clave"], arg)
            except rv.ErrorRevendedor as err:
                self.estado.pop(chat, None)
                return self.tg.mensaje(chat, f"⚠️ {err}", VOLVER)
            self.estado.pop(chat, None)
            self.tg.mensaje(chat, f"✅ Revendedor creado. Pasale estos datos:\n\n{self._datos_acceso(e['usuario'], e['clave'])}\n\n"
                                  "Todavía no tiene monedas: cargale desde su ficha.",
                            [[("🧑‍💼 Ver revendedor", f"rv:{r['id']}")], [("◂ Revendedores", "rv")]])
        elif acc == "rvc":            # elegir cuántas monedas
            rid, tipo = (partes + ["", ""])[:2]
            if not self.revs.buscar(rid) or tipo not in rv.MONEDAS:
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            self.estado[chat] = {"paso": "rv_cant", "rid": rid, "tipo": tipo}
            fila = [(f"+{n}", f"rvn:{rid}:{tipo}:{n}") for n in (1, 5, 10, 20)]
            self.tg.mensaje(chat, f"{rv.EMOJI[tipo]} Moneda de {tipo} ({rv.MONEDAS[tipo]} días)\n\n"
                                  "¿Cuántas le agregás? Tocá un número o escribilo. "
                                  "Con un número negativo (por ejemplo -2) se le quitan.",
                            [fila, [("✖ Cancelar", f"rv:{rid}")]])
        elif acc == "rvn":
            rid, tipo, n = (partes + ["", "", ""])[:3]
            self.estado.pop(chat, None)
            self._dar_monedas(chat, rid, tipo, n)
        elif acc == "rvk":            # cambiar contraseña
            r = self.revs.buscar(arg)
            if not r:
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            self.estado[chat] = {"paso": "rv_nclave", "rid": arg}
            self.tg.mensaje(chat, f"🔑 Nueva contraseña para {r['usuario']} (de {rv.CLAVE_MIN} a {rv.CLAVE_MAX} caracteres).\n\n"
                                  "La borro del chat en cuanto la leo. O tocá para que invente una.",
                            [[("🎲 Generar una", f"rvkg:{arg}")], [("✖ Cancelar", f"rv:{arg}")]])
        elif acc == "rvkg":
            self.estado.pop(chat, None)
            self._poner_clave(chat, arg, rv.clave_nueva())
        elif acc == "rvb":            # bloquear / activar acceso al panel
            r = self.revs.buscar(arg)
            if not r:
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            self.revs.activar(arg, not r["activo"])
            self.pantalla_revendedor(chat, mid, arg, "▶️ Acceso activado." if not r["activo"] else
                                     "⏸ Acceso bloqueado: ya no puede entrar al panel. Sus usuarios siguen conectando.")
        elif acc == "rvmq":
            if not self.revs.buscar(arg):
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            self._elegir_maquina(chat, mid, f"rvmq2:{arg}")
        elif acc == "rvmq2":
            rid, mid_maq = (partes + [""])[:2]
            try:
                self.revs.asignar_maquina(rid, mid_maq)
            except rv.ErrorRevendedor as err:
                return self.pantalla_revendedores(chat, mid, f"⚠️ {err}") or True
            self.pantalla_revendedor(chat, mid, rid, "✅ VPS cambiada. Los usuarios que ya creó siguen en la VPS anterior.")
        elif acc == "rvh":
            r = self.revs.buscar(arg)
            if not r:
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            movs = self.revs.movimientos(arg, 15)
            lineas = [self._linea_mov(m) for m in reversed(movs)] or ["(sin movimientos)"]
            self.mostrar(chat, mid, f"📜 Últimos movimientos de {r['usuario']}\n\n" + "\n".join(lineas),
                         [[("◂ Volver", f"rv:{arg}")]])
        elif acc == "rvx":
            r = self.revs.buscar(arg)
            if not r:
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            self.mostrar(chat, mid, f"🗑 ¿Eliminar a {r['usuario']}?\n\nPierde el acceso al panel y sus monedas. "
                                    "Los usuarios que creó siguen en la VPS y siguen conectando (nadie los puede tocar desde el panel).",
                         [[("✅ Sí, eliminar", f"rvxx:{arg}"), ("No", f"rv:{arg}")]])
        elif acc == "rvxx":
            r = self.revs.buscar(arg)
            try:
                self.revs.eliminar(arg)
            except rv.ErrorRevendedor as err:
                return self.pantalla_revendedores(chat, mid, f"⚠️ {err}") or True
            self.pantalla_revendedores(chat, mid, f"✅ {r['usuario']} eliminado.")
        else:
            return False
        return True

    # ------------------------------------------------------------------ acciones
    @staticmethod
    def _linea_mov(m):
        cuando = time.strftime("%d/%m %H:%M", time.localtime(m["t"]))
        q = m["que"]
        if q == "monedas":
            n = m.get("n", 0)
            return f"{cuando} · {'+' if n > 0 else ''}{n} {rv.EMOJI.get(m.get('tipo'), '')} {m.get('tipo', '')} (admin)"
        if q in ("crear", "renovar"):
            return f"{cuando} · {q} {m.get('token', '')} · {m.get('dias', '')} d {rv.EMOJI.get(m.get('moneda'), '')}"
        if q in ("bloquear", "desbloquear", "eliminar"):
            return f"{cuando} · {q} {m.get('token', '')}"
        return f"{cuando} · {q}"

    def _dar_monedas(self, chat, rid, tipo, n):
        try:
            n = int(n)
            if n == 0 or abs(n) > RANGO:
                raise ValueError
            r = self.revs.agregar_monedas(rid, tipo, n)
        except ValueError:
            return self.tg.mensaje(chat, f"⚠️ Escribí un número entero distinto de cero (hasta {RANGO}).", [[("◂ Volver", f"rv:{rid}")]])
        except rv.ErrorRevendedor as err:
            return self.tg.mensaje(chat, f"⚠️ {err}", [[("◂ Volver", f"rv:{rid}")]])
        self.pantalla_revendedor(chat, None, rid,
                                 f"✅ {'+' if n > 0 else ''}{n} {rv.EMOJI[tipo]} {tipo}. Ahora tiene {r['monedas'][tipo]}.")

    def _poner_clave(self, chat, rid, clave):
        try:
            r = self.revs.cambiar_clave(rid, clave)
        except rv.ErrorRevendedor as err:
            return self.tg.mensaje(chat, f"⚠️ {err}", [[("◂ Volver", f"rv:{rid}")]])
        self.tg.mensaje(chat, f"✅ Contraseña cambiada. Los datos nuevos:\n\n{self._datos_acceso(r['usuario'], clave)}",
                        [[("🧑‍💼 Ver revendedor", f"rv:{rid}")], [("◂ Revendedores", "rv")]])

    # ------------------------------------------------------------------ pasos de texto
    def texto_revendedores(self, chat, e, paso, t):
        """Devuelve True si manejó el paso. Los pasos de revendedores empiezan con 'rv_'."""
        if not paso.startswith("rv_"):
            return False
        if paso == "rv_usuario":
            u = t.strip().lower()
            if not rv.USUARIO_RE.match(u):
                self.tg.mensaje(chat, "⚠️ Usuario inválido: de 3 a 20 letras minúsculas, números, punto, guion o guion bajo. Probá otro:", CANCELAR)
            elif any(r["usuario"] == u for r in self.revs.listar()):
                self.tg.mensaje(chat, "⚠️ Ese usuario ya existe. Escribí otro:", CANCELAR)
            else:
                e.update(paso="rv_clave", usuario=u)
                self.tg.mensaje(chat, f"Usuario: {u}\n\nAhora la contraseña (de {rv.CLAVE_MIN} a {rv.CLAVE_MAX} caracteres). "
                                      "La borro del chat en cuanto la leo. O tocá para que invente una.",
                                [[("🎲 Generar una", "rvg")], [("✖ Cancelar", "rv")]])
        elif paso == "rv_clave":
            self.borrar_entrada(chat, e)
            if not rv.clave_valida(t):
                self.tg.mensaje(chat, f"⚠️ Contraseña inválida: de {rv.CLAVE_MIN} a {rv.CLAVE_MAX} caracteres, sin espacios al borde. Escribila de nuevo:",
                                [[("🎲 Generar una", "rvg")], [("✖ Cancelar", "rv")]])
            else:
                e.update(paso="rv_maquina", clave=t)
                self._elegir_maquina(chat, None, "rvm")
        elif paso == "rv_cant":
            self._cantidad_texto(chat, e, t)
        elif paso == "rv_nclave":
            self.borrar_entrada(chat, e)
            self.estado.pop(chat, None)
            self._poner_clave(chat, e["rid"], t)
        else:
            return False
        return True

    def _cantidad_texto(self, chat, e, t):
        self.estado.pop(chat, None)
        self._dar_monedas(chat, e["rid"], e["tipo"], t.strip().replace("+", ""))
