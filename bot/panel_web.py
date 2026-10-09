"""Panel web de revendedores (https://tu-dominio/r).

Cada revendedor entra con el usuario y la contraseña que le creó el administrador desde el bot, y puede:
crear usuarios (token + nombre + 7/15/30 días), renovarlos, bloquearlos/desbloquearlos y eliminarlos. Crear y renovar
gastan monedas (bronce 7 días, plata 15, oro 30); lo demás es gratis. Solo ve y toca los usuarios que él creó.

Seguridad: sin JavaScript (CSP estricta), cookie de sesión HttpOnly + SameSite=Strict + Secure, token CSRF en cada
formulario, contraseñas con scrypt, freno de intentos por IP y por usuario, y solo funciona por https (por http
redirige). Se engancha al servidor web del bot (publico.py) con manejar().
"""
import html
import re
import secrets
import time
import urllib.parse
from datetime import date

from revendedores import EMOJI, MONEDAS, ErrorRevendedor

BASE = "/r"
VIDA_SESION = 12 * 3600
MAX_SESIONES = 500
FALLOS_IP, VENTANA_IP = 10, 300
FALLOS_USUARIO, VENTANA_USUARIO = 8, 600
CACHE_LISTA = 15
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")
CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
       "base-uri 'none'; frame-ancestors 'none'")

CSS = """
:root{--bg:#f4f5f8;--tarjeta:#fff;--texto:#1b1d27;--suave:#6a6f82;--borde:#e1e3ea;--acento:#6d4aff;--ok:#1f9d57;--mal:#d33f49;--aviso:#c98a00}
@media(prefers-color-scheme:dark){:root{--bg:#10121a;--tarjeta:#1a1d29;--texto:#eceef6;--suave:#9aa0b6;--borde:#2a2e3f;--acento:#8d73ff;--ok:#46c47f;--mal:#f0636c;--aviso:#e8b23a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--texto);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0}h2{font-size:17px;margin:0 0 12px}
.tarjeta{background:var(--tarjeta);border:1px solid var(--borde);border-radius:14px;padding:16px;margin:0 0 14px}
.barra{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 14px}
.suave{color:var(--suave);font-size:14px}
.monedas{display:flex;gap:10px;flex-wrap:wrap}
.m{display:inline-flex;align-items:center;gap:6px;font-weight:600}
.m i{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;font-style:normal;font-weight:800;font-size:14px;color:#2a1c00;box-shadow:inset 0 -3px 5px rgba(0,0,0,.25),inset 0 2px 3px rgba(255,255,255,.55),0 1px 2px rgba(0,0,0,.3)}
.bronce i{background:radial-gradient(circle at 30% 25%,#e9b27d,#b0662b 70%);color:#fff}
.plata i{background:radial-gradient(circle at 30% 25%,#fafafa,#a9afbc 70%);color:#2b2f3a}
.oro i{background:radial-gradient(circle at 30% 25%,#fff0a8,#e0a815 70%);color:#4a3200}
label{display:block;font-size:14px;color:var(--suave);margin:10px 0 4px}
input[type=text],input[type=password]{width:100%;padding:12px;border-radius:10px;border:1px solid var(--borde);background:var(--bg);color:var(--texto);font-size:16px}
.dias{display:flex;gap:8px;margin:6px 0 4px}
.dias label{flex:1;margin:0;color:var(--texto);cursor:pointer}
.dias input{position:absolute;opacity:0}
.dias span{display:flex;flex-direction:column;align-items:center;gap:4px;padding:10px 4px;border:2px solid var(--borde);border-radius:12px;font-size:13px}
.dias input:checked+span{border-color:var(--acento);background:rgba(109,74,255,.1)}
.dias input:disabled+span{opacity:.4}
button{font:inherit;cursor:pointer;border:0;border-radius:10px;padding:10px 14px;background:var(--acento);color:#fff;font-weight:600}
button.gris{background:transparent;color:var(--texto);border:1px solid var(--borde)}
button.rojo{background:var(--mal)}button.chico{padding:6px 10px;font-size:13px}
.fila{border-top:1px solid var(--borde);padding:12px 0}.fila:first-child{border-top:0}
.fila b{word-break:break-all}.acciones{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.acciones button{display:inline-flex;align-items:center;gap:6px}
.acciones .m i{width:22px;height:22px;font-size:11px;box-shadow:none}
.etq{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;font-weight:700;color:#fff}
.activo{background:var(--ok)}.vencido{background:var(--mal)}.bloqueado{background:var(--aviso)}
.aviso{padding:12px 14px;border-radius:10px;margin:0 0 14px;font-size:15px}
.aviso.ok{background:rgba(31,157,87,.15);border:1px solid var(--ok)}.aviso.mal{background:rgba(211,63,73,.15);border:1px solid var(--mal)}
form.una{margin:0}.centro{max-width:380px;margin:12vh auto 0}
"""


def e(t):
    return html.escape(str(t), quote=True)


def _moneda(tipo, texto=""):
    return f'<span class="m {tipo}"><i>{MONEDAS[tipo]}</i>{texto}</span>'


class PanelWeb:
    def __init__(self, rev, servicio, reloj=time.time, hoy=date.today):
        self.rev, self.srv, self.reloj, self.hoy = rev, servicio, reloj, hoy
        self.sesiones = {}       # cookie -> {"rid", "csrf", "vence", "aviso"}
        self.fallos_ip = {}      # ip -> [momentos]
        self.fallos_usr = {}     # usuario -> [momentos]
        self.cache = {}          # rid -> (momento, filas)

    # ------------------------------------------------------------------ entrada
    def manejar(self, metodo, ruta, cabeceras, cuerpo, ip, https):
        """(código, cabeceras, cuerpo en bytes). Solo para rutas que empiezan con /r."""
        if metodo not in ("GET", "HEAD", "POST"):
            return self._resp(405, "No permitido")
        if not https:
            host = cabeceras.get("Host", "")
            if not HOST_RE.match(host):
                return self._resp(400, "Pedido inválido")
            return 308, {"Location": f"https://{host}{BASE}", "Cache-Control": "no-store", "Content-Length": "0"}, b""
        ahora = self.reloj()
        s = self._sesion(cabeceras, ahora)
        if metodo == "POST":
            datos = {k: v[0] for k, v in urllib.parse.parse_qs(cuerpo.decode("utf-8", "replace"), keep_blank_values=True).items()}
            if ruta == BASE + "/entrar":
                return self._entrar(datos, ip, ahora)
            if not s or not secrets.compare_digest(datos.get("csrf", "").encode("utf-8"), s["csrf"].encode("utf-8")):
                return self._ir(BASE)
            if ruta == BASE + "/salir":
                self.sesiones = {k: v for k, v in self.sesiones.items() if v is not s}
                return 303, {"Location": BASE, "Set-Cookie": self._cookie("", 0), "Cache-Control": "no-store",
                             "Content-Length": "0"}, b""
            if ruta == BASE + "/accion":
                return self._accion(s, datos)
            return self._resp(404, "No existe")
        if ruta not in (BASE, BASE + "/"):
            return self._resp(404, "No existe")
        if not s:
            return self._pagina(self._login(), 200)
        return self._pagina(self._inicio(s), 200)

    # ------------------------------------------------------------------ sesión
    def _cookie(self, valor, vida):
        return f"zr={valor}; Path={BASE}; HttpOnly; Secure; SameSite=Strict; Max-Age={vida}"

    def _sesion(self, cabeceras, ahora):
        for parte in cabeceras.get("Cookie", "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "zr" and v in self.sesiones:
                s = self.sesiones[v]
                r = self.rev.buscar(s["rid"])
                if s["vence"] > ahora and r and r["activo"]:
                    s["_cookie"] = v
                    return s
                self.sesiones.pop(v, None)
        return None

    def _frenado(self, tabla, clave, fallos, ventana, ahora):
        v = [t for t in tabla.get(clave, []) if ahora - t < ventana]
        tabla[clave] = v
        if len(tabla) > 5000:
            tabla.clear()
        return len(v) >= fallos

    def _fallo(self, tabla, clave, ahora):
        tabla.setdefault(clave, []).append(ahora)

    def _entrar(self, datos, ip, ahora):
        usuario = datos.get("usuario", "").strip().lower()[:40]
        if self._frenado(self.fallos_ip, ip, FALLOS_IP, VENTANA_IP, ahora) or \
                self._frenado(self.fallos_usr, usuario, FALLOS_USUARIO, VENTANA_USUARIO, ahora):
            return self._pagina(self._login("Demasiados intentos. Esperá unos minutos y probá de nuevo."), 429)
        r = self.rev.verificar(usuario, datos.get("clave", ""))
        if not r:
            self._fallo(self.fallos_ip, ip, ahora)
            self._fallo(self.fallos_usr, usuario, ahora)
            return self._pagina(self._login("Usuario o contraseña incorrectos."), 200)
        self.fallos_usr.pop(usuario, None)
        if len(self.sesiones) >= MAX_SESIONES:
            self.sesiones = {k: v for k, v in self.sesiones.items() if v["vence"] > ahora}
            if len(self.sesiones) >= MAX_SESIONES:
                self.sesiones.clear()
        cookie = secrets.token_urlsafe(32)
        self.sesiones[cookie] = {"rid": r["id"], "csrf": secrets.token_urlsafe(24), "vence": ahora + VIDA_SESION, "aviso": None}
        return 303, {"Location": BASE, "Set-Cookie": self._cookie(cookie, VIDA_SESION), "Cache-Control": "no-store",
                     "Content-Length": "0"}, b""

    # ------------------------------------------------------------------ acciones
    def _ir(self, destino):
        return 303, {"Location": destino, "Cache-Control": "no-store", "Content-Length": "0"}, b""

    def _accion(self, s, d):
        rid = s["rid"]
        a, token = d.get("a", ""), d.get("token", "").strip()
        try:
            if a == "crear":
                dias = int(d.get("dias", "0") or 0)
                res = self.srv.crear(rid, token, d.get("nombre", ""), dias)
                s["aviso"] = ("ok", f"✅ Usuario creado. Token: {res['token']} · vence el {res['vence']:%d/%m/%Y} "
                                    f"· gastó 1 moneda de {res['moneda']}.")
            elif a in ("r7", "r15", "r30"):
                res = self.srv.renovar(rid, token, int(a[1:]))
                s["aviso"] = ("ok", f"✅ Renovado {a[1:]} días. Ahora vence el {res['vence']:%d/%m/%Y} · gastó 1 moneda de {res['moneda']}.")
            elif a in ("bloquear", "desbloquear"):
                self.srv.bloquear(rid, token, a == "bloquear")
                s["aviso"] = ("ok", "✅ Usuario bloqueado." if a == "bloquear" else "✅ Usuario desbloqueado.")
            elif a == "eliminar":
                return self._pagina(self._confirmar(s, token), 200)
            elif a == "eliminar_ok":
                self.srv.eliminar(rid, token)
                s["aviso"] = ("ok", "✅ Usuario eliminado.")
            else:
                s["aviso"] = ("mal", "Esa acción no existe.")
        except ValueError:
            s["aviso"] = ("mal", "Dato inválido.")
        except ErrorRevendedor as ex:
            s["aviso"] = ("mal", f"⚠️ {ex}")
        self.cache.pop(rid, None)
        return self._ir(BASE)

    # ------------------------------------------------------------------ páginas
    def _resp(self, codigo, texto):
        cuerpo = texto.encode("utf-8")
        return codigo, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store",
                        "Content-Length": str(len(cuerpo))}, cuerpo

    def _pagina(self, contenido, codigo):
        cuerpo = ("<!doctype html><html lang=es><head><meta charset=utf-8>"
                  "<meta name=viewport content='width=device-width,initial-scale=1'><meta name=robots content=noindex>"
                  f"<title>Panel de revendedores</title><style>{CSS}</style></head><body><main>{contenido}</main></body></html>"
                  ).encode("utf-8")
        return codigo, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                        "Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer", "Content-Length": str(len(cuerpo))}, cuerpo

    def _login(self, error=""):
        aviso = f'<div class="aviso mal">{e(error)}</div>' if error else ""
        return ('<div class="centro"><div class="tarjeta"><h1>🛡 Panel de revendedores</h1>'
                f'<p class="suave">Entrá con el usuario y la contraseña que te dio el administrador.</p>{aviso}'
                f'<form method="post" action="{BASE}/entrar" autocomplete="on">'
                '<label>Usuario</label><input type="text" name="usuario" autocomplete="username" autocapitalize="none" required>'
                '<label>Contraseña</label><input type="password" name="clave" autocomplete="current-password" required>'
                '<p><button type="submit" style="width:100%">Entrar</button></p></form></div></div>')

    def _cabecera(self, s, r):
        m = "".join(_moneda(t, f"× {r['monedas'].get(t, 0)}") for t in MONEDAS)
        return ('<div class="barra"><div><h1>👋 ' + e(r["usuario"]) + '</h1><span class="suave">Panel de revendedores</span></div>'
                f'<form class="una" method="post" action="{BASE}/salir"><input type="hidden" name="csrf" value="{e(s["csrf"])}">'
                '<button class="gris chico">Salir</button></form></div>'
                f'<div class="tarjeta"><div class="suave" style="margin-bottom:8px">Tus monedas</div><div class="monedas">{m}</div></div>')

    def _inicio(self, s):
        r = self.rev.buscar(s["rid"])
        partes = [self._cabecera(s, r)]
        if s.get("aviso"):
            tipo, texto = s["aviso"]
            partes.append(f'<div class="aviso {tipo}">{e(texto)}</div>')
            s["aviso"] = None
        partes.append(self._form_crear(s, r))
        partes.append(self._lista(s, r))
        return "".join(partes)

    def _form_crear(self, s, r):
        opciones = ""
        for i, (t, d) in enumerate(MONEDAS.items()):
            n = r["monedas"].get(t, 0)
            opciones += (f'<label><input type="radio" name="dias" value="{d}" {"disabled" if n < 1 else ""} '
                         f'{"checked" if i == 0 and n > 0 else ""}><span>{_moneda(t)}<b>{d} días</b>'
                         f'<small class="suave">{n} disponibles</small></span></label>')
        return ('<div class="tarjeta"><h2>➕ Crear usuario</h2>'
                f'<form method="post" action="{BASE}/accion"><input type="hidden" name="csrf" value="{e(s["csrf"])}">'
                '<input type="hidden" name="a" value="crear">'
                '<label>Token del cliente (8 a 32 letras y números)</label>'
                '<input type="text" name="token" maxlength="32" autocapitalize="none" autocomplete="off" required>'
                '<label>Nombre del cliente</label><input type="text" name="nombre" maxlength="48" autocomplete="off" required>'
                f'<label>Duración</label><div class="dias">{opciones}</div>'
                '<p><button type="submit">Crear usuario</button></p></form></div>')

    def _filas(self, rid):
        ahora = self.reloj()
        c = self.cache.get(rid)
        if c and ahora - c[0] < CACHE_LISTA:
            return c[1]
        try:
            filas = self.srv.listar(rid)
        except ErrorRevendedor as ex:
            filas = str(ex)
        self.cache[rid] = (ahora, filas)
        return filas

    def _lista(self, s, r):
        filas = self._filas(r["id"])
        if isinstance(filas, str):
            return f'<div class="tarjeta"><h2>👥 Tus usuarios</h2><div class="aviso mal">⚠️ {e(filas)}</div></div>'
        if not filas:
            return '<div class="tarjeta"><h2>👥 Tus usuarios</h2><p class="suave">Todavía no creaste ninguno.</p></div>'
        hoy = self.hoy()
        out = [f'<div class="tarjeta"><h2>👥 Tus usuarios · {len(filas)}</h2>']
        if filas[0].get("sin_datos"):
            out.append('<div class="aviso mal">No pude consultar la VPS ahora: puede que no veas la fecha ni el estado.</div>')
        for f in filas:
            if not f["existe"]:
                estado, cls = "No existe en la VPS", "vencido"
            elif f["bloqueado"]:
                estado, cls = "Bloqueado", "bloqueado"
            elif f["vence"] and f["vence"] < hoy:
                estado, cls = "Vencido", "vencido"
            else:
                estado, cls = "Activo", "activo"
            if f["vence"]:
                dias = (f["vence"] - hoy).days
                vence = f'vence {f["vence"]:%d/%m/%Y}' + (f" · {dias} d" if dias >= 0 else "")
            else:
                vence = "sin fecha"
            tok = e(f["token"])
            botones = "".join(f'<button class="gris chico" name="a" value="r{d}">{_moneda(t, "renovar")}</button>'
                              for t, d in MONEDAS.items())
            bloqueo = ('<button class="gris chico" name="a" value="desbloquear">▶ Desbloquear</button>' if f["bloqueado"]
                       else '<button class="gris chico" name="a" value="bloquear">⏸ Bloquear</button>')
            out.append(f'<div class="fila"><form class="una" method="post" action="{BASE}/accion">'
                       f'<input type="hidden" name="csrf" value="{e(s["csrf"])}"><input type="hidden" name="token" value="{tok}">'
                       f'<b>{e(f["nombre"])}</b> <span class="etq {cls}">{estado}</span><br>'
                       f'<span class="suave">{tok} · {vence}</span>'
                       f'<div class="acciones">{botones}{bloqueo}'
                       '<button class="gris chico" name="a" value="eliminar">🗑 Eliminar</button></div></form></div>')
        out.append("</div>")
        return "".join(out)

    def _confirmar(self, s, token):
        propias = self.rev.cuentas_de(s["rid"])
        nombre = propias.get(token, {}).get("etq", token)
        return (f'<div class="centro"><div class="tarjeta"><h2>🗑 ¿Eliminar a {e(nombre)}?</h2>'
                '<p>Se borra de la VPS y deja de poder conectar. Las monedas gastadas no se devuelven.</p>'
                f'<form class="una" method="post" action="{BASE}/accion"><input type="hidden" name="csrf" value="{e(s["csrf"])}">'
                f'<input type="hidden" name="token" value="{e(token)}"><input type="hidden" name="a" value="eliminar_ok">'
                '<button class="rojo">Sí, eliminar</button> '
                f'</form><p><a href="{BASE}">No, volver</a></p></div></div>')
