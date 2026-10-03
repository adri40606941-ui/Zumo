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
        out.append({
            "usuario": u["usuario"],
            "nombre": nombre_mostrar(u["usuario"]),
            "limite": u["limite"],
            "exp": u["exp"],
            "dias": dias_restantes(u["exp"]),
            "online": en_linea(u["usuario"]),
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
:root{--bg:#0d0b14;--panel:#171320;--line:#3a2a55;--purple:#a78bfa;--orange:#f59e0b;--green:#34d399;--red:#f87171;--grey:#8b8699;--text:#e8e6f0}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:ui-monospace,Menlo,Consolas,monospace;margin:0;padding:16px}
h1{color:var(--purple);font-size:1.1rem;letter-spacing:.15em;text-transform:uppercase;margin:0 0 12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.stats{display:flex;flex-wrap:wrap;gap:16px;font-size:.85rem}
.stats b{color:var(--orange)}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{color:var(--orange);text-align:left;padding:6px 4px;border-bottom:1px solid var(--line)}
td{padding:6px 4px;border-bottom:1px solid #241c35}
.on{color:var(--green)} .off{color:var(--grey)}
.pill{padding:2px 8px;border-radius:999px;font-size:.75rem}
.pill.on{background:#0f3d2e;color:var(--green)} .pill.off{background:#241c35;color:var(--grey)}
input,select,button{font-family:inherit;background:#0d0b14;border:1px solid var(--line);color:var(--text);padding:8px;border-radius:6px;font-size:.85rem}
button{cursor:pointer;background:var(--purple);color:#0d0b14;font-weight:bold;border:none}
button.danger{background:var(--red)}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--text)}
form.inline{display:inline}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
a{color:var(--purple)}
.msg{padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:.85rem}
.msg.err{background:#3d1414;color:var(--red)} .msg.ok{background:#0f3d2e;color:var(--green)}
.modo{display:flex;gap:8px;margin-bottom:8px}
.modo label{display:flex;gap:4px;align-items:center;font-size:.8rem}
.hide{display:none}
"""

LOGIN_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel ZUMO</title><style>{{css}}</style></head>
<body>
<div class="card" style="max-width:360px;margin:60px auto">
<h1>═ Panel ZUMO ═</h1>
{% if error %}<div class="msg err">{{error}}</div>{% endif %}
<form method="post">
<div class="row" style="flex-direction:column;align-items:stretch">
<input name="usuario" placeholder="Usuario" autofocus required>
<input name="password" type="password" placeholder="Contraseña" required>
<button type="submit">Entrar</button>
</div>
</form>
</div>
</body></html>
"""

BASE_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<meta charset="utf-8"><title>Panel ZUMO</title><style>{{css}}</style></head>
<body>
<div class="row" style="justify-content:space-between">
<h1>═ Panel ZUMO ═</h1>
<a href="{{ url_for('logout') }}">Salir</a>
</div>

<div class="card">
<div class="stats">
<span>RAM: <b id="s-ram">-</b></span>
<span>CPU: <b id="s-cpu">-</b></span>
<span>Cuentas: <b id="s-cuentas">-</b></span>
<span>En línea: <b id="s-online">-</b></span>
</div>
</div>

<div class="card">
<h3 style="margin-top:0">Crear usuario</h3>
{% if error == 'hwid' %}<div class="msg err">HWID inválido (8 a 32 caracteres alfanuméricos)</div>{% endif %}
{% if error == 'existe' %}<div class="msg err">Ese usuario ya existe</div>{% endif %}
<div class="modo">
<label><input type="radio" name="modo_sel" value="normal" checked onclick="toggleModo()"> Normal</label>
<label><input type="radio" name="modo_sel" value="hwid" onclick="toggleModo()"> HWID</label>
</div>
<form method="post" action="{{ url_for('crear') }}" id="form-crear">
<input type="hidden" name="modo" id="modo-input" value="normal">
<div class="row" id="campos-normal">
<input name="usuario" placeholder="Usuario">
<input name="password" placeholder="Contraseña">
</div>
<div class="row hide" id="campos-hwid">
<input name="etiqueta" placeholder="Nombre del cliente">
<input name="hwid" placeholder="HWID (8 a 32 caracteres)">
</div>
<div class="row">
<input name="dias" type="number" placeholder="Días" min="1" required style="width:90px">
<input name="limite" type="number" placeholder="Límite" min="1" value="1" style="width:90px">
<button type="submit">Crear</button>
</div>
</form>
</div>

<div class="card">
<h3 style="margin-top:0">Usuarios (en vivo)</h3>
<table id="tabla-usuarios">
<thead><tr><th>Usuario/Cliente</th><th>Estado</th><th>Límite</th><th>Vence</th><th></th></tr></thead>
<tbody id="tbody-usuarios"><tr><td colspan="5">Cargando…</td></tr></tbody>
</table>
</div>

<script>
function toggleModo(){
  var hwid = document.querySelector('input[name=modo_sel]:checked').value === 'hwid';
  document.getElementById('modo-input').value = hwid ? 'hwid' : 'normal';
  document.getElementById('campos-hwid').classList.toggle('hide', !hwid);
  document.getElementById('campos-normal').classList.toggle('hide', hwid);
}

function fila(u){
  var estado = u.online > 0
    ? '<span class="pill on">● online ('+u.online+')</span>'
    : '<span class="pill off">○ offline</span>';
  return '<tr>' +
    '<td>'+u.nombre+'</td>' +
    '<td>'+estado+'</td>' +
    '<td>'+u.limite+'</td>' +
    '<td>'+u.dias+'</td>' +
    '<td>' +
      '<form class="inline" method="post" action="/eliminar/'+encodeURIComponent(u.usuario)+'" onsubmit="return confirm(\\'¿Borrar '+u.nombre+'?\\')">' +
        '<button class="danger" type="submit">Borrar</button>' +
      '</form>' +
    '</td>' +
  '</tr>';
}

function actualizar(){
  fetch('/api/usuarios').then(r => r.json()).then(d => {
    document.getElementById('s-ram').textContent = d.stats.mu + '/' + d.stats.mt + 'MB';
    document.getElementById('s-cpu').textContent = d.stats.cpu + '%';
    document.getElementById('s-cuentas').textContent = d.stats.cuentas;
    document.getElementById('s-online').textContent = d.stats.online;
    var tb = document.getElementById('tbody-usuarios');
    if (d.usuarios.length === 0){ tb.innerHTML = '<tr><td colspan="5">No hay usuarios</td></tr>'; return; }
    tb.innerHTML = d.usuarios.map(fila).join('');
  }).catch(() => {});
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


if __name__ == "__main__":
    port = int(CONF.get("PORT", "9090"))
    app.run(host="0.0.0.0", port=port)
