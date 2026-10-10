#!/usr/bin/env python3
"""
Prueba del adaptador BHTTP con espera larga (fuentes/bhttp-shim) contra el servidor BHTTP real.
Necesita una máquina x86_64 (los binarios de binarios/ son de esa arquitectura).
Uso: python3 tests/prueba-bhttp-espera.py
Comprueba:
  - un lote de bajada vacío se retiene ~espera y luego se contesta,
  - si llegan datos mientras espera, se contestan enseguida (sin esperar todo el plazo),
  - repetir el mismo pedido da la misma respuesta (la app reintenta si pierde la respuesta),
  - la sesión sigue por otra conexión TCP, y modo 2 (DTunnel) y modo 4 (cerrar) siguen andando.
"""
import hashlib, os, platform, shutil, socket, struct, subprocess, sys, tempfile, threading, time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if platform.machine() not in ("x86_64", "AMD64"):
    print("SALTADA: hace falta x86_64"); sys.exit(0)
TMP = tempfile.mkdtemp()
SERVIDOR, SHIM = os.path.join(TMP, "servidor"), os.path.join(TMP, "shim")
for origen, destino_ in (("bhttp-server-amd64", SERVIDOR), ("bhttp-shim-amd64", SHIM)):
    shutil.copy(os.path.join(RAIZ, "binarios", origen), destino_); os.chmod(destino_, 0o755)
SSH_FALSO, INTERNO, PUBLICO = 28226, 28201, 28202
fallos = []


def ok(cond, msg):
    print(("  ok  " if cond else "  MAL ") + msg)
    if not cond:
        fallos.append(msg)


def destino():
    l = socket.socket(); l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    l.bind(("127.0.0.1", SSH_FALSO)); l.listen(5)
    while True:
        c, _ = l.accept()
        def f(c=c):
            time.sleep(1.2); c.sendall(b"PRIMER")
            time.sleep(1.0); c.sendall(b"SEGUNDO")
            while c.recv(1000):
                pass
        threading.Thread(target=f, daemon=True).start()


def xor(sid, modo, seq, resp, d):
    o = bytearray()
    for i in range(0, len(d), 32):
        k = hashlib.sha256(sid + bytes([modo]) + struct.pack(">Q", seq) + bytes([1 if resp else 0]) + struct.pack(">I", i // 32)).digest()
        o += bytes(a ^ b for a, b in zip(d[i:i + 32], k))
    return bytes(o)


def rd(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise EOFError(len(b))
        b += c
    return b


def send(s, modo, sid, seq, body=b""):
    s.sendall(bytes([modo]) + sid + struct.pack(">Q", seq) + struct.pack(">I", len(body)) + xor(sid, modo, seq, False, body))


def frame(s):
    h = rd(s, 5); return h[0], rd(s, struct.unpack(">I", h[1:])[0])


def lote(s, sid, seq, cant=4):
    send(s, 3, sid, seq, struct.pack(">IH", 16384, cant)); t = time.time()
    fr = [frame(s) for _ in range(cant)]
    datos = b"".join(xor(sid, 3, seq + k, True, b[4:4 + struct.unpack(">I", b[:4])[0]]) for k, (st, b) in enumerate(fr) if st == 2 and len(b) >= 4)
    return fr, datos, time.time() - t


def main():
    threading.Thread(target=destino, daemon=True).start()
    bs = subprocess.Popen([SERVIDOR, "-listen", "127.0.0.1", "-port", str(INTERNO), "-backend-port", str(SSH_FALSO)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sh = subprocess.Popen([SHIM, "-listen", "127.0.0.1:%d" % PUBLICO, "-backend", "127.0.0.1:%d" % INTERNO], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        sid = os.urandom(16)
        s = socket.create_connection(("127.0.0.1", PUBLICO)); send(s, 1, sid, 0); ok(frame(s)[0] == 0, "abrir sesión")
        b = socket.create_connection(("127.0.0.1", PUBLICO)); b.settimeout(20)
        f1, d1, t1 = lote(b, sid, 0)
        ok(d1 == b"" and 0.8 < t1 < 1.5, "lote vacío: se retiene ~1 s (%.2f s) y vuelve vacío" % t1)
        f1b, d1b, t1b = lote(b, sid, 0)
        ok(f1b == f1 and t1b < 0.3, "pedido repetido: misma respuesta al instante")
        f2, d2, t2 = lote(b, sid, 4)
        ok(d2 == b"PRIMER" and t2 < 0.6, "datos que llegan durante la espera: salen enseguida (%.2f s)" % t2)
        b2 = socket.create_connection(("127.0.0.1", PUBLICO)); b2.settimeout(20)
        f3, d3, t3 = lote(b2, sid, 8)
        ok(d3 == b"SEGUNDO", "la sesión sigue por una conexión TCP nueva")
        send(s, 1, sid, 1, b"hola"); ok(frame(s)[0] == 0, "subida de datos")
        s3 = socket.create_connection(("127.0.0.1", PUBLICO)); send(s3, 4, sid, 0); ok(frame(s3)[0] == 0, "cerrar sesión")
        # modo 2 (DTunnel): sin cuerpo, el largo es el tamaño pedido
        sid2 = os.urandom(16); m = socket.create_connection(("127.0.0.1", PUBLICO)); send(m, 1, sid2, 0); frame(m)
        time.sleep(1.5)
        d = socket.create_connection(("127.0.0.1", PUBLICO)); d.settimeout(10)
        d.sendall(bytes([2]) + sid2 + struct.pack(">Q", 0) + struct.pack(">I", 16384))
        st, body = frame(d); n = struct.unpack(">I", body[:4])[0]
        ok(st == 2 and xor(sid2, 2, 0, True, body[4:4 + n]) == b"PRIMER", "modo 2 (DTunnel) sigue funcionando")
    finally:
        sh.terminate(); bs.terminate(); shutil.rmtree(TMP, ignore_errors=True)
    print("FALLÓ" if fallos else "TODO OK"); sys.exit(1 if fallos else 0)


main()
