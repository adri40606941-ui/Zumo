"""Panel web de revendedores (https://tu-dominio/r).

Cada revendedor entra con el usuario y la contraseña que le creó el administrador desde el bot, y puede:
crear usuarios (token + nombre + 7/15/30 días), renovarlos, bloquearlos/desbloquearlos y eliminarlos. Crear y renovar
gastan monedas (bronce 7 días, plata 15, oro 30); lo demás es gratis. Solo ve y toca los usuarios que él creó.

Seguridad: CSP estricta (el único script es un aviso chico, con nonce, y todo anda también sin él), cookie de sesión
HttpOnly + SameSite=Lax + Secure, token CSRF en cada formulario, contraseñas con scrypt, freno de intentos por IP y
por usuario, y solo funciona por https (por http redirige). Se engancha al servidor web del bot (publico.py) con
manejar(). El modo oscuro se elige con un botón (cookie "zt"), sin JavaScript.
"""
import html
import re
import secrets
import threading
import time
import urllib.parse
from datetime import date

from revendedores import MONEDAS, ErrorRevendedor

BASE = "/r"
VIDA_SESION = 12 * 3600
MAX_SESIONES = 500
FALLOS_IP, VENTANA_IP = 10, 300
FALLOS_USUARIO, VENTANA_USUARIO = 8, 600
CACHE_LISTA = 15
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-%s'; img-src data:; form-action 'self'; "
       "base-uri 'none'; frame-ancestors 'none'")

CSS = """
:root{--bg:#f3f4f8;--tarjeta:#fff;--texto:#1b1d27;--suave:#6a6f82;--borde:#e1e3ea;--acento:#6d4aff;--ok:#1f9d57;--mal:#d33f49;--aviso:#c98a00;--campo:#f3f4f8}
@media(prefers-color-scheme:dark){:root:not([data-tema]){--bg:#0d0f16;--tarjeta:#171a25;--texto:#eceef6;--suave:#9aa0b6;--borde:#292d3e;--acento:#9a85ff;--ok:#46c47f;--mal:#f0636c;--aviso:#e8b23a;--campo:#0f121b}}
:root[data-tema=oscuro]{--bg:#0d0f16;--tarjeta:#171a25;--texto:#eceef6;--suave:#9aa0b6;--borde:#292d3e;--acento:#9a85ff;--ok:#46c47f;--mal:#f0636c;--aviso:#e8b23a;--campo:#0f121b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--texto);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0}h2{font-size:17px;margin:0 0 12px}
.tarjeta{background:var(--tarjeta);border:1px solid var(--borde);border-radius:16px;padding:16px;margin:0 0 14px}
.barra{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 14px}
.barra .der{display:flex;gap:8px;align-items:center}
.suave{color:var(--suave);font-size:14px}
.monedas{display:flex;gap:8px}
.mon{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;text-align:center;gap:6px;padding:12px 6px;border:1px solid var(--borde);border-radius:14px;background:var(--campo)}
.mon svg{width:46px;height:46px;flex:none;filter:drop-shadow(0 2px 2px rgba(0,0,0,.3))}
.mon b{font-size:22px;line-height:1}.mon small{display:block;color:var(--suave);font-size:12px}
label{display:block;font-size:14px;color:var(--suave);margin:10px 0 4px}
input[type=text],input[type=password]{width:100%;padding:12px;border-radius:12px;border:1px solid var(--borde);background:var(--campo);color:var(--texto);font-size:16px}
.dias{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:6px 0 4px}
.dias label{margin:0;color:var(--texto);cursor:pointer;position:relative}
.dias input{position:absolute;opacity:0;pointer-events:none}
.dias .op{display:flex;flex-direction:column;align-items:center;gap:2px;padding:10px 4px;border:2px solid var(--borde);border-radius:14px;background:var(--campo);text-align:center}
.dias .op svg{width:52px;height:52px;filter:drop-shadow(0 2px 2px rgba(0,0,0,.3))}
.dias .op b{font-size:15px}.dias .op small{color:var(--suave);font-size:12px}
.dias input:checked+.op{border-color:var(--acento);box-shadow:0 0 0 3px rgba(109,74,255,.2)}
.dias .sin .op{opacity:.5}.dias .sin svg{filter:grayscale(1)}
button{font:inherit;cursor:pointer;border:0;border-radius:12px;padding:11px 16px;background:var(--acento);color:#fff;font-weight:600}
button.gris{background:transparent;color:var(--texto);border:1px solid var(--borde)}
button.rojo{background:var(--mal)}button.chico{padding:6px 10px;font-size:13px}
button.icono{display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;padding:0;border-radius:50%}
button.icono svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.b-sol{display:none}
:root[data-tema=oscuro] .b-luna{display:none}:root[data-tema=oscuro] .b-sol{display:inline-flex}
@media(prefers-color-scheme:dark){:root:not([data-tema]) .b-luna{display:none}:root:not([data-tema]) .b-sol{display:inline-flex}}
.fila{border-top:1px solid var(--borde);padding:12px 0}.fila:first-child{border-top:0}
.fila b{word-break:break-all}.acciones{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.acciones button{display:inline-flex;align-items:center;gap:6px}
.acciones svg{width:22px;height:22px}
.punto{display:inline-block;width:11px;height:11px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 3px rgba(31,157,87,.25);vertical-align:middle;margin-right:6px}
details.edit{margin-top:8px}details.edit>summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--borde);border-radius:99px;font-size:14px;font-weight:600}
details.edit>summary::-webkit-details-marker{display:none}details.edit[open]>summary{border-color:var(--acento)}
details.edit svg.lapiz{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.nom{display:flex;gap:6px;margin-top:10px}.nom input{flex:1;min-width:0;padding:8px 10px}
.etq{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;font-weight:700;color:#fff}
.activo{background:var(--ok)}.vencido{background:var(--mal)}.bloqueado{background:var(--aviso)}
.aviso{padding:12px 14px;border-radius:12px;margin:0 0 14px;font-size:15px}
.aviso.ok{background:rgba(31,157,87,.15);border:1px solid var(--ok)}.aviso.mal{background:rgba(211,63,73,.15);border:1px solid var(--mal)}
#sin-moneda{display:none;margin:8px 0 0}
form.una{margin:0}.centro{max-width:380px;margin:10vh auto 0}
.tope{display:flex;justify-content:flex-end;max-width:380px;margin:0 auto}
"""

# Las tres monedas, dibujadas una sola vez y reusadas con <use>. Cada una tiene borde acanalado, aro, cara con degradé,
# el número en relieve y un brillo arriba a la izquierda.
_COLORES = {
    "bronce": ("#f6c08c", "#c9733a", "#6e3510", "#8a4518", "#fff1e2"),
    "plata": ("#ffffff", "#cfd4df", "#6d7587", "#4a5164", "#ffffff"),
    "oro": ("#fff6b8", "#f5c93a", "#a56f00", "#7a5200", "#fffbe0"),
}


def _simbolos():
    out = ['<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>']
    for tipo, (claro, medio, oscuro, num, brillo) in _COLORES.items():
        out.append(f'<radialGradient id="g-{tipo}" cx=".35" cy=".3" r=".9"><stop offset="0" stop-color="{claro}"/>'
                   f'<stop offset=".55" stop-color="{medio}"/><stop offset="1" stop-color="{oscuro}"/></radialGradient>'
                   f'<linearGradient id="b-{tipo}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{claro}"/>'
                   f'<stop offset=".5" stop-color="{medio}"/><stop offset="1" stop-color="{oscuro}"/></linearGradient>')
    out.append("</defs>")
    for tipo, (claro, medio, oscuro, num, brillo) in _COLORES.items():
        n = MONEDAS[tipo]
        tam = 25 if n >= 10 else 30
        out.append(
            f'<symbol id="m-{tipo}" viewBox="0 0 64 64">'
            f'<circle cx="32" cy="32" r="30.5" fill="{oscuro}"/>'
            f'<circle cx="32" cy="32" r="29.5" fill="url(#b-{tipo})"/>'
            f'<circle cx="32" cy="32" r="28" fill="none" stroke="{oscuro}" stroke-opacity=".55" stroke-width="2.4" stroke-dasharray="1.6 1.9"/>'
            f'<circle cx="32" cy="32" r="24.5" fill="url(#g-{tipo})" stroke="{oscuro}" stroke-opacity=".6" stroke-width="1.2"/>'
            f'<circle cx="32" cy="32" r="21.5" fill="none" stroke="{brillo}" stroke-opacity=".7" stroke-width="1"/>'
            f'<text x="32" y="{32 + tam * .36 + 1.4:.1f}" text-anchor="middle" font-family="Georgia,\'Times New Roman\',serif" font-weight="700" '
            f'font-size="{tam}" fill="{brillo}" fill-opacity=".85">{n}</text>'
            f'<text x="32" y="{32 + tam * .36:.1f}" text-anchor="middle" font-family="Georgia,\'Times New Roman\',serif" font-weight="700" '
            f'font-size="{tam}" fill="{num}">{n}</text>'
            f'<path d="M14 24 A20 20 0 0 1 30 12" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width="3" stroke-linecap="round"/>'
            '</symbol>')
    out.append("</svg>")
    return "".join(out)


SIMBOLOS = _simbolos()
LUNA = '<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
SOL = ('<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
       'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')

# Si tocás una moneda que no tenés, avisa en el acto (sin esto, el servidor igual avisa al crear).
SCRIPT = """
(function(){var m=document.getElementById('sin-moneda'),t;
document.querySelectorAll('input[name=dias]').forEach(function(r){r.addEventListener('click',function(e){
if(r.getAttribute('data-n')==='0'){e.preventDefault();m.textContent='No disponés de la moneda de '+r.getAttribute('data-t')+' ('+r.value+' días).';
m.style.display='block';clearTimeout(t);t=setTimeout(function(){m.style.display='none'},4000);}else{m.style.display='none';}});});})();
"""


def e(t):
    return html.escape(str(t), quote=True)


def _moneda(tipo, tam=22):
    return f'<svg width="{tam}" height="{tam}" viewBox="0 0 64 64" aria-hidden="true"><use href="#m-{tipo}"/></svg>'


class PanelWeb:
    def __init__(self, rev, servicio, reloj=time.time, hoy=date.today):
        self.rev, self.srv, self.reloj, self.hoy = rev, servicio, reloj, hoy
        self.sesiones = {}       # cookie -> {"rid", "csrf", "vence", "aviso"}
        self.fallos_ip = {}      # ip -> [momentos]
        self.fallos_usr = {}     # usuario -> [momentos]
        self.cache = {}          # rid -> (momento, filas)
        self._pedido = threading.local()    # el tema elegido en el pedido que se está atendiendo

    # ------------------------------------------------------------------ entrada
    def manejar(self, metodo, ruta, cabeceras, cuerpo, ip, https):
        """(código, cabeceras, cuerpo en bytes). Solo para rutas que empiezan con /r."""
        if metodo not in ("GET", "HEAD", "POST"):
            return self._resp(405, "No permitido")
        # Los nombres de los encabezados no distinguen mayúsculas: Cloudflare o un proxy pueden mandar "cookie" o
        # "host" en minúscula, y con eso la sesión nunca se encontraba (el login volvía siempre en blanco).
        cabeceras = {k.title(): v for k, v in cabeceras.items()}
        if not https:
            host = cabeceras.get("Host", "")
            if not HOST_RE.match(host):
                return self._resp(400, "Pedido inválido")
            return 308, {"Location": f"https://{host}{BASE}", "Cache-Control": "no-store", "Content-Length": "0"}, b""
        ahora = self.reloj()
        self._pedido.tema = self._cookie_de(cabeceras, "zt")
        if self._pedido.tema not in ("oscuro", "claro"):
            self._pedido.tema = ""
        s = self._sesion(cabeceras, ahora)
        if metodo == "POST":
            datos = {k: v[0] for k, v in urllib.parse.parse_qs(cuerpo.decode("utf-8", "replace"), keep_blank_values=True).items()}
            if ruta == BASE + "/tema":
                t = datos.get("t") if datos.get("t") in ("oscuro", "claro") else ""
                return 303, {"Location": BASE, "Set-Cookie": f"zt={t or 'x'}; Path={BASE}; Secure; SameSite=Lax; "
                                                              f"Max-Age={365 * 86400 if t else 0}",
                             "Cache-Control": "no-store", "Content-Length": "0"}, b""
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
        return self._pagina(self._inicio(s), 200, con_script=True)

    # ------------------------------------------------------------------ sesión
    def _cookie(self, valor, vida):
        return f"zr={valor}; Path={BASE}; HttpOnly; Secure; SameSite=Lax; Max-Age={vida}"

    @staticmethod
    def _cookie_de(cabeceras, nombre):
        for parte in cabeceras.get("Cookie", "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == nombre:
                return v
        return ""

    def _sesion(self, cabeceras, ahora):
        v = self._cookie_de(cabeceras, "zr")
        if v and v in self.sesiones:
            s = self.sesiones[v]
            r = self.rev.buscar(s["rid"])
            if s["vence"] > ahora and r and r["activo"]:
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
                if not d.get("dias"):
                    raise ErrorRevendedor("Elegí la duración: 7, 15 o 30 días.")
                dias = int(d["dias"])
                res = self.srv.crear(rid, token, d.get("nombre", ""), dias)
                s["aviso"] = ("ok", f"✅ Usuario creado. Token: {res['token']} · vence el {res['vence']:%d/%m/%Y} "
                                    f"· gastó 1 moneda de {res['moneda']}.")
            elif a in ("r7", "r15", "r30"):
                res = self.srv.renovar(rid, token, int(a[1:]))
                s["aviso"] = ("ok", f"✅ Renovado {a[1:]} días. Ahora vence el {res['vence']:%d/%m/%Y} · gastó 1 moneda de {res['moneda']}.")
            elif a in ("bloquear", "desbloquear"):
                self.srv.bloquear(rid, token, a == "bloquear")
                s["aviso"] = ("ok", "✅ Usuario bloqueado." if a == "bloquear" else "✅ Usuario desbloqueado.")
            elif a == "renombrar":
                n = self.srv.renombrar(rid, token, d.get("nombre", ""))
                s["aviso"] = ("ok", f"✅ Nombre cambiado a «{n}».")
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

    def _pagina(self, contenido, codigo, con_script=False):
        tema = getattr(self._pedido, "tema", "")
        nonce = secrets.token_urlsafe(12)
        script = f'<script nonce="{nonce}">{SCRIPT}</script>' if con_script else ""
        cuerpo = (f'<!doctype html><html lang=es{f" data-tema={tema}" if tema else ""}><head><meta charset=utf-8>'
                  "<meta name=viewport content='width=device-width,initial-scale=1'><meta name=robots content=noindex>"
                  f"<meta name=color-scheme content='light dark'><title>Panel de revendedores</title><style>{CSS}</style></head>"
                  f"<body>{SIMBOLOS}<main>{contenido}</main>{script}</body></html>").encode("utf-8")
        return codigo, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                        "Content-Security-Policy": CSP % nonce, "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer", "Content-Length": str(len(cuerpo))}, cuerpo

    def _boton_tema(self):
        """Luna en modo claro, sol en modo oscuro (CSS elige cuál se ve). Funciona sin JavaScript."""
        return (f'<form class="una" method="post" action="{BASE}/tema">'
                f'<button class="gris icono b-luna" name="t" value="oscuro" title="Modo oscuro" aria-label="Modo oscuro">{LUNA}</button>'
                f'<button class="gris icono b-sol" name="t" value="claro" title="Modo claro" aria-label="Modo claro">{SOL}</button></form>')

    def _login(self, error=""):
        aviso = f'<div class="aviso mal">{e(error)}</div>' if error else ""
        return (f'<div class="tope">{self._boton_tema()}</div>'
                '<div class="centro"><div class="tarjeta"><h1>🛡 Panel de revendedores</h1>'
                f'<p class="suave">Entrá con el usuario y la contraseña que te dio el administrador.</p>{aviso}'
                f'<form method="post" action="{BASE}/entrar" autocomplete="on">'
                '<label>Usuario</label><input type="text" name="usuario" autocomplete="username" autocapitalize="none" required>'
                '<label>Contraseña</label><input type="password" name="clave" autocomplete="current-password" required>'
                '<p><button type="submit" style="width:100%">Entrar</button></p></form></div></div>')

    def _cabecera(self, s, r):
        nombres = {"bronce": "Bronce", "plata": "Plata", "oro": "Oro"}
        m = "".join(f'<div class="mon">{_moneda(t, 46)}<div><b>× {r["monedas"].get(t, 0)}</b>'
                    f'<small>{nombres[t]} · {d} días</small></div></div>' for t, d in MONEDAS.items())
        return ('<div class="barra"><div><h1>👋 ' + e(r["usuario"]) + '</h1><span class="suave">Panel de revendedores</span></div>'
                f'<div class="der">{self._boton_tema()}'
                f'<form class="una" method="post" action="{BASE}/salir"><input type="hidden" name="csrf" value="{e(s["csrf"])}">'
                '<button class="gris chico">Salir</button></form></div></div>'
                f'<div class="tarjeta"><div class="suave" style="margin-bottom:8px">Tus monedas</div><div class="monedas">{m}</div></div>')

    def _inicio(self, s):
        r = self.rev.buscar(s["rid"])
        partes = [self._cabecera(s, r)]
        if s.get("aviso"):
            tipo, texto = s["aviso"]
            partes.append(f'<div class="aviso {tipo}" role="alert">{e(texto)}</div>')
            s["aviso"] = None
        partes.append(self._form_crear(s, r))
        partes.append(self._lista(s, r))
        return "".join(partes)

    def _form_crear(self, s, r):
        opciones, marcado = "", False
        for t, d in MONEDAS.items():
            n = r["monedas"].get(t, 0)
            check = ""
            if n > 0 and not marcado:
                check, marcado = " checked", True
            opciones += (f'<label class="{"sin" if n < 1 else ""}"><input type="radio" name="dias" value="{d}" '
                         f'data-n="{n}" data-t="{t}"{check}><span class="op">{_moneda(t, 52)}<b>{d} días</b>'
                         f'<small>{"Tenés " + str(n) if n else "No tenés"}</small></span></label>')
        return ('<div class="tarjeta"><h2>➕ Crear usuario</h2>'
                f'<form method="post" action="{BASE}/accion"><input type="hidden" name="csrf" value="{e(s["csrf"])}">'
                '<input type="hidden" name="a" value="crear">'
                '<label>Token del cliente (8 a 32 letras y números)</label>'
                '<input type="text" name="token" maxlength="32" autocapitalize="none" autocomplete="off" required>'
                '<label>Nombre del cliente</label><input type="text" name="nombre" maxlength="48" autocomplete="off" required>'
                f'<label>Duración (gasta una moneda)</label><div class="dias">{opciones}</div>'
                '<div id="sin-moneda" class="aviso mal" role="alert"></div>'
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
            botones = "".join(f'<button class="gris chico" name="a" value="r{d}" title="Renovar {d} días">{_moneda(t, 22)}'
                              f'Renovar {d}</button>' for t, d in MONEDAS.items())
            bloqueo = ('<button class="gris chico" name="a" value="desbloquear">▶ Desbloquear</button>' if f["bloqueado"]
                       else '<button class="gris chico" name="a" value="bloquear">🔒 Bloquear</button>')
            punto = ('<span class="punto" role="img" aria-label="Conectado" title="Conectado ahora"></span>'
                     if f.get("conectado") else "")
            lapiz = ('<svg class="lapiz" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/>'
                     '<path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>')
            out.append(f'<div class="fila"><form class="una" method="post" action="{BASE}/accion">'
                       f'<input type="hidden" name="csrf" value="{e(s["csrf"])}"><input type="hidden" name="token" value="{tok}">'
                       f'{punto}<b>{e(f["nombre"])}</b> <span class="etq {cls}">{estado}</span>'
                       f'{" <span class=suave>· conectado</span>" if punto else ""}<br>'
                       f'<span class="suave">{tok} · {vence}</span>'
                       f'<details class="edit"><summary>{lapiz} Editar</summary>'
                       f'<div class="nom"><input type="text" name="nombre" value="{e(f["nombre"])}" maxlength="48" '
                       'autocomplete="off" aria-label="Nombre del usuario">'
                       '<button class="chico" name="a" value="renombrar">Guardar nombre</button></div>'
                       f'<div class="acciones">{botones}{bloqueo}'
                       '<button class="gris chico" name="a" value="eliminar">🗑 Eliminar</button></div></details></form></div>')
        out.append("</div>")
        return "".join(out)

    def _confirmar(self, s, token):
        propias = self.rev.cuentas_de(s["rid"])
        nombre = propias.get(token, {}).get("etq", token)
        return (f'<div class="tope">{self._boton_tema()}</div>'
                f'<div class="centro"><div class="tarjeta"><h2>🗑 ¿Eliminar a {e(nombre)}?</h2>'
                '<p>Se borra de la VPS y deja de poder conectar. Las monedas gastadas no se devuelven.</p>'
                f'<form class="una" method="post" action="{BASE}/accion"><input type="hidden" name="csrf" value="{e(s["csrf"])}">'
                f'<input type="hidden" name="token" value="{e(token)}"><input type="hidden" name="a" value="eliminar_ok">'
                '<button class="rojo">Sí, eliminar</button> '
                f'</form><p><a href="{BASE}">No, volver</a></p></div></div>')
