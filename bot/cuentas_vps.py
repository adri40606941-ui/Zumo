"""Usuarios (tokens) en una VPS enlazada por SSH: crear, renovar, bloquear, desbloquear, eliminar y consultar.

Hace lo mismo que el bot hace en su propia VPS (ver crear_hwid / renovar_usuario / borrar_usuario en zumo-bot.py),
pero ejecutando los comandos en la máquina remota con maquinas.correr. Hace falta que esa VPS tenga el panel
instalado (/etc/zumo/zumo-lib.sh): de ahí salen las funciones que anotan el usuario en usuarios.db.

El token es el HWID del cliente: es a la vez el usuario y la contraseña de Linux, y el nombre del cliente va en el
GECOS como "hwid,<nombre>" (así lo reconoce el panel).
"""
import re
import shlex
from datetime import date, timedelta

import maquinas as mq

TOKEN_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
LIB = "/etc/zumo/zumo-lib.sh"
DB = "/etc/zumo/usuarios.db"


class ErrorCuenta(Exception):
    """No se pudo hacer algo con un usuario (el texto se muestra tal cual)."""


def limpiar_nombre(t):
    """El nombre del cliente va en el GECOS: sin ':' ni caracteres de control, máx. 48."""
    return re.sub(r"[\x00-\x1f\x7f:,]", "", t or "").strip()[:48] or "cliente"


def _cuenta_hasta(exp):
    """La cuenta de Linux vence un día después: el corte exacto lo hace el limitador."""
    return (exp + timedelta(days=1)).isoformat()


def _sh(m, script, correr=None, timeout=30):
    """Corre un script de bash en la máquina y devuelve su salida."""
    f = correr or mq.correr
    try:
        return f(m, "bash -c " + shlex.quote(script), timeout=timeout)
    except mq.ErrorMaquina as e:
        raise ErrorCuenta(str(e))
    except Exception as e:      # tiempo agotado, conexión cortada…
        raise ErrorCuenta(f"No pude hablar con la VPS ({e.__class__.__name__}).")


def _token(t):
    if not TOKEN_RE.match(t or ""):
        raise ErrorCuenta("El token tiene que tener de 8 a 32 letras y números (sin espacios ni guiones).")
    return t


CABECERA = f'[ -f {LIB} ] || {{ echo SINPANEL; exit 0; }}\nsource {LIB}\n'


def crear(m, token, nombre, dias, limite=1, hoy=None, correr=None):
    """Crea el usuario. Devuelve la fecha en que vence (date)."""
    t = _token(token)
    exp = (hoy or date.today()) + timedelta(days=int(dias))
    script = (CABECERA +
              f'if id {t} >/dev/null 2>&1; then echo EXISTE; exit 0; fi\n'
              f'useradd --badname -M -s /bin/false -e {_cuenta_hasta(exp)} -c {shlex.quote("hwid," + limpiar_nombre(nombre))} {t} '
              '|| { echo ERROR; exit 0; }\n'
              f'echo {shlex.quote(t + ":" + t)} | chpasswd\n'
              f'zumo_db_add {t} {int(limite)} {exp.isoformat()}\n'
              'echo OK\n')
    r = _sh(m, script, correr)
    if "SINPANEL" in r:
        raise ErrorCuenta("Esa VPS no tiene el panel instalado, así que no puedo crear usuarios ahí.")
    if "EXISTE" in r:
        raise ErrorCuenta("Ese token ya existe en la VPS.")
    if "OK" not in r:
        raise ErrorCuenta("La VPS no pudo crear el usuario.")
    return exp


def _vence_actual(m, t, correr):
    """(existe, vence o None) de ese token en la VPS."""
    r = _sh(m, f'id {t} >/dev/null 2>&1 && echo EXISTE || echo NO\ngrep "^{t}:" {DB} 2>/dev/null | head -1', correr)
    if "EXISTE" not in r:
        return False, None
    for linea in r.splitlines():
        p = linea.strip().split(":")
        if len(p) >= 3 and p[0] == t:
            try:
                return True, date.fromisoformat(p[2])
            except ValueError:
                return True, None
    return True, None


def renovar(m, token, dias, hoy=None, correr=None):
    """Suma los días a su vencimiento (si ya venció, cuenta desde hoy). Devuelve la nueva fecha."""
    t = _token(token)
    hoy = hoy or date.today()
    existe, actual = _vence_actual(m, t, correr)
    if not existe:
        raise ErrorCuenta("Ese usuario ya no existe en la VPS.")
    exp = max(actual or hoy, hoy) + timedelta(days=int(dias))
    script = (CABECERA +
              f'usermod -e {_cuenta_hasta(exp)} {t} || {{ echo ERROR; exit 0; }}\n'
              f'zumo_db_set {t} 3 {exp.isoformat()}\n'
              f'sed -i "/^{t}:/d" /etc/zumo/datos.db 2>/dev/null\n'      # el contador de datos vuelve a cero
              'echo OK\n')
    r = _sh(m, script, correr)
    if "SINPANEL" in r:
        raise ErrorCuenta("Esa VPS no tiene el panel instalado.")
    if "OK" not in r:
        raise ErrorCuenta("La VPS no pudo renovar el usuario.")
    return exp


def eliminar(m, token, correr=None):
    t = _token(token)
    script = (f'[ -f {LIB} ] && source {LIB}\n'
              f'pkill -9 -u {t} 2>/dev/null\nuserdel {t} 2>/dev/null\n'
              f'if command -v zumo_db_del >/dev/null 2>&1; then zumo_db_del {t}; else sed -i "/^{t}:/d" {DB}; fi\n'
              f'sed -i "/^{t}:/d" /etc/zumo/datos.db /etc/zumo/datos-hist.db 2>/dev/null\n'
              f'id {t} >/dev/null 2>&1 && echo QUEDA || echo OK\n')
    if "OK" not in _sh(m, script, correr):
        raise ErrorCuenta("La VPS no pudo eliminar el usuario.")


def bloquear(m, token, si=True, correr=None):
    """Bloquea (corta la sesión y no deja entrar) o desbloquea. No toca la fecha de vencimiento."""
    t = _token(token)
    if si:
        script = f'usermod -L {t} && pkill -9 -u {t} 2>/dev/null; id {t} >/dev/null 2>&1 && echo OK || echo NO\n'
    else:
        script = f'usermod -U {t} && echo OK || echo NO\n'
    if "OK" not in _sh(m, script, correr):
        raise ErrorCuenta("La VPS no pudo cambiar el bloqueo (¿el usuario ya no existe?).")


def estado(m, tokens, correr=None):
    """{token: {'existe', 'vence' (date|None), 'bloqueado'}} de esos tokens, en una sola conexión."""
    tokens = [_token(t) for t in tokens]
    if not tokens:
        return {}
    script = ('for u in ' + " ".join(tokens) + '; do\n'
              '  if id "$u" >/dev/null 2>&1; then\n'
              f'    v=$(grep "^$u:" {DB} 2>/dev/null | head -1 | cut -d: -f3)\n'
              '    b=$(passwd -S "$u" 2>/dev/null | cut -d" " -f2)\n'
              '    echo "$u SI ${v:--} ${b:--}"\n'
              '  else echo "$u NO - -"; fi\n'
              'done\n')
    out = {}
    for linea in _sh(m, script, correr).splitlines():
        p = linea.split()
        if len(p) != 4 or p[0] not in tokens:
            continue
        try:
            vence = date.fromisoformat(p[2]) if p[2] != "-" else None
        except ValueError:
            vence = None
        out[p[0]] = {"existe": p[1] == "SI", "vence": vence, "bloqueado": p[3] == "L"}
    return out
