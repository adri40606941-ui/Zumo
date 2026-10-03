#!/usr/bin/env python3
"""Panel web ZUMO: crear/borrar/editar usuarios y ver quién está conectado,
desde el navegador. Usa la misma base /etc/zumo/usuarios.db que panel.sh."""
import re
import secrets
import subprocess
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, request, redirect, url_for, session, render_template_string, jsonify
from werkzeug.security import check_password_hash

CONF_PATH = "/etc/zumo/web.conf"
DB_PATH = "/etc/zumo/usuarios.db"


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
        subprocess.run(["chpasswd"], input=f"{usuario}:{password}\n".encode(), check=True, capture_output=True)
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


def cambiar_password(usuario, password):
    subprocess.run(["chpasswd"], input=f"{usuario}:{password}\n".encode(), capture_output=True)


def _reescribir_db(usuario, campo, valor):
    if not Path(DB_PATH).exists():
        return
    out = []
    for l in Path(DB_PATH).read_text().splitlines():
        p = l.split(":")
        if len(p) == 3 and p[0] == usuario:
            p[campo] = valor
        out.append(":".join(p))
    Path(DB_PATH).write_text("\n".join(out) + ("\n" if out else ""))


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
    if Path(DB_PATH).exists():
        out = []
        for l in Path(DB_PATH).read_text().splitlines():
            p = l.split(":")
            if len(p) == 3 and p[0] == usuario_actual:
                p[0] = nuevo_hwid
            out.append(":".join(p))
        Path(DB_PATH).write_text("\n".join(out) + ("\n" if out else ""))
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

.edit-panel{display:flex;flex-direction:column;gap:8px;padding:0 0 16px;margin-top:-2px}
.erow{display:flex;gap:8px}
.erow form{display:flex;gap:8px;flex:1}
.erow input{flex:1}
.erow-actions{display:flex;gap:8px;justify-content:flex-end}

.empty{color:var(--text-dim);font-size:.85rem;text-align:center;padding:20px 0}

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
<div class="field"><label>Límite de conexiones</label><input name="limite" type="number" min="1" value="1"></div>
</div>
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

<script>
function toggleModo(modo){
  var hwid = modo === 'hwid';
  document.getElementById('modo-input').value = hwid ? 'hwid' : 'normal';
  document.getElementById('campos-hwid').classList.toggle('hide', !hwid);
  document.getElementById('campos-normal').classList.toggle('hide', hwid);
  document.getElementById('lbl-hwid').classList.toggle('active', hwid);
  document.getElementById('lbl-normal').classList.toggle('active', !hwid);
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

var abiertos = {};

function toggleEdit(usuario){
  abiertos[usuario] = !abiertos[usuario];
  var el = document.getElementById('edit-' + usuario);
  if (el) el.classList.toggle('hide', !abiertos[usuario]);
}

function fila(u){
  // u.usuario solo trae letras/números/_/- (validado en el servidor), así que es
  // seguro meterlo directo en atributos HTML y en URLs sin escapar comillas a mano.
  var nombre = escapeHtml(u.nombre);
  var usr = encodeURIComponent(u.usuario);
  var expClass = u.dias === 'vencido' ? 'bad' : (u.dias === 'vence hoy' ? 'warn' : '');
  var estado = u.bloqueado
    ? '<span class="status"><span class="bulb"></span>bloqueado</span>'
    : (u.online > 0
      ? '<span class="status on"><span class="bulb"></span>online · '+u.online+'</span>'
      : '<span class="status"><span class="bulb"></span>offline</span>');
  var editId = 'edit-' + u.usuario;
  var abierto = !!abiertos[u.usuario];
  return '' +
  '<div class="urow-wrap" data-usuario="'+u.usuario+'" data-nombre="'+nombre+'">' +
    '<div class="urow">' +
      '<div class="uinfo" data-action="toggle">' +
        '<div class="uname">'+nombre+'</div>' +
        '<div class="umeta">' + estado +
          '<span>límite '+u.limite+'</span>' +
          '<span class="exp '+expClass+'">'+u.dias+'</span>' +
        '</div>' +
      '</div>' +
      '<button type="button" class="btn-edit" data-action="toggle">Editar</button>' +
    '</div>' +
    '<div class="edit-panel'+(abierto ? '' : ' hide')+'" id="'+editId+'">' +
      '<div class="erow">' +
        '<form method="post" action="/renovar/'+usr+'">' +
          '<input type="number" name="dias" min="1" placeholder="Días a sumar" required>' +
          '<button class="btn-mini" type="submit">Renovar</button>' +
        '</form>' +
      '</div>' +
      '<div class="erow">' +
        '<form method="post" action="/limite/'+usr+'">' +
          '<input type="number" name="limite" min="1" value="'+u.limite+'">' +
          '<button class="btn-mini" type="submit">Guardar límite</button>' +
        '</form>' +
      '</div>' +
      (u.es_hwid ?
        '<div class="erow">' +
          '<form method="post" action="/hwid/'+usr+'">' +
            '<input type="text" name="nuevo_hwid" placeholder="HWID nuevo (app regenerada)" maxlength="32">' +
            '<button class="btn-mini" type="submit">Cambiar HWID</button>' +
          '</form>' +
        '</div>' : '') +
      '<div class="erow-actions">' +
        '<form method="post" action="/bloquear/'+usr+'">' +
          '<button class="btn-mini'+(u.bloqueado ? '' : ' btn-warn')+'" type="submit">'+(u.bloqueado ? 'Desbloquear' : 'Bloquear')+'</button>' +
        '</form>' +
        '<form method="post" action="/eliminar/'+usr+'" data-action="confirmar-borrado">' +
          '<button class="btn-danger" type="submit">Borrar</button>' +
        '</form>' +
      '</div>' +
    '</div>' +
  '</div>';
}

function bindRows(){
  document.querySelectorAll('[data-action="toggle"]').forEach(function(el){
    el.onclick = function(){
      toggleEdit(el.closest('.urow-wrap').dataset.usuario);
    };
  });
  document.querySelectorAll('[data-action="confirmar-borrado"]').forEach(function(f){
    f.onsubmit = function(){
      var nombre = f.closest('.urow-wrap').dataset.nombre;
      return confirm('¿Borrar a ' + nombre + '?');
    };
  });
}

function actualizar(){
  fetch('/api/usuarios').then(r => r.json()).then(d => {
    document.getElementById('s-ram').textContent = d.stats.mu + ' / ' + d.stats.mt + ' MB';
    document.getElementById('s-cpu').textContent = d.stats.cpu + '%';
    document.getElementById('s-cuentas').textContent = d.stats.cuentas;
    document.getElementById('s-online').textContent = d.stats.online;
    var el = document.getElementById('lista-usuarios');
    if (d.usuarios.length === 0){
      el.innerHTML = '<div class="empty">Todavía no hay usuarios — creá el primero arriba.</div>';
      return;
    }
    el.innerHTML = d.usuarios.map(fila).join('');
    bindRows();
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
        u = request.form.get("usuario", "")
        p = request.form.get("password", "")
        if u and p and u == CONF.get("WEB_USER") and check_password_hash(CONF.get("WEB_PASS_HASH", ""), p):
            session["ok"] = True
            return redirect(url_for("index"))
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
    if dias < 1:
        return redirect(url_for("index"))

    if modo == "hwid":
        etiqueta = (request.form.get("etiqueta") or "cliente").strip()
        hwid = re.sub(r"[^A-Za-z0-9]", "", request.form.get("hwid", ""))
        if not (8 <= len(hwid) <= 32):
            return redirect(url_for("index", error="hwid"))
        ok, _ = crear_usuario(hwid, hwid, dias, limite, etiqueta=etiqueta)
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
    eliminar_usuario(usuario)
    return redirect(url_for("index"))


@app.route("/renovar/<usuario>", methods=["POST"])
@login_requerido
def renovar(usuario):
    try:
        dias = int(request.form.get("dias", "0") or 0)
    except ValueError:
        dias = 0
    if dias > 0:
        renovar_usuario(usuario, dias)
    return redirect(url_for("index"))


@app.route("/limite/<usuario>", methods=["POST"])
@login_requerido
def limite(usuario):
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
    if esta_bloqueado(usuario):
        desbloquear_usuario(usuario)
    else:
        bloquear_usuario(usuario)
    return redirect(url_for("index"))


@app.route("/hwid/<usuario>", methods=["POST"])
@login_requerido
def hwid_cambiar(usuario):
    nuevo = request.form.get("nuevo_hwid", "")
    ok, _ = cambiar_hwid(usuario, nuevo)
    if not ok:
        return redirect(url_for("index", error="hwid"))
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(CONF.get("PORT", "9090"))
    app.run(host="0.0.0.0", port=port)
