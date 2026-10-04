#!/usr/bin/env python3
"""Panel web ZUMO: crear/borrar/editar usuarios y ver quién está conectado,
desde el navegador. Usa la misma base /etc/zumo/usuarios.db que panel.sh."""
import fcntl
import os
import re
import secrets
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from urllib.parse import urlparse

from flask import Flask, request, redirect, url_for, session, render_template_string, jsonify, abort
from werkzeug.security import check_password_hash

CONF_PATH = "/etc/zumo/web.conf"
DB_PATH = "/etc/zumo/usuarios.db"
LOCK_PATH = "/etc/zumo/usuarios.lock"

# Mismo formato y mismo lock que usa panel.sh (zumo-lib.sh). Toda lectura-
# modificación-escritura del DB se hace con el lock tomado para que el panel
# de la terminal y el panel web no se pisen entre sí.


class _db_lock:
    """Lock exclusivo sobre /etc/zumo/usuarios.lock (compatible con flock de bash)."""

    def __enter__(self):
        self._f = open(LOCK_PATH, "w")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        try:
            fcntl.flock(self._f, fcntl.LOCK_UN)
        finally:
            self._f.close()


def _escribir_db_atomico(lineas):
    """Escribe el DB completo de forma atómica (tmp + rename) para no dejarlo
    corrupto si algo falla a mitad de camino. Asumí el lock ya tomado."""
    destino = os.path.dirname(DB_PATH) or "."
    fd, tmp = tempfile.mkstemp(dir=destino, prefix=".usuarios.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lineas) + ("\n" if lineas else ""))
        os.replace(tmp, DB_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _limpiar_etiqueta(s):
    """La etiqueta se guarda en el GECOS (/etc/passwd, separado por ':'), así que
    no puede llevar ':' ni saltos de línea o corrompería esa línea del passwd."""
    s = (s or "cliente").strip()
    s = re.sub(r"[\x00-\x1f\x7f:]", "", s)
    return s[:48] or "cliente"


def _password_valido(p):
    """Sin caracteres de control (romperían la línea 'usuario:pass' de chpasswd)
    y con un tope de largo razonable."""
    if not p or len(p) > 128:
        return False
    return not re.search(r"[\x00-\x1f\x7f]", p)


def cargar_config():
    conf = {}
    for line in Path(CONF_PATH).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        conf[k.strip()] = v.strip()
    return conf


CONF = cargar_config()
app = Flask(__name__)
app.secret_key = CONF.get("SECRET_KEY") or secrets.token_hex(32)
# La cookie de sesión no viaja en peticiones que vienen de otro sitio: frena
# que una página ajena dispare acciones del panel (CSRF) desde tu navegador.
app.config.update(SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True)


@app.before_request
def _mismo_origen():
    """Las acciones (POST) solo se aceptan si el navegador dice que salen de
    este mismo panel. Doble defensa junto con SameSite=Strict."""
    if request.method != "POST":
        return
    origen = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if origen and urlparse(origen).netloc != request.host:
        abort(403)


def _usuario_del_panel(usuario):
    """Solo se puede operar sobre usuarios que están en usuarios.db. Sin esto,
    /eliminar/root ejecutaría userdel y pkill sobre cuentas del sistema."""
    if not any(u["usuario"] == usuario for u in leer_usuarios()):
        abort(404)

# ---------- lógica de usuarios (misma convención que panel.sh / usuarios.db) ----------

def etiqueta_de(usuario):
    """Si el usuario fue creado en modo HWID, el GECOS trae 'hwid,<cliente>'."""
    try:
        r = subprocess.run(["getent", "passwd", usuario], capture_output=True, text=True, timeout=5)
        gecos = r.stdout.strip().split(":")[4] if r.returncode == 0 else ""
    except Exception:
        gecos = ""
    if gecos.startswith("hwid,"):
        return gecos.split(",", 1)[1], True
    return usuario, False


def nombre_mostrar(usuario):
    etiqueta, es_hwid = etiqueta_de(usuario)
    return f"{etiqueta} (HWID)" if es_hwid else etiqueta


def en_linea(usuario):
    try:
        r = subprocess.run(["ps", "-u", usuario, "-o", "comm="], capture_output=True, text=True, timeout=5)
    except Exception:
        return 0
    return sum(1 for l in r.stdout.splitlines() if l.strip() == "sshd")


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
    if etiqueta is None and not re.match(r"^[a-z_][a-z0-9_-]*$", usuario):
        return False, "Nombre de usuario inválido (minúsculas, sin espacios)."
    if not _password_valido(password):
        return False, "Contraseña inválida (vacía, muy larga o con caracteres no permitidos)."
    with _db_lock():
        r = subprocess.run(["id", usuario], capture_output=True)
        if r.returncode == 0:
            return False, "Ese usuario ya existe."
        exp = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
        cmd = ["useradd", "-M", "-s", "/bin/false", "-e", exp]
        if etiqueta is not None:
            cmd += ["--badname", "-c", f"hwid,{_limpiar_etiqueta(etiqueta)}"]
        cmd.append(usuario)
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            subprocess.run(["chpasswd"], input=f"{usuario}:{password}\n".encode(), check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            return False, f"Falló al crear el usuario: {e.stderr.decode(errors='ignore')[:200]}"
        with open(DB_PATH, "a") as f:
            f.write(f"{usuario}:{limite}:{exp}\n")
    return True, exp


def eliminar_usuario(usuario):
    subprocess.run(["pkill", "-9", "-u", usuario], capture_output=True)
    subprocess.run(["userdel", usuario], capture_output=True)
    with _db_lock():
        if Path(DB_PATH).exists():
            lineas = [l for l in Path(DB_PATH).read_text().splitlines()
                      if l.split(":")[0] != usuario]
            _escribir_db_atomico(lineas)


def cambiar_password(usuario, password):
    if not _password_valido(password):
        return False
    subprocess.run(["chpasswd"], input=f"{usuario}:{password}\n".encode(), capture_output=True)
    return True


def _reescribir_db(usuario, campo, valor):
    with _db_lock():
        if not Path(DB_PATH).exists():
            return
        out = []
        for l in Path(DB_PATH).read_text().splitlines():
            if not l:
                continue
            p = l.split(":")
            if len(p) == 3 and p[0] == usuario:
                p[campo] = valor
            out.append(":".join(p))
        _escribir_db_atomico(out)


def cambiar_limite(usuario, nuevo_limite):
    _reescribir_db(usuario, 1, str(nuevo_limite))


def cambiar_dias(usuario, dias):
    nexp = (datetime.now() + timedelta(days=dias)).strftime("%Y-%m-%d")
    subprocess.run(["usermod", "-e", nexp, usuario], capture_output=True)
    _reescribir_db(usuario, 2, nexp)
    return nexp


def renovar_usuario(usuario, dias_extra):
    """Suma días al vencimiento. Si ya venció, cuenta desde hoy; si no, desde la
    fecha de vencimiento actual (para no 'perder' los días que le quedaban)."""
    base = datetime.now()
    actual = next((u for u in leer_usuarios() if u["usuario"] == usuario), None)
    if actual:
        try:
            exp_actual = datetime.strptime(actual["exp"], "%Y-%m-%d")
            if exp_actual > base:
                base = exp_actual
        except ValueError:
            pass
    nexp = (base + timedelta(days=dias_extra)).strftime("%Y-%m-%d")
    subprocess.run(["usermod", "-e", nexp, usuario], capture_output=True)
    _reescribir_db(usuario, 2, nexp)
    return nexp


def esta_bloqueado(usuario):
    try:
        r = subprocess.run(["passwd", "-S", usuario], capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    partes = r.stdout.split()
    return r.returncode == 0 and len(partes) > 1 and partes[1] == "L"


def bloquear_usuario(usuario):
    subprocess.run(["usermod", "-L", usuario], capture_output=True)
    subprocess.run(["pkill", "-9", "-u", usuario], capture_output=True)


def desbloquear_usuario(usuario):
    subprocess.run(["usermod", "-U", usuario], capture_output=True)


def cambiar_hwid(usuario_actual, nuevo_hwid):
    """Renombra un usuario HWID (login Linux + contraseña) conservando etiqueta,
    límite y vencimiento. Para cuando la app del cliente regenera su HWID."""
    nuevo_hwid = re.sub(r"[^A-Za-z0-9]", "", nuevo_hwid)
    if not (8 <= len(nuevo_hwid) <= 32):
        return False, "HWID inválido (8 a 32 caracteres alfanuméricos)."
    _, es_hwid = etiqueta_de(usuario_actual)
    if not es_hwid:
        return False, "Ese usuario no es de modo HWID."
    r = subprocess.run(["id", nuevo_hwid], capture_output=True)
    if r.returncode == 0:
        return False, "Ya existe un usuario con ese HWID."
    subprocess.run(["pkill", "-9", "-u", usuario_actual], capture_output=True)
    try:
        subprocess.run(["usermod", "--badname", "-l", nuevo_hwid, usuario_actual], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return False, f"Falló el cambio: {e.stderr.decode(errors='ignore')[:200]}"
    subprocess.run(["chpasswd"], input=f"{nuevo_hwid}:{nuevo_hwid}\n".encode(), capture_output=True)
    with _db_lock():
        if Path(DB_PATH).exists():
            out = []
            for l in Path(DB_PATH).read_text().splitlines():
                if not l:
                    continue
                p = l.split(":")
                if len(p) == 3 and p[0] == usuario_actual:
                    p[0] = nuevo_hwid
                out.append(":".join(p))
            _escribir_db_atomico(out)
    return True, nuevo_hwid


def stats():
    mt = mf = 0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            mt = int(line.split()[1]) // 1024
        elif line.startswith("MemAvailable:"):
            mf = int(line.split()[1]) // 1024
    mu = max(0, mt - mf)
    try:
        la = float(Path("/proc/loadavg").read_text().split()[0])
        cores = max(1, Path("/proc/cpuinfo").read_text().count("processor\t:"))
        cpu = min(100, round(la / cores * 100))
    except Exception:
        cpu = 0
    usuarios = leer_usuarios()
    online = sum(1 for u in usuarios if en_linea(u["usuario"]) > 0)
    return {"mt": mt, "mu": mu, "cpu": cpu, "cuentas": len(usuarios), "online": online}


def usuarios_para_api():
    out = []
    for u in leer_usuarios():
        _, es_hwid = etiqueta_de(u["usuario"])
        out.append({
            "usuario": u["usuario"],
            "nombre": nombre_mostrar(u["usuario"]),
            "limite": u["limite"],
            "exp": u["exp"],
            "dias": dias_restantes(u["exp"]),
            "online": en_linea(u["usuario"]),
            "bloqueado": esta_bloqueado(u["usuario"]),
            "es_hwid": es_hwid,
        })
    return out


# ---------- auth ----------

# Rate-limit de login por IP: tras varios intentos fallidos dentro de la
# ventana, se rechaza con una espera creciente. En memoria (se reinicia con el
# servicio), suficiente para frenar fuerza bruta en un panel chico.
_LOGIN_FALLOS = {}
_LOGIN_MAX = 5
_LOGIN_VENTANA = 300  # segundos


def _login_ip():
    return request.remote_addr or "?"


def _login_bloqueado(ip):
    rec = _LOGIN_FALLOS.get(ip)
    if not rec:
        return False
    fallos, primero = rec
    if time.time() - primero > _LOGIN_VENTANA:
        _LOGIN_FALLOS.pop(ip, None)
        return False
    return fallos >= _LOGIN_MAX


def _login_fallo(ip):
    fallos, primero = _LOGIN_FALLOS.get(ip, (0, time.time()))
    _LOGIN_FALLOS[ip] = (fallos + 1, primero)


def _login_ok(ip):
    _LOGIN_FALLOS.pop(ip, None)


def login_requerido(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("ok"):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrap


BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root{
  --bg:#0a0912; --surface:#141220; --surface-2:#1b1830; --line:#2a2540;
  --violet:#8b5cf6; --violet-dim:#5b4b8a;
  --good:#22c55e; --good-bg:#132a1d;
  --warn:#f5a524; --warn-bg:#2e2410;
  --bad:#f0506e; --bad-bg:#2e1420;
  --idle:#6e6886;
  --text:#eeecf7; --text-dim:#9791ab;
}
*{box-sizing:border-box}
html,body{overflow-x:hidden}
body{
  background:
    radial-gradient(1200px 500px at 50% -10%, #1c1733 0%, transparent 60%),
    var(--bg);
  color:var(--text); font-family:'Space Grotesk',system-ui,sans-serif;
  margin:0; padding:0 0 32px; min-height:100vh;
}
.wrap{max-width:720px;margin:0 auto;padding:0 16px}
.mono{font-family:'JetBrains Mono',ui-monospace,Menlo,monospace}

.topbar{display:flex;align-items:center;justify-content:space-between;padding:20px 16px 18px;max-width:720px;margin:0 auto}
.brand{display:flex;align-items:center;gap:10px}
.brand .dot{width:10px;height:10px;border-radius:3px;background:var(--violet);box-shadow:0 0 14px #8b5cf699}
.brand span{font-weight:700;font-size:1.15rem;letter-spacing:.01em}
.exit{color:var(--text-dim);text-decoration:none;font-size:.85rem;border:1px solid var(--line);padding:7px 12px;border-radius:8px;transition:border-color .15s}
.exit:hover{border-color:var(--violet-dim);color:var(--text)}

.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 20px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat .label{color:var(--text-dim);font-size:.72rem;letter-spacing:.02em}
.stat .value{font-family:'JetBrains Mono';font-size:1.3rem;font-weight:600;margin-top:4px}
.stat.accent .value{color:var(--violet)}
.stat.live .value{color:var(--good)}

.section{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:16px}
.section h2{font-size:.95rem;font-weight:500;margin:0 0 14px;color:var(--text)}

.seg{display:flex;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:3px;margin-bottom:14px}
.seg label{flex:1;text-align:center;padding:8px 0;border-radius:7px;font-size:.85rem;color:var(--text-dim);cursor:pointer;transition:background .15s,color .15s}
.seg input{display:none}
.seg label.active{background:var(--surface-2);color:var(--text)}

.field{display:flex;flex-direction:column;gap:5px;margin-bottom:12px}
.field label{font-size:.75rem;color:var(--text-dim)}
.field-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
input{
  font-family:inherit;background:var(--bg);border:1px solid var(--line);color:var(--text);
  padding:10px 12px;border-radius:8px;font-size:.9rem;width:100%
}
input:focus{outline:none;border-color:var(--violet-dim)}
.btn{
  cursor:pointer;background:var(--violet);color:#fff;font-weight:600;border:none;
  padding:11px 16px;border-radius:9px;font-size:.9rem;width:100%;font-family:inherit
}
.btn:active{background:#7c4deb}
.btn-danger{background:transparent;border:1px solid #4a2230;color:var(--bad);font-weight:500;padding:6px 12px;font-size:.8rem;width:auto;border-radius:7px}
.btn-danger:active{background:var(--bad-bg)}
.btn-edit{background:transparent;border:1px solid var(--line);color:var(--text-dim);font-weight:500;padding:6px 12px;font-size:.8rem;width:auto;border-radius:7px;flex-shrink:0}
.btn-edit:active{border-color:var(--violet-dim);color:var(--text)}
.btn-mini{background:var(--violet-dim);color:#fff;font-weight:600;border:none;padding:8px 12px;border-radius:7px;font-size:.8rem;width:auto;font-family:inherit}
.btn-mini:active{background:var(--violet)}
.btn-warn{background:transparent;border:1px solid #4a3a16;color:var(--warn)}
.btn-warn:active{background:var(--warn-bg)}

.msg{padding:10px 14px;border-radius:8px;margin-bottom:14px;font-size:.85rem}
.msg-err{background:var(--bad-bg);color:var(--bad)}
.msg-info{background:var(--surface-2);color:var(--text-dim);border:1px solid var(--line)}
.msg-ok{background:var(--good-bg);color:var(--good)}

.hide{display:none !important}

.userlist{display:flex;flex-direction:column}
.urow-wrap{border-bottom:1px solid var(--line)}
.urow-wrap:last-child{border-bottom:none}
.urow{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px 0}
.uinfo{min-width:0;flex:1;cursor:pointer}
.uname{font-family:'JetBrains Mono';font-size:.92rem;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.utag{color:var(--text-dim);font-weight:400;font-size:.78rem}
.umeta{display:flex;align-items:center;gap:8px;margin-top:4px;font-size:.78rem;color:var(--text-dim)}
.status{display:flex;align-items:center;gap:5px}
.status .bulb{width:7px;height:7px;border-radius:50%;background:var(--idle);flex-shrink:0}
.status.on .bulb{background:var(--good);animation:pulse 1.8s ease-in-out infinite}
.status.on{color:var(--good)}
.exp{padding:1px 7px;border-radius:5px;background:var(--surface-2)}
.exp.warn{background:var(--warn-bg);color:var(--warn)}
.exp.bad{background:var(--bad-bg);color:var(--bad)}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 #22c55e66}50%{box-shadow:0 0 0 4px #22c55e00}}

.erow{display:flex;gap:8px;margin-bottom:10px}
.erow form{display:flex;gap:8px;flex:1}
.erow input{flex:1}
.erow-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:4px}
.erow-msg{background:var(--bad-bg);color:var(--bad);padding:8px 10px;border-radius:7px;font-size:.78rem;margin-bottom:10px}

.empty{color:var(--text-dim);font-size:.85rem;text-align:center;padding:20px 0}

.modal-overlay{position:fixed;inset:0;background:rgba(5,4,10,.7);display:flex;align-items:flex-start;justify-content:center;z-index:50;padding:16px;overflow-y:auto}
.modal-overlay.hide{display:none}
.modal{background:var(--surface);border:1px solid var(--line);border-radius:16px;width:100%;max-width:480px;padding:20px;max-height:none;margin-top:6vh;margin-bottom:16px}
@media (min-width:560px){.modal-overlay{align-items:center}.modal{margin-top:0}}
.modal-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:16px}
.modal-title{font-weight:600;font-size:1.05rem;line-height:1.3;word-break:break-word}
.modal-sub{margin-top:5px}
.modal-close{background:transparent;border:1px solid var(--line);color:var(--text-dim);width:34px;height:34px;min-width:34px;border-radius:9px;padding:0;font-size:1rem;flex-shrink:0}
.modal-close:active{border-color:var(--violet-dim);color:var(--text)}
.modal-label{font-size:.75rem;color:var(--text-dim);margin:16px 0 6px}
.modal-label:first-of-type{margin-top:0}

.toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,0);background:var(--good-bg);color:var(--good);border:1px solid var(--good);padding:12px 20px;border-radius:10px;font-size:.88rem;font-weight:600;z-index:60;box-shadow:0 8px 24px rgba(0,0,0,.35);transition:opacity .25s,transform .25s}
.toast.hide{opacity:0;transform:translate(-50%,8px);pointer-events:none}

.login-shell{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px}
.login-card{width:100%;max-width:340px;background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:28px 24px}
.login-card .brand{justify-content:center;margin-bottom:22px}
.login-card .btn{margin-top:6px}
"""

LOGIN_HTML = """
<!doctype html><html lang="es"><head><meta name="viewport" content="width=device-width, initial-scale=1">
<meta charset="utf-8"><title>Panel Zumo</title><style>{{css}}</style></head>
<body>
<div class="login-shell">
<div class="login-card">
<div class="brand"><span class="dot"></span><span>Panel Zumo</span></div>
{% if error %}<div class="msg msg-err">{{error}}</div>{% endif %}
<form method="post">
<div class="field"><label>Usuario</label><input name="usuario" autofocus required autocomplete="username"></div>
<div class="field"><label>Contraseña</label><input name="password" type="password" required autocomplete="current-password"></div>
<button class="btn" type="submit">Entrar</button>
</form>
</div>
</div>
</body></html>
"""

BASE_HTML = """
<!doctype html><html lang="es"><head><meta name="viewport" content="width=device-width, initial-scale=1">
<meta charset="utf-8"><title>Panel Zumo</title><style>{{css}}</style></head>
<body>
<div class="topbar">
<div class="brand"><span class="dot"></span><span>Panel Zumo</span></div>
<a class="exit" href="{{ url_for('logout') }}">Salir</a>
</div>

<div class="wrap">

<div class="stat-grid">
<div class="stat"><div class="label">Memoria</div><div class="value mono" id="s-ram">—</div></div>
<div class="stat"><div class="label">Procesador</div><div class="value mono" id="s-cpu">—</div></div>
<div class="stat accent"><div class="label">Cuentas</div><div class="value mono" id="s-cuentas">—</div></div>
<div class="stat live"><div class="label">Conectados ahora</div><div class="value mono" id="s-online">—</div></div>
</div>

<div class="section">
<h2>Crear usuario</h2>
{% if error == 'hwid' %}<div class="msg msg-err">Ese HWID no es válido: tiene que tener de 8 a 32 caracteres alfanuméricos.</div>{% endif %}
{% if error == 'existe' %}<div class="msg msg-err">Ya existe un usuario con ese nombre o HWID.</div>{% endif %}
<div class="seg">
<label class="active" id="lbl-normal"><input type="radio" name="modo_sel" value="normal" checked onclick="toggleModo('normal')"><span>Normal</span></label>
<label id="lbl-hwid"><input type="radio" name="modo_sel" value="hwid" onclick="toggleModo('hwid')"><span>HWID</span></label>
</div>
<form method="post" action="{{ url_for('crear') }}" id="form-crear">
<input type="hidden" name="modo" id="modo-input" value="normal">

<div id="campos-normal">
<div class="field"><label>Usuario</label><input name="usuario"></div>
<div class="field"><label>Contraseña</label><input name="password"></div>
</div>

<div id="campos-hwid" class="hide">
<div class="field"><label>Nombre del cliente</label><input name="etiqueta" placeholder="Para identificarlo en el panel"></div>
<div class="field"><label>HWID</label><input name="hwid" placeholder="8 a 32 caracteres"></div>
</div>

<div class="field-row">
<div class="field"><label>Días</label><input name="dias" type="number" min="1" required></div>
<div class="field" id="campo-limite"><label>Límite de conexiones</label><input name="limite" type="number" min="1" value="1"></div>
</div>
<div class="msg msg-info hide" id="aviso-limite-hwid">Los usuarios HWID se crean con límite de 1 conexión.</div>
<button class="btn" type="submit">Crear usuario</button>
</form>
</div>

<div class="section">
<h2>Usuarios</h2>
<div class="userlist" id="lista-usuarios">
<div class="empty">Cargando…</div>
</div>
</div>

</div>

<div class="toast hide" id="toast"></div>

<div class="modal-overlay hide" id="modal-overlay">
<div class="modal">
<div class="modal-head">
<div>
<div class="modal-title" id="modal-title"></div>
<div class="modal-sub umeta" id="modal-sub"></div>
</div>
<button type="button" class="modal-close" id="modal-close">✕</button>
</div>
<div id="modal-body"></div>
</div>
</div>

<script>
function toggleModo(modo){
  var hwid = modo === 'hwid';
  document.getElementById('modo-input').value = hwid ? 'hwid' : 'normal';
  document.getElementById('campos-hwid').classList.toggle('hide', !hwid);
  document.getElementById('campos-normal').classList.toggle('hide', hwid);
  document.getElementById('lbl-hwid').classList.toggle('active', hwid);
  document.getElementById('lbl-normal').classList.toggle('active', !hwid);
  // Los HWID siempre van con límite 1: no se pregunta, se fija solo.
  document.getElementById('campo-limite').classList.toggle('hide', hwid);
  document.getElementById('aviso-limite-hwid').classList.toggle('hide', !hwid);
  document.querySelector('#campo-limite input').value = hwid ? '1' : document.querySelector('#campo-limite input').value;
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

var MENSAJES_ERROR = {
  hwid: 'HWID inválido, o ya usado por otro cliente.',
  existe: 'Ya existe un usuario con ese nombre o HWID.'
};

var usuariosActuales = [];  // última lista traída de /api/usuarios
var modalUsuario = null;    // usuario que está abierto en el modal, o null
var errores = {};

// ---------- fila de la lista (solo resumen; tocarla abre el modal) ----------

function fila(u){
  var nombre = escapeHtml(u.nombre);
  var expClass = u.dias === 'vencido' ? 'bad' : (u.dias === 'vence hoy' ? 'warn' : '');
  var estado = u.bloqueado
    ? '<span class="status"><span class="bulb"></span>bloqueado</span>'
    : (u.online > 0
      ? '<span class="status on"><span class="bulb"></span>online · '+u.online+'</span>'
      : '<span class="status"><span class="bulb"></span>offline</span>');
  return '' +
  '<div class="urow-wrap" data-usuario="'+u.usuario+'">' +
    '<div class="urow" data-action="abrir">' +
      '<div class="uinfo">' +
        '<div class="uname">'+nombre+'</div>' +
        '<div class="umeta">' + estado +
          '<span>límite '+u.limite+'</span>' +
          '<span class="exp '+expClass+'">'+u.dias+'</span>' +
        '</div>' +
      '</div>' +
      '<button type="button" class="btn-edit">Editar</button>' +
    '</div>' +
  '</div>';
}

function bindRows(){
  document.querySelectorAll('[data-action="abrir"]').forEach(function(el){
    el.onclick = function(){
      abrirModal(el.closest('.urow-wrap').dataset.usuario);
    };
  });
}

// ---------- modal de edición ----------

function cuerpoModal(u){
  var usr = encodeURIComponent(u.usuario);
  var error = errores[u.usuario] || '';
  return '' +
    '<div class="erow-msg'+(error ? '' : ' hide')+'">'+error+'</div>' +
    '<div class="modal-label">Renovar</div>' +
    '<div class="erow">' +
      '<form method="post" action="/renovar/'+usr+'" class="ajax-form">' +
        '<input type="number" name="dias" min="1" placeholder="Días a sumar" required>' +
        '<button class="btn-mini" type="submit">Renovar</button>' +
      '</form>' +
    '</div>' +
    '<div class="modal-label">Límite de conexiones</div>' +
    '<div class="erow">' +
      '<form method="post" action="/limite/'+usr+'" class="ajax-form">' +
        '<input type="number" name="limite" min="1" value="'+u.limite+'">' +
        '<button class="btn-mini" type="submit">Guardar límite</button>' +
      '</form>' +
    '</div>' +
    (u.es_hwid ?
      '<div class="modal-label">HWID</div>' +
      '<div class="erow">' +
        '<form method="post" action="/hwid/'+usr+'" class="ajax-form f-hwid">' +
          '<input type="text" name="nuevo_hwid" placeholder="HWID nuevo (app regenerada)" maxlength="32">' +
          '<button class="btn-mini" type="submit">Cambiar HWID</button>' +
        '</form>' +
      '</div>' : '') +
    '<div class="erow-actions">' +
      '<form method="post" action="/bloquear/'+usr+'" class="ajax-form">' +
        '<button class="btn-mini'+(u.bloqueado ? '' : ' btn-warn')+'" type="submit">'+(u.bloqueado ? 'Desbloquear' : 'Bloquear')+'</button>' +
      '</form>' +
      '<form method="post" action="/eliminar/'+usr+'" class="ajax-form" data-action="confirmar-borrado">' +
        '<button class="btn-danger" type="submit">Borrar</button>' +
      '</form>' +
    '</div>';
}

var modalClaveRenderizada = null;

function renderModal(){
  var u = usuariosActuales.find(function(x){ return x.usuario === modalUsuario; });
  if (!u){ cerrarModal(); return; }
  var estado = u.bloqueado ? 'bloqueado' : (u.online > 0 ? 'online · '+u.online : 'offline');
  document.getElementById('modal-title').textContent = u.nombre;
  document.getElementById('modal-sub').textContent = estado + ' · límite ' + u.limite + ' · ' + u.dias;

  // Solo reconstruimos el cuerpo (los formularios) cuando algo relevante
  // cambió de verdad. Si no, el refresco automático de cada 3s borraría
  // lo que el usuario esté escribiendo en ese momento (p. ej. el HWID nuevo).
  var clave = modalUsuario+'|'+u.limite+'|'+u.bloqueado+'|'+u.es_hwid+'|'+(errores[u.usuario]||'');
  if (clave !== modalClaveRenderizada){
    document.getElementById('modal-body').innerHTML = cuerpoModal(u);
    bindModalForms();
    modalClaveRenderizada = clave;
  }
}

function abrirModal(usuario){
  modalUsuario = usuario;
  modalClaveRenderizada = null;
  renderModal();
  document.getElementById('modal-overlay').classList.remove('hide');
}

function cerrarModal(){
  modalUsuario = null;
  modalClaveRenderizada = null;
  document.getElementById('modal-overlay').classList.add('hide');
}

function bindModalForms(){
  document.querySelectorAll('.ajax-form').forEach(function(f){
    f.onsubmit = function(e){
      e.preventDefault();
      var usuario = modalUsuario;
      var u = usuariosActuales.find(function(x){ return x.usuario === usuario; });

      if (f.dataset.action === 'confirmar-borrado' && !confirm('¿Borrar a ' + (u ? u.nombre : usuario) + '?')) return;

      // Si es "cambiar HWID", el modal tiene que seguir al usuario con su nombre nuevo.
      var nuevoHwidInput = f.querySelector('input[name="nuevo_hwid"]');
      var nuevoHwid = nuevoHwidInput ? nuevoHwidInput.value.trim() : null;

      var btn = f.querySelector('button');
      var textoOriginal = btn.textContent;
      btn.disabled = true; btn.textContent = '…';

      fetch(f.action, { method: 'POST', body: new FormData(f) }).then(function(r){
        var url = new URL(r.url);
        var error = url.searchParams.get('error');
        if (error){
          errores[usuario] = MENSAJES_ERROR[error] || 'No se pudo completar la acción.';
        } else {
          delete errores[usuario];
          if (nuevoHwid){
            // Cambio de HWID exitoso: avisamos y volvemos al panel principal
            // en vez de dejar el modal abierto bajo el nombre nuevo.
            delete errores[nuevoHwid];
            cerrarModal();
            mostrarToast('HWID cambiado con éxito');
          } else if (f.dataset.action === 'confirmar-borrado'){
            cerrarModal();
          }
        }
      }).catch(function(){
        errores[usuario] = 'No se pudo conectar con el panel.';
      }).finally(function(){
        actualizar();
      });
    };
  });
}

var toastTimeout = null;
function mostrarToast(msg){
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.remove('hide');
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(function(){ t.classList.add('hide'); }, 3000);
}

document.getElementById('modal-close').onclick = cerrarModal;
document.getElementById('modal-overlay').onclick = function(e){
  if (e.target.id === 'modal-overlay') cerrarModal();
};

// ---------- refresco periódico ----------

function actualizar(){
  fetch('/api/usuarios').then(r => r.json()).then(d => {
    document.getElementById('s-ram').textContent = d.stats.mu + ' / ' + d.stats.mt + ' MB';
    document.getElementById('s-cpu').textContent = d.stats.cpu + '%';
    document.getElementById('s-cuentas').textContent = d.stats.cuentas;
    document.getElementById('s-online').textContent = d.stats.online;
    usuariosActuales = d.usuarios;

    var el = document.getElementById('lista-usuarios');
    if (d.usuarios.length === 0){
      el.innerHTML = '<div class="empty">Todavía no hay usuarios — creá el primero arriba.</div>';
    } else {
      el.innerHTML = d.usuarios.map(fila).join('');
      bindRows();
    }

    if (modalUsuario) renderModal();
  }).catch(e => { console.error('Panel Zumo: fallo al actualizar', e); });
}
actualizar();
setInterval(actualizar, 3000);
</script>
</body></html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = _login_ip()
        if _login_bloqueado(ip):
            time.sleep(2)
            error = "Demasiados intentos. Esperá unos minutos e intentá de nuevo."
            return render_template_string(LOGIN_HTML, error=error, css=BASE_CSS)
        u = request.form.get("usuario", "")
        p = request.form.get("password", "")
        if u and p and u == CONF.get("WEB_USER") and check_password_hash(CONF.get("WEB_PASS_HASH", ""), p):
            _login_ok(ip)
            session["ok"] = True
            return redirect(url_for("index"))
        _login_fallo(ip)
        time.sleep(1)  # penalización fija contra fuerza bruta
        error = "Usuario o contraseña incorrectos"
    return render_template_string(LOGIN_HTML, error=error, css=BASE_CSS)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_requerido
def index():
    error = request.args.get("error")
    return render_template_string(BASE_HTML, css=BASE_CSS, error=error)


@app.route("/api/usuarios")
@login_requerido
def api_usuarios():
    return jsonify({"usuarios": usuarios_para_api(), "stats": stats()})


@app.route("/crear", methods=["POST"])
@login_requerido
def crear():
    modo = request.form.get("modo", "normal")
    try:
        dias = int(request.form.get("dias", "0") or 0)
    except ValueError:
        dias = 0
    try:
        limite = int(request.form.get("limite", "1") or 1)
    except ValueError:
        limite = 1
    if dias < 1 or dias > 3650:
        return redirect(url_for("index"))
    if limite < 1:
        limite = 1

    if modo == "hwid":
        etiqueta = (request.form.get("etiqueta") or "cliente").strip()
        hwid = re.sub(r"[^A-Za-z0-9]", "", request.form.get("hwid", ""))
        if not (8 <= len(hwid) <= 32):
            return redirect(url_for("index", error="hwid"))
        ok, _ = crear_usuario(hwid, hwid, dias, 1, etiqueta=etiqueta)
    else:
        usuario = (request.form.get("usuario") or "").strip()
        password = request.form.get("password") or ""
        if not usuario or not password:
            return redirect(url_for("index"))
        ok, _ = crear_usuario(usuario, password, dias, limite)
    if not ok:
        return redirect(url_for("index", error="existe"))
    return redirect(url_for("index"))


@app.route("/eliminar/<usuario>", methods=["POST"])
@login_requerido
def eliminar(usuario):
    _usuario_del_panel(usuario)
    eliminar_usuario(usuario)
    return redirect(url_for("index"))


@app.route("/renovar/<usuario>", methods=["POST"])
@login_requerido
def renovar(usuario):
    _usuario_del_panel(usuario)
    try:
        dias = int(request.form.get("dias", "0") or 0)
    except ValueError:
        dias = 0
    if 0 < dias <= 3650:
        renovar_usuario(usuario, dias)
    return redirect(url_for("index"))


@app.route("/limite/<usuario>", methods=["POST"])
@login_requerido
def limite(usuario):
    _usuario_del_panel(usuario)
    try:
        nl = int(request.form.get("limite", "0") or 0)
    except ValueError:
        nl = 0
    if nl > 0:
        cambiar_limite(usuario, nl)
    return redirect(url_for("index"))


@app.route("/bloquear/<usuario>", methods=["POST"])
@login_requerido
def bloquear(usuario):
    _usuario_del_panel(usuario)
    if esta_bloqueado(usuario):
        desbloquear_usuario(usuario)
    else:
        bloquear_usuario(usuario)
    return redirect(url_for("index"))


@app.route("/hwid/<usuario>", methods=["POST"])
@login_requerido
def hwid_cambiar(usuario):
    _usuario_del_panel(usuario)
    nuevo = request.form.get("nuevo_hwid", "")
    ok, _ = cambiar_hwid(usuario, nuevo)
    if not ok:
        return redirect(url_for("index", error="hwid"))
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(CONF.get("PORT", "9090"))
    # BIND=127.0.0.1 en web.conf deja el panel solo para acceso por túnel SSH.
    app.run(host=CONF.get("BIND", "0.0.0.0"), port=port)
