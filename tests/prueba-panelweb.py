#!/usr/bin/env python3
"""Prueba del panel web con el cliente de pruebas de Flask.

Comprueba que:
  - sin sesión no se puede operar;
  - un POST que viene de otro origen se rechaza (403);
  - /eliminar/root y compañía no tocan cuentas que no están en usuarios.db (404);
  - un usuario del panel sí se puede operar.

No arranca el servidor ni toca usuarios reales: usa un usuario de prueba, un
web.conf y un usuarios.db temporales (apuntados por variables en memoria).
Uso: sudo python3 tests/prueba-panelweb.py   (necesita root, flask y useradd)
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash

AQUI = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="zumo-web-"))
(tmp / "web.conf").write_text(
    f"WEB_USER=admin\nWEB_PASS_HASH={generate_password_hash('clave123')}\nSECRET_KEY=prueba\nPORT=9090\n"
)
(tmp / "usuarios.db").write_text("zweb1:1:2099-01-01\n")

# Cargamos panelweb.py apuntando sus rutas al directorio temporal.
src = (AQUI / "panelweb.py").read_text()
src = src.replace('"/etc/zumo/web.conf"', repr(str(tmp / "web.conf")))
src = src.replace('"/etc/zumo/usuarios.db"', repr(str(tmp / "usuarios.db")))
src = src.replace('"/etc/zumo/usuarios.lock"', repr(str(tmp / "usuarios.lock")))
mod_path = tmp / "panelweb_test.py"
mod_path.write_text(src)
spec = importlib.util.spec_from_file_location("panelweb_test", mod_path)
pw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pw)

fallos = 0


def chequear(desc, esperado, real):
    global fallos
    if esperado == real:
        print(f"  ok   {desc} ({real})")
    else:
        print(f"  FALLA {desc}: esperado {esperado}, real {real}")
        fallos += 1


def existe(u):
    return subprocess.run(["id", u], capture_output=True).returncode == 0


subprocess.run(["useradd", "-M", "-s", "/bin/false", "zweb1"], capture_output=True)
try:
    c = pw.app.test_client()
    H = {"Origin": "http://localhost"}  # el test client usa host "localhost"

    print("1) sin sesión")
    r = c.post("/eliminar/zweb1", headers=H)
    chequear("POST sin login redirige al login", 302, r.status_code)
    chequear("y el usuario sigue existiendo", True, existe("zweb1"))

    r = c.post("/login", data={"usuario": "admin", "password": "clave123"}, headers=H)
    chequear("login correcto", 302, r.status_code)

    print("2) origen distinto")
    r = c.post("/eliminar/zweb1", headers={"Origin": "http://sitio-malo.example"})
    chequear("POST de otro origen", 403, r.status_code)
    chequear("el usuario sigue existiendo", True, existe("zweb1"))
    r = c.post("/bloquear/zweb1", headers={"Referer": "http://sitio-malo.example/x"})
    chequear("POST con Referer ajeno", 403, r.status_code)

    print("3) cuentas que no son del panel")
    for ruta in ("/eliminar/root", "/bloquear/root", "/renovar/root", "/limite/root", "/hwid/root"):
        r = c.post(ruta, data={"dias": "5", "limite": "5", "nuevo_hwid": "ABCDEFGH1234"}, headers=H)
        chequear(f"{ruta}", 404, r.status_code)
    chequear("root sigue existiendo", True, existe("root"))

    print("4) cookie de sesión")
    cookies = c.get_cookie("session")
    chequear("SameSite=Strict", "Strict", (cookies.same_site if cookies else None))

    print("5) usuario del panel sí se puede operar")
    r = c.post("/limite/zweb1", data={"limite": "3"}, headers=H)
    chequear("cambiar límite", 302, r.status_code)
    chequear("límite guardado", "zweb1:3:2099-01-01", (tmp / "usuarios.db").read_text().strip())
    r = c.post("/eliminar/zweb1", headers=H)
    chequear("eliminar usuario del panel", 302, r.status_code)
    chequear("ya no existe", False, existe("zweb1"))

    print("6) crear con límite 0 se corrige a 1; días absurdos se rechazan")
    r = c.post("/crear", data={"modo": "normal", "usuario": "zweb2", "password": "abc12345", "dias": "2", "limite": "0"}, headers=H)
    chequear("crear", 302, r.status_code)
    fila = [l for l in (tmp / "usuarios.db").read_text().splitlines() if l.startswith("zweb2:")]
    chequear("límite mínimo 1", True, bool(fila) and fila[0].split(":")[1] == "1")
    r = c.post("/crear", data={"modo": "normal", "usuario": "zweb3", "password": "abc12345", "dias": "99999999", "limite": "1"}, headers=H)
    chequear("días absurdos no crean usuario", False, existe("zweb3"))
finally:
    for u in ("zweb1", "zweb2", "zweb3"):
        subprocess.run(["userdel", u], capture_output=True)

print()
if fallos == 0:
    print("TODO OK")
else:
    print(f"{fallos} prueba(s) fallaron")
    sys.exit(1)
