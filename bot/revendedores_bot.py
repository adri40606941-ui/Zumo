"""Pantallas del bot para 🧑‍💼 Revendedores: crear su acceso al panel web, asignarle una VPS y cargarle monedas.

Se mezcla en la clase Bot de zumo-bot.py (usa self.tg, self.estado, self.mostrar, self.revs y self.dominio_lista()).
Los datos y las reglas están en revendedores.py; el panel web en panel_web.py.
"""
import os
import shutil
import socket
import ssl
import threading
import time
from datetime import date, datetime

import maquinas as mq
import revendedores as rv

VOLVER = [[("◂ Revendedores", "rv")]]
CANCELAR = [[("✖ Cancelar", "rv")]]
RANGO = 999
RESPALDOS = os.environ.get("ZUMO_RESPALDOS_REV", "/var/backups/zumo")     # copias locales diarias de revendedores.json
GUARDAR_COPIAS = 14
CERT = "/etc/zumo/web/cert.pem"


def _monedas(r):
    return " ".join(f"{rv.EMOJI[t]}{r['monedas'].get(t, 0)}" for t in rv.MONEDAS)


class RevendedoresMixin:
    srv_rev = None                  # el Servicio de revendedores (lo pone main)
    _vps_fallas = None
    _vps_caidas = None

    # ------------------------------------------------------------------ avisos y vigilancia
    def avisar_admins(self, texto):
        """Manda el texto a todos los admins sin trabar a quien lo pidió (el panel web, por ejemplo)."""
        def enviar():
            for a in sorted(self.admins):
                try:
                    self.tg.mensaje(a, texto)
                except Exception as ex:
                    print("zumo-bot: no pude avisar al admin:", ex, flush=True)
        threading.Thread(target=enviar, daemon=True).start()

    def copia_local_revendedores(self):
        """Una copia por día de revendedores.json en RESPALDOS (se guardan las últimas 14). Devuelve la ruta o None."""
        origen = self.revs.archivo
        if not os.path.exists(origen):
            return None
        os.makedirs(RESPALDOS, mode=0o700, exist_ok=True)
        destino = os.path.join(RESPALDOS, f"revendedores-{date.today():%Y%m%d}.json")
        if not os.path.exists(destino):
            shutil.copyfile(origen, destino)
            os.chmod(destino, 0o600)
        copias = sorted(f for f in os.listdir(RESPALDOS) if f.startswith("revendedores-") and f.endswith(".json"))
        for viejo in copias[:-GUARDAR_COPIAS]:
            try:
                os.remove(os.path.join(RESPALDOS, viejo))
            except OSError:
                pass
        return destino

    def _aviso_sin_clave(self):
        """Si no hay contraseña de respaldo, las monedas y usuarios solo tienen copia dentro de la misma VPS:
        avisa una vez por semana."""
        env = self.leer_env_fn() if getattr(self, "leer_env_fn", None) else {}
        if env.get("RESPALDO_PASS"):
            return
        marca = os.path.join(RESPALDOS, "aviso-sin-clave")
        try:
            if time.time() - os.path.getmtime(marca) < 7 * 86400:
                return
        except OSError:
            pass
        open(marca, "w").close()
        self.avisar_admins("⚠️ No hay contraseña de respaldo: las monedas y los usuarios de tus revendedores solo tienen copia "
                           "dentro de esta misma VPS. Si se pierde, se pierde todo.\n\n"
                           "Poné la contraseña en 💾 Respaldo y el bot te manda cada día una copia cifrada por acá.")

    def _vigilar_vps(self):
        """Prueba cada VPS de los revendedores. Avisa cuando una deja de contestar (2 veces seguidas) y cuando vuelve."""
        if self._vps_fallas is None:
            self._vps_fallas, self._vps_caidas = {}, set()
        uso = {}
        for r in self.revs.listar():
            for i in r["maquinas"]:
                uso.setdefault(i, []).append(r["usuario"])
        for mid, quienes in uso.items():
            try:
                m = mq.buscar(mid)
                if not m:
                    continue
                mq.correr(m, "true", timeout=10)
                self._vps_fallas[mid] = 0
                if mid in self._vps_caidas:
                    self._vps_caidas.discard(mid)
                    self.avisar_admins(f"🟢 La VPS {m['nombre']} volvió a contestar.")
            except Exception:
                self._vps_fallas[mid] = self._vps_fallas.get(mid, 0) + 1
                if self._vps_fallas[mid] >= 2 and mid not in self._vps_caidas:
                    self._vps_caidas.add(mid)
                    try:
                        nombre = (mq.buscar(mid) or {}).get("nombre", mid)
                    except Exception:
                        nombre = mid
                    self.avisar_admins(f"🔴 La VPS {nombre} no contesta (revendedores: {', '.join(sorted(set(quienes)))}). "
                                       "Los usuarios nuevos se crean en las demás; cuando vuelva se completa sola.")

    def vigilar_una_vez(self):
        if self.srv_rev is not None:
            hechos = self.srv_rev.reparar()
            if hechos:
                self.avisar_admins("🔧 Completé usuarios de revendedores en sus VPS:\n" + "\n".join(hechos[:15])
                                   + (f"\n… y {len(hechos) - 15} más" if len(hechos) > 15 else ""))
        self._vigilar_vps()
        self.copia_local_revendedores()
        self._aviso_sin_clave()

    def vigilar_revendedores(self):
        """Hilo: cada 5 minutos completa usuarios en VPS nuevas, vigila las VPS y hace la copia diaria."""
        time.sleep(90)
        while True:
            try:
                self.vigilar_una_vez()
            except Exception as ex:      # el hilo no se puede caer
                print("zumo-bot: vigilancia de revendedores:", ex, flush=True)
            time.sleep(300)

    def url_panel(self):
        d = self.dominio_lista()
        return f"https://{d}/r" if d else ""

    def _nombre_maquina(self, mid):
        try:
            m = mq.buscar(mid)
        except mq.ErrorMaquina:
            m = None
        return f"{m['nombre']} ({m['host']})" if m else "⚠️ (sin VPS)"

    def _vps_texto(self, r):
        return ", ".join(self._nombre_maquina(i) for i in r["maquinas"]) or "⚠️ ninguna"

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
        botones.append([("📋 Todos los usuarios", "rvu"), ("📊 Resumen", "rvs")])
        botones.append([("🔒 HTTPS del panel", "rvt")])
        botones.append([("◂ Menú", "menu")])
        self.mostrar(chat, mid, txt, botones)

    def pantalla_revendedor(self, chat, mid, rid, aviso=""):
        r = self.revs.buscar(rid)
        if not r:
            return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.")
        n = len(self.revs.cuentas_de(rid))
        txt = (aviso + "\n\n" if aviso else "") + (
            f"🧑‍💼 {r['usuario']}{'' if r['activo'] else '  ⛔ acceso bloqueado'}\n\n"
            f"🖥 VPS: {self._vps_texto(r)}\n"
            f"👥 Usuarios creados: {n}\n\n"
            f"🥉 Bronce (7 días): {r['monedas'].get('bronce', 0)}\n"
            f"🥈 Plata (15 días): {r['monedas'].get('plata', 0)}\n"
            f"🥇 Oro (30 días): {r['monedas'].get('oro', 0)}\n\n"
            "Tocá una moneda para agregarle (o quitarle).")
        botones = [[(f"{rv.EMOJI[t]} {t.capitalize()} · {d} días", f"rvc:{rid}:{t}")] for t, d in rv.MONEDAS.items()]
        botones += [[("🔑 Contraseña", f"rvk:{rid}"), (f"🖥 VPS ({len(r['maquinas'])})", f"rvmq:{rid}")],
                    [("📋 Usuarios y VPS", f"rvu:{rid}")],
                    [("▶️ Activar acceso" if not r["activo"] else "⏸ Bloquear acceso", f"rvb:{rid}"),
                     ("📜 Movimientos", f"rvh:{rid}")],
                    [("🗑 Eliminar revendedor", f"rvx:{rid}")],
                    [("◂ Revendedores", "rv")]]
        self.mostrar(chat, mid, txt, botones)

    def pantalla_vps_rev(self, chat, mid, rid, aviso=""):
        r = self.revs.buscar(rid)
        if not r:
            return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.")
        txt = (aviso + "\n\n" if aviso else "") + f"🖥 VPS de {r['usuario']}\n\n"
        txt += "\n".join(f"• {self._nombre_maquina(i)}" for i in r["maquinas"]) or "(ninguna)"
        txt += ("\n\nLos usuarios nuevos se crean en todas estas VPS; renovar, bloquear y eliminar también se aplican en todas. "
                "Si una no contesta, se avisa y se completa sola cuando vuelve.")
        botones = [[(f"❌ Quitar {self._nombre_maquina(i).split(' (')[0]}", f"rvmq-:{rid}:{i}")] for i in r["maquinas"]
                   if len(r["maquinas"]) > 1]
        botones += [[("➕ Agregar otra VPS", f"rvmq+:{rid}")], [("◂ Volver", f"rv:{rid}")]]
        self.mostrar(chat, mid, txt, botones)

    def lineas_usuarios(self, rid=None):
        """Texto: usuarios creados por revendedores y en qué VPS está cada uno."""
        cuentas = self.revs.todas_las_cuentas()
        revs = {r["id"]: r for r in self.revs.listar()}
        por_rev = {}
        for t, c in cuentas.items():
            if rid and c["rev"] != str(rid):
                continue
            por_rev.setdefault(c["rev"], []).append((t, c))
        nombres = {}
        out = []
        for r_id, items in sorted(por_rev.items(), key=lambda x: revs.get(x[0], {"usuario": "~"})["usuario"]):
            r = revs.get(r_id)
            out.append(f"🧑‍💼 {r['usuario'] if r else '(eliminado)'} · {len(items)} usuario(s)")
            for t, c in sorted(items, key=lambda x: -x[1].get("creado", 0)):
                vps = ", ".join(nombres.setdefault(i, self._nombre_maquina(i).split(" (")[0]) for i in c["maq"]) or "—"
                cuando = datetime.fromtimestamp(c.get("creado", 0)).strftime("%d/%m/%y") if c.get("creado") else "?"
                out.append(f"  • {c.get('etq') or 'cliente'} · {t} · creado {cuando} · 🖥 {vps}")
            out.append("")
        return out

    def pantalla_usuarios_rev(self, chat, mid, rid=""):
        r = self.revs.buscar(rid) if rid else None
        if rid and not r:
            return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.")
        lineas = self.lineas_usuarios(rid or None)
        volver = [[("◂ " + (r["usuario"] if r else "Revendedores"), f"rv:{rid}" if r else "rv")]]
        if not lineas:
            return self.mostrar(chat, mid, "📋 Todavía no hay usuarios creados por revendedores.", volver)
        txt = "📋 Usuarios y en qué VPS se crearon\n\n" + "\n".join(lineas)
        if len(txt) <= 3500:
            return self.mostrar(chat, mid, txt, volver)
        self.mostrar(chat, mid, "📋 La lista es larga: te la mando como archivo.", volver)
        self.tg.documento(chat, "usuarios-revendedores.txt", "\n".join(lineas).encode("utf-8"), "📋 Usuarios y VPS")

    @staticmethod
    def _mes(offset=0):
        """(desde, hasta) en epoch del mes de hoy (offset 0) o del anterior (-1), en hora local."""
        h = date.today()
        a, m = h.year, h.month + offset
        while m < 1:
            a, m = a - 1, m + 12
        d0 = datetime(a, m, 1)
        d1 = datetime(a + (m == 12), 1 if m == 12 else m + 1, 1)
        return d0.timestamp(), d1.timestamp(), d0.strftime("%m/%Y")

    def pantalla_resumen(self, chat, mid, anterior=False):
        desde, hasta, nombre = self._mes(-1 if anterior else 0)
        res = self.revs.resumen(desde, hasta)
        txt = f"📊 Resumen de {nombre}\n\nCargadas = monedas que les diste · Gastadas = usuarios creados o renovados\n"
        tot_c = {t: 0 for t in rv.MONEDAS}
        tot_g = {t: 0 for t in rv.MONEDAS}
        for o in sorted(res.values(), key=lambda x: x["usuario"]):
            fmt = lambda d: " ".join(f"{rv.EMOJI[t]}{d[t]}" for t in rv.MONEDAS)
            txt += (f"\n🧑‍💼 {o['usuario']}{'' if o['activo'] else ' ⛔'} · {o['usuarios']} usuarios\n"
                    f"   Cargadas {fmt(o['cargadas'])} · Gastadas {fmt(o['gastadas'])}\n"
                    f"   Saldo sin usar {fmt(o['saldo'])}\n")
            for t in rv.MONEDAS:
                tot_c[t] += o["cargadas"][t]
                tot_g[t] += o["gastadas"][t]
        if not res:
            txt += "\n(todavía no hay revendedores)"
        else:
            fmt = lambda d: " ".join(f"{rv.EMOJI[t]}{d[t]}" for t in rv.MONEDAS)
            txt += f"\nTOTAL · Cargadas {fmt(tot_c)} · Gastadas {fmt(tot_g)}"
        otro = ("📅 Este mes", "rvs") if anterior else ("📅 Mes anterior", "rvs:prev")
        self.mostrar(chat, mid, txt[:4000], [[otro], [("◂ Revendedores", "rv")]])

    def estado_https(self):
        """(hay certificado, el puerto 443 contesta con TLS) del servidor web de este bot."""
        cert = os.path.exists(CERT)
        ok = False
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            with socket.create_connection(("127.0.0.1", 443), timeout=3) as s, ctx.wrap_socket(s):
                ok = True
        except (OSError, ssl.SSLError):
            pass
        return cert, ok

    def pantalla_https(self, chat, mid):
        cert, ok = self.estado_https()
        d = self.dominio_lista() or "(falta el dominio)"
        txt = ("🔒 HTTPS del panel de revendedores\n\n"
               f"Dominio: {d}\n"
               f"{'✅' if cert else '⚠️'} Certificado en la VPS: {'sí' if cert else 'no (el instalador lo crea si hay dominio)'}\n"
               f"{'✅' if ok else '⚠️'} Puerto 443 de esta VPS: {'contesta' if ok else 'no contesta (¿firewall o el bot sin reiniciar?)'}\n\n"
               "El panel tiene usuarios y contraseñas, así que conviene que TODO el camino vaya cifrado:\n\n"
               "1) En Cloudflare entrá a tu dominio → SSL/TLS → Resumen.\n"
               "2) Cambiá el modo de «Flexible» a «Completo» (Full). Con «Flexible», el tramo entre Cloudflare y tu VPS va SIN cifrar.\n"
               "   (Con el certificado propio de esta VPS sirve «Completo»; «Completo (estricto)» pide un certificado de origen de Cloudflare.)\n"
               "3) En SSL/TLS → Certificados de borde, activá «Usar siempre HTTPS».\n\n"
               "Antes de cambiarlo, comprobá que arriba el puerto 443 diga ✅; si no, el panel dejaría de abrir.")
        self.mostrar(chat, mid, txt, [[("🔄 Volver a comprobar", "rvt")], [("◂ Revendedores", "rv")]])

    def _datos_acceso(self, usuario, clave):
        url = self.url_panel() or "(falta el dominio de la VPS)"
        return f"🔗 {url}\n👤 Usuario: {usuario}\n🔒 Contraseña: {clave}"

    def _elegir_maquina(self, chat, mid, destino, aviso="", extra=()):
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
        maqs = [m for m in maqs if m["id"] not in extra]
        if not maqs:
            return self.pantalla_vps_rev(chat, mid, destino.split(":")[1], "No quedan más VPS enlazadas para sumarle. "
                                                                           "Enlazá otra en 🖥 Máquinas.")
        botones = [[(f"🖥 {m['nombre']} · {m['host']}", f"{destino}:{m['id']}")] for m in maqs]
        botones.append([("✖ Cancelar", "rv")])
        txt = (aviso + "\n\n" if aviso else "") + ("¿Qué VPS le sumás? Los usuarios nuevos se crean en todas las suyas."
                                                    if extra else "¿En qué VPS se van a crear los usuarios de este revendedor?")
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
        elif acc == "rvmq":           # las VPS del revendedor
            if not self.revs.buscar(arg):
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            self.pantalla_vps_rev(chat, mid, arg)
        elif acc == "rvmq+":          # elegir una VPS más
            if not self.revs.buscar(arg):
                return self.pantalla_revendedores(chat, mid, "Ese revendedor ya no existe.") or True
            self._elegir_maquina(chat, mid, f"rvmq2:{arg}", extra=self.revs.buscar(arg)["maquinas"])
        elif acc == "rvmq2":          # VPS elegida: se suma
            rid, mid_maq = (partes + [""])[:2]
            try:
                self.revs.agregar_maquina(rid, mid_maq)
            except rv.ErrorRevendedor as err:
                return self.pantalla_vps_rev(chat, mid, rid, f"⚠️ {err}") or True
            self.pantalla_vps_rev(chat, mid, rid, "✅ VPS agregada. Los usuarios nuevos se crean en todas; "
                                                  "los que ya tenía se completan solos en unos minutos.")
        elif acc == "rvmq-":          # sacar una VPS
            rid, mid_maq = (partes + [""])[:2]
            try:
                self.revs.quitar_maquina(rid, mid_maq)
            except rv.ErrorRevendedor as err:
                return self.pantalla_vps_rev(chat, mid, rid, f"⚠️ {err}") or True
            self.pantalla_vps_rev(chat, mid, rid, "✅ VPS quitada. Sus usuarios siguen ahí hasta que los elimine.")
        elif acc == "rvu":            # usuarios creados y en qué VPS
            self.pantalla_usuarios_rev(chat, mid, arg)
        elif acc == "rvs":            # resumen de monedas
            self.pantalla_resumen(chat, mid, arg == "prev")
        elif acc == "rvt":            # HTTPS del panel
            self.pantalla_https(chat, mid)
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
