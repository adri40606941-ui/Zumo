"""Máquinas: las VPS del administrador que el bot maneja por SSH.

- Guardado: /etc/zumo/maquinas.enc, cifrado con AES-256 (openssl, igual que los respaldos).
  La clave de cifrado está en /etc/zumo/maquinas.key (0600), fuera del repo.
- Conexión: SSH con usuario y contraseña. La huella del servidor se guarda al agregarlo y,
  si después cambia, el bot no se conecta (puede ser un ataque o una reinstalación).
- Protocolos: son los servicios de systemd que instala el panel (PDirect, BadVPN, HCR, BHTTP).
  El bot solo los prende o apaga; no los instala.
"""
import base64
import hashlib
import json
import os
import subprocess
import uuid

DIR = os.environ.get("ZUMO_MAQUINAS_DIR", "/etc/zumo")
ARCHIVO = os.path.join(DIR, "maquinas.enc")
CLAVE = os.path.join(DIR, "maquinas.key")
UNIDADES = "/etc/systemd/system"

# nombre visible -> unidades de systemd que lo forman (en el orden en que se prenden)
PROTOCOLOS = [
    ("PDirect (WebSocket 80)", ["pdirect-80"]),
    ("BadVPN (UDPGW 7300)", ["udpgw-7300"]),
    ("HCR Server", ["hcr-server"]),
    ("BHTTP", ["bhttp-server", "bhttp-shim"]),
    ("BHTTP v2", ["bhttp-v2"]),
]


class ErrorMaquina(Exception):
    """Algo no anduvo con una máquina (se muestra tal cual en el chat)."""


# ------------------------------------------------------------------ cifrado y guardado
def _clave_cifrado(crear=False):
    if not os.path.exists(CLAVE):
        if not crear:
            return None
        fd = os.open(CLAVE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(base64.b64encode(os.urandom(32)).decode() + "\n")
    with open(CLAVE, encoding="utf-8") as f:
        return f.read().strip()


def _openssl(modo, datos, clave):
    r = subprocess.run(["openssl", "enc", modo, "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                        "-pass", "env:ZUMO_MAQ"],
                       input=datos, capture_output=True, env={**os.environ, "ZUMO_MAQ": clave})
    if r.returncode != 0:
        raise ErrorMaquina("No pude cifrar/descifrar la lista de máquinas.")
    return r.stdout


def cargar():
    """Lista de máquinas (descifrada). Vacía si todavía no hay ninguna."""
    if not os.path.exists(ARCHIVO):
        return []
    clave = _clave_cifrado()
    if not clave:
        raise ErrorMaquina("Falta la clave de las máquinas (maquinas.key). No puedo abrir la lista.")
    with open(ARCHIVO, "rb") as f:
        cifrado = f.read()
    return json.loads(_openssl("-d", cifrado, clave).decode("utf-8"))


def guardar(lista):
    clave = _clave_cifrado(crear=True)
    datos = _openssl("-e", json.dumps(lista, ensure_ascii=False).encode("utf-8"), clave)
    tmp = ARCHIVO + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(datos)
    os.replace(tmp, ARCHIVO)


def agregar(nombre, host, puerto, usuario, clave, huella):
    lista = cargar()
    m = {"id": uuid.uuid4().hex[:8], "nombre": nombre, "host": host, "puerto": int(puerto),
         "usuario": usuario, "clave": clave, "huella": huella}
    lista.append(m)
    guardar(lista)
    return m


def buscar(mid):
    for m in cargar():
        if m["id"] == mid:
            return m
    return None


def quitar(mid):
    guardar([m for m in cargar() if m["id"] != mid])


def actualizar_huella(mid, huella):
    lista = cargar()
    for m in lista:
        if m["id"] == mid:
            m["huella"] = huella
    guardar(lista)


# ------------------------------------------------------------------ SSH
def _huella(cliente):
    clave = cliente.get_transport().get_remote_server_key()
    return "SHA256:" + base64.b64encode(hashlib.sha256(clave.asbytes()).digest()).decode().rstrip("=")


def conectar(m, timeout=10):
    """Abre SSH a la máquina. Devuelve (cliente, huella). Falla si la huella no coincide."""
    try:
        import paramiko
    except ImportError:
        raise ErrorMaquina("Falta paramiko en el servidor (apt install python3-paramiko).")

    class _SinAviso(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            pass  # la huella se compara después de conectar

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(_SinAviso())
    try:
        cli.connect(m["host"], port=m["puerto"], username=m["usuario"], password=m["clave"],
                    timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                    look_for_keys=False, allow_agent=False)
    except paramiko.AuthenticationException:
        cli.close()
        raise ErrorMaquina("El servidor no aceptó el usuario o la contraseña.")
    except Exception as e:
        cli.close()
        raise ErrorMaquina(f"No pude conectar a {m['host']}:{m['puerto']} ({e.__class__.__name__}).")
    h = _huella(cli)
    if m.get("huella") and h != m["huella"]:
        cli.close()
        raise ErrorMaquina("La huella del servidor cambió. Puede ser un ataque o que lo reinstalaron. "
                           "Si confiás en el cambio, quitá la máquina y agregala de nuevo.")
    return cli, h


def correr(m, comando, timeout=20):
    cli, h = conectar(m)
    try:
        _, salida, _ = cli.exec_command(comando, timeout=timeout)
        return salida.read().decode("utf-8", "replace")
    finally:
        cli.close()


def probar(host, puerto, usuario, clave):
    """Conecta una vez para validar los datos y devuelve la huella del servidor."""
    cli, h = conectar({"host": host, "puerto": int(puerto), "usuario": usuario, "clave": clave, "huella": ""})
    cli.close()
    return h


# ------------------------------------------------------------------ recursos
COMANDO_RECURSOS = r"""
red() { awk -F: 'NR>2 { n=$1; gsub(/ /,"",n); if (n=="lo" || n ~ /^(veth|docker|br-)/) next; split($2,a," "); rx+=a[1]; tx+=a[9] } END { print rx+0, tx+0 }' /proc/net/dev; }
a=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5}' /proc/stat); r1=$(red); sleep 1
b=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5}' /proc/stat); r2=$(red)
echo "CPU $a $b"
echo "RED $r1 $r2"
echo "CORES $(nproc)"
free -m | awk '/^Mem:/{print "RAM", $2, $3, $7}'
df -k / | awk 'NR==2{print "DISCO", $2, $3, $4}'
echo "CARGA $(cut -d' ' -f1-3 /proc/loadavg)"
echo "ACTIVO $(uptime -p 2>/dev/null)"
echo "SESIONES $(who | wc -l)"
"""


def parsear_recursos(texto):
    """Convierte la salida de COMANDO_RECURSOS en un dict. Lo que no se entienda queda en None."""
    r = {"cpu": None, "cores": None, "ram": None, "disco": None, "disco_libre": None, "red": None,
         "carga": None, "activo": None, "sesiones": None}
    for linea in texto.splitlines():
        p = linea.split()
        if not p:
            continue
        try:
            if p[0] == "CPU" and len(p) == 5:
                t1, i1, t2, i2 = int(p[1]), int(p[2]), int(p[3]), int(p[4])
                dt, di = t2 - t1, i2 - i1
                r["cpu"] = round(100 * (dt - di) / dt, 1) if dt > 0 else 0.0
            elif p[0] == "CORES":
                r["cores"] = int(p[1])
            elif p[0] == "RAM" and len(p) >= 3:
                r["ram"] = (int(p[2]), int(p[1]))            # (usada MB, total MB)
            elif p[0] == "DISCO" and len(p) >= 3:
                r["disco"] = (int(p[2]) // 1024, int(p[1]) // 1024)   # (usado MB, total MB)
                libre_kb = int(p[3]) if len(p) >= 4 else int(p[1]) - int(p[2])
                r["disco_libre"] = max(0, libre_kb) // 1024            # libre MB
            elif p[0] == "RED" and len(p) == 5:
                # bytes por segundo en el último segundo (todas las placas menos lo y las de docker)
                r["red"] = (max(0, int(p[3]) - int(p[1])), max(0, int(p[4]) - int(p[2])))   # (baja, sube)
            elif p[0] == "CARGA":
                r["carga"] = " · ".join(p[1:4])
            elif p[0] == "ACTIVO":
                r["activo"] = linea[len("ACTIVO"):].strip()
            elif p[0] == "SESIONES":
                r["sesiones"] = int(p[1])
        except (ValueError, IndexError):
            pass
    return r


def recursos(m):
    return parsear_recursos(correr(m, COMANDO_RECURSOS, timeout=25))


# ------------------------------------------------------------------ protocolos
def comando_estado():
    unidades = sorted({u for _, us in PROTOCOLOS for u in us})
    partes = []
    for u in unidades:
        partes.append(f'if [ -f {UNIDADES}/{u}.service ]; then echo "{u} $(systemctl is-active {u} 2>/dev/null)"; else echo "{u} no"; fi')
    return "; ".join(partes)


def parsear_estado(texto):
    """Devuelve {unidad: 'active' | 'inactive' | ... | 'no'} según la salida de comando_estado()."""
    estado = {}
    for linea in texto.splitlines():
        p = linea.split()
        if len(p) == 2:
            estado[p[0]] = p[1]
    return estado


def estado_protocolos(m):
    """Lista de (nombre, estado) con estado: 'activo', 'inactivo', 'no instalado' o 'parcial'."""
    est = parsear_estado(correr(m, comando_estado()))
    salida = []
    for nombre, unidades in PROTOCOLOS:
        vistas = [est.get(u, "no") for u in unidades]
        if all(v == "no" for v in vistas):
            e = "no instalado"
        elif all(v == "active" for v in vistas):
            e = "activo"
        elif any(v == "active" for v in vistas):
            e = "parcial"
        else:
            e = "inactivo"
        salida.append((nombre, e))
    return salida


def cambiar_protocolo(m, nombre, encender):
    """Prende o apaga un protocolo. Solo toca unidades que ya existen en la máquina."""
    unidades = dict(PROTOCOLOS).get(nombre)
    if unidades is None:
        raise ErrorMaquina("Ese protocolo no existe.")
    existen = parsear_estado(correr(m, comando_estado()))
    presentes = [u for u in unidades if existen.get(u, "no") != "no"]
    if not presentes:
        raise ErrorMaquina(f"{nombre} no está instalado en esta máquina. Instalalo desde el panel.")
    accion = "enable --now" if encender else "disable --now"
    orden = presentes if encender else list(reversed(presentes))
    cmd = f"systemctl {accion} {' '.join(orden)} 2>&1; echo FIN=$?"
    salida = correr(m, cmd, timeout=60)
    if "FIN=0" not in salida:
        raise ErrorMaquina(f"No se pudo cambiar {nombre}: {salida.strip()[-300:]}")
