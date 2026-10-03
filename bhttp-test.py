#!/usr/bin/env python3
"""
Prueba de compatibilidad BHTTP (imita lo que hace DTunnel).
Uso:  python3 bhttp-test.py [host] [puerto]      (por defecto 127.0.0.1 8001)
Hace 3 pasos y dice en cual falla:
  1) Sonda (modo 0)           -> "BHTTP session connected" en la app
  2) Subida de banner SSH (1) -> el servidor abre la conexion a sshd
  3) Descarga (modo 2)        -> debe devolver el banner "SSH-2.0-..." de sshd
"""
import hashlib, os, socket, struct, sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8001
SID = os.urandom(16)
DOWN = 1350          # tamano de descarga que muestra la app (↓ 1350B)


def xor(data, mode, seq, resp):
    """Ofuscado de la app: XOR con SHA256(sid|modo|seq|flag|contador)."""
    out = bytearray()
    for i in range(0, len(data), 32):
        k = hashlib.sha256(SID + bytes([mode]) + struct.pack(">Q", seq)
                           + bytes([1 if resp else 0]) + struct.pack(">I", i // 32)).digest()
        blk = data[i:i + 32]
        out += bytes(a ^ b for a, b in zip(blk, k))
    return bytes(out)


def probe_payload():
    p = bytearray(b"BHP1" + bytes([1, 0]) + struct.pack(">I", 0))
    return bytes(p)


def rd(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise EOFError("el servidor cerro la conexion tras %d/%d bytes" % (len(b), n))
        b += c
    return b


def request(mode, seq, payload=b"", length=None, obf=True):
    s = socket.create_connection((HOST, PORT), timeout=8)
    body = xor(payload, mode, seq, False) if (payload and obf) else payload
    ln = len(payload) if length is None else length
    s.sendall(bytes([mode]) + SID + struct.pack(">Q", seq) + struct.pack(">I", ln) + body)
    hdr = rd(s, 5)
    st, n = hdr[0], struct.unpack(">I", hdr[1:])[0]
    data = rd(s, n) if 0 < n <= 524288 else b""
    s.close()
    return st, data


def ok(msg):  print("  [OK]  " + msg)
def bad(msg): print("  [FALLA] " + msg)


print("Servidor %s:%d  SID=%s" % (HOST, PORT, SID.hex()))

# --- 1) sonda
print("1) Sonda (modo 0)")
try:
    st, d = request(0, 0, probe_payload())
    if st != 0:
        bad("estado %d: %r" % (st, d[:80]))
    else:
        plain = xor(d, 0, 0, True)
        if plain[:4] == b"BHP1" and len(plain) == 10:
            ok("el servidor responde a la sonda correctamente")
        else:
            bad("responde, pero la respuesta no tiene el formato que espera la app: %r" % plain[:20])
except Exception as e:
    bad("%s" % e)
    sys.exit(1)

# --- 2) subir banner SSH
print("2) Subida de datos (modo 1)")
banner = b"SSH-2.0-prueba_bhttp\r\n"
up_seq = None
for seq in (0, 1):
    try:
        st, d = request(1, seq, banner)
        print("     seq=%d -> estado %d %r" % (seq, st, d[:60]))
        if st == 0:
            up_seq = seq
            break
    except Exception as e:
        print("     seq=%d -> %s" % (seq, e))
if up_seq is None:
    bad("el servidor no acepta la subida")
    sys.exit(1)
ok("subida aceptada (seq=%d)" % up_seq)

# --- 3) descargar la respuesta de sshd
print("3) Descarga (modo 2): esperando el banner de sshd")
found = False
for seq in (0, 1, 2, 3):
    try:
        st, d = request(2, seq, b"", length=DOWN)
    except Exception as e:
        print("     seq=%d -> %s" % (seq, e))
        continue
    info = "estado %d, %d bytes" % (st, len(d))
    if st == 2 and len(d) >= 4:
        n = struct.unpack(">I", d[:4])[0]
        data = d[4:4 + n]
        for sq in (seq, 0, 1):
            plain = xor(data, 2, sq, True)
            if plain.startswith(b"SSH-"):
                ok("sshd respondio por el tunel: %r" % plain[:40])
                found = True
                break
        if found:
            break
        info += ", datos=%d (no parecen un banner SSH)" % n
    print("     seq=%d -> %s" % (seq, info))
if not found:
    bad("la descarga no devolvio el banner de sshd (aqui se traba la app)")
    sys.exit(2)
print("\nTodo OK: el servidor habla el mismo protocolo que la app.")
