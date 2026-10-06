import socket, subprocess, threading, time, os, sys, glob, tempfile, shutil

AQUI = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(AQUI, "..", "zumo-go")
BIN = os.path.join(tempfile.mkdtemp(), "zumo-go")
if shutil.which("go"):
    r = subprocess.run(["go", "build", "-o", BIN, "."], cwd=SRC, capture_output=True, text=True)
    if r.returncode != 0:
        print("no compila zumo-go:\n" + r.stderr); sys.exit(1)
else:
    cand = os.path.join(AQUI, "..", "zumo-go-amd64")
    if not os.path.exists(cand):
        print("falta go y el binario zumo-go-amd64"); sys.exit(1)
    BIN = cand

# servidor "ssh" de mentira: devuelve lo que recibe (echo)
echo = socket.socket(); echo.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
echo.bind(("127.0.0.1", 0)); echo.listen(5); EP = echo.getsockname()[1]
def echo_srv():
    while True:
        try: c,_ = echo.accept()
        except OSError: return
        def h(c):
            try:
                while True:
                    d = c.recv(4096)
                    if not d: break
                    c.sendall(d)
            except OSError: pass
            finally: c.close()
        threading.Thread(target=h, args=(c,), daemon=True).start()
threading.Thread(target=echo_srv, daemon=True).start()

LP = 18080
env = dict(os.environ, PDIRECT_BANNER="TESTBAN", PDIRECT_COLOR="lime")
proc = subprocess.Popen([BIN, str(EP), str(LP)], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(0.6)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FALLA ") + name)
    if not cond: fails += 1

try:
    s = socket.create_connection(("127.0.0.1", LP), timeout=5); s.settimeout(5)
    s.sendall(b"GET /ssh HTTP/1.1\r\nHost: cdn.x.net\r\nUpgrade: websocket\r\n\r\nHELLO-SSH")
    # leer la respuesta 101 (hasta el doble \r\n\r\n del banner doble)
    data = b""
    while b"Conexion Exitosa\r\n\r\n" not in data:
        data += s.recv(1024)
    check("responde 101", data.startswith(b"HTTP/1.1 101"))
    check("incluye el banner (TESTBAN)", b"TESTBAN" in data)
    check("incluye el color (lime)", b'color="lime"' in data)
    # lo que mandamos despues del header (HELLO-SSH) tiene que volver por el echo
    eco = s.recv(1024)
    check("relayea al SSH (echo de HELLO-SSH)", eco == b"HELLO-SSH")
    # mandar mas y ver que sigue el tunel
    s.sendall(b"PING123"); check("tunel bidireccional", s.recv(1024) == b"PING123")
    # pmap: debe haberse creado un archivo con la IP real
    pm = glob.glob("/run/zumo/pmap/*")
    ipok = any(open(p).read().strip() == "127.0.0.1" for p in pm) if pm else False
    check("anota la IP real en pmap", ipok)
    s.close()

    # X-Split: el segmento partido se descarta, igual responde 101 y tunela
    s2 = socket.create_connection(("127.0.0.1", LP), timeout=5); s2.settimeout(5)
    s2.sendall(b"GET / HTTP/1.1\r\nX-Split\r\nHost: x\r\n\r\n")
    time.sleep(0.2); s2.sendall(b"BASURA-SPLIT")   # segmento partido -> se descarta
    d2 = b""
    while b"Conexion Exitosa\r\n\r\n" not in d2:
        d2 += s2.recv(1024)
    check("X-Split tambien responde 101", d2.startswith(b"HTTP/1.1 101"))
    s2.sendall(b"DESPUES"); check("X-Split tunela lo de despues", s2.recv(1024) == b"DESPUES")
    s2.close()
finally:
    proc.terminate(); echo.close()

print("TODO OK" if fails == 0 else f"{fails} fallaron")
sys.exit(1 if fails else 0)
