#!/usr/bin/env python3
"""
Prueba de compatibilidad BHTTP v3 (imita a DTunnel).
Uso:  python3 bhttp-test.py [host] [puerto]      (por defecto 127.0.0.1 8001)
Prueba varias combinaciones de numeros de secuencia con sesiones nuevas
y dice cual hace que sshd devuelva su banner "SSH-2.0-..." por el tunel.
"""
import hashlib, os, socket, struct, subprocess, sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8001
DOWN = 1350
SID = b""


def xor(data, mode, seq, resp):
    out = bytearray()
    for i in range(0, len(data), 32):
        k = hashlib.sha256(SID + bytes([mode]) + struct.pack(">Q", seq)
                           + bytes([1 if resp else 0]) + struct.pack(">I", i // 32)).digest()
        out += bytes(a ^ b for a, b in zip(data[i:i + 32], k))
    return bytes(out)


def rd(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise EOFError("conexion cerrada (%d/%d bytes)" % (len(b), n))
        b += c
    return b


def request(mode, seq, payload=b"", length=None, timeout=8):
    s = socket.create_connection((HOST, PORT), timeout=timeout)
    body = xor(payload, mode, seq, False) if payload else b""
    ln = len(payload) if length is None else length
    s.sendall(bytes([mode]) + SID + struct.pack(">Q", seq) + struct.pack(">I", ln) + body)
    hdr = rd(s, 5)
    st, n = hdr[0], struct.unpack(">I", hdr[1:])[0]
    data = rd(s, n) if 0 < n <= 524288 else b""
    s.close()
    return st, data


def ssh_conns():
    try:
        o = subprocess.run(["ss", "-tnH", "state", "established", "( dport = :22 )"],
                           capture_output=True, text=True).stdout
        return len([l for l in o.splitlines() if "127.0.0.1:22" in l])
    except Exception:
        return -1


def decode_download(st, d):
    if st == 2 and len(d) >= 4:
        n = struct.unpack(">I", d[:4])[0]
        return n, d[4:4 + n]
    return None, d


def scenario(name, up_seqs, down_seq, wait):
    global SID
    SID = os.urandom(16)
    print("\n== %s" % name)
    try:
        st, d = request(1, 0, b"")
        print("   abrir sesion (modo 1, seq 0, vacio): estado %d %r" % (st, d[:40]))
        if st != 0:
            return False
        for sq in up_seqs:
            st, d = request(1, sq, b"SSH-2.0-prueba_bhttp\r\n")
            print("   subir banner seq=%d: estado %d %r" % (sq, st, d[:40]))
        print("   conexiones del servidor hacia sshd: %d" % ssh_conns())
        st, d = request(2, down_seq, b"", length=DOWN, timeout=wait)
        n, data = decode_download(st, d)
        print("   descargar seq=%d: estado %d, %d bytes recibidos" % (down_seq, st, len(d)))
        for sq in (down_seq, 0, 1):
            p = xor(data, 2, sq, True) if data else b""
            if p.startswith(b"SSH-"):
                print("   [OK] sshd respondio por el tunel: %r" % p[:40])
                return True
        print("   [FALLA] llegaron datos pero no parecen el banner de sshd (%r)" % (data[:24],))
    except Exception as e:
        print("   [FALLA] %s  (conexiones hacia sshd: %d)" % (e, ssh_conns()))
    return False


def batch_scenario(name, up_seqs, count, polls, wait=6):
    """Descarga en lote (modo 3) repetida (como hace la app): cuerpo = tamano(4) + cantidad(2).
    El servidor responde en ~2 ms con lo que haya, asi que se consulta varias veces."""
    global SID
    import time
    SID = os.urandom(16)
    print("\n== %s" % name)
    try:
        st, d = request(1, 0, b"")
        print("   abrir sesion: estado %d" % st)
        if st != 0:
            return False
        for sq in up_seqs:
            st, d = request(1, sq, b"SSH-2.0-prueba_bhttp\r\n")
            print("   subir banner seq=%d: estado %d %r" % (sq, st, d[:40]))
        seq = 0
        got = b""
        for poll in range(polls):
            body = struct.pack(">I", DOWN) + struct.pack(">H", count)
            s = socket.create_connection((HOST, PORT), timeout=wait)
            s.sendall(bytes([3]) + SID + struct.pack(">Q", seq) + struct.pack(">I", len(body))
                      + xor(body, 3, seq, False))
            sizes = []
            for k in range(count):
                hdr = rd(s, 5)
                st, n = hdr[0], struct.unpack(">I", hdr[1:])[0]
                data = rd(s, n) if 0 < n <= 524288 else b""
                if st != 2:
                    print("   consulta %d: estado %d, mensaje %r" % (poll, st, data[:80]))
                    sizes.append(-1)
                    continue
                dl = struct.unpack(">I", data[:4])[0] if len(data) >= 4 else 0
                sizes.append(dl)
                if dl:
                    got += xor(data[4:4 + dl], 3, seq + k, True)
            s.close()
            if poll < 3 or any(x > 0 for x in sizes):
                print("   consulta %d (seq=%d): datos por respuesta %s" % (poll, seq, sizes))
            seq += count
            if got.startswith(b"SSH-"):
                print("   [OK] sshd respondio por el tunel: %r" % got[:40])
                return True
            time.sleep(0.25)
        print("   [FALLA] tras %d consultas no llego el banner (recibido: %r)" % (polls, got[:40]))
    except Exception as e:
        print("   [FALLA] %s  (conexiones hacia sshd: %d)" % (e, ssh_conns()))
    return False


print("Servidor %s:%d" % (HOST, PORT))
SID = os.urandom(16)
st, d = request(0, 0, b"BHP1" + bytes([1, 0]) + b"\0\0\0\0")
p = xor(d, 0, 0, True) if st == 0 else b""
print("Sonda: %s" % ("OK" if p[:4] == b"BHP1" else "FALLA (estado %d)" % st))
if p[:4] != b"BHP1":
    sys.exit(1)

good = []
if batch_scenario("E: lote modo 3, 1 respuesta por consulta", [0], 1, 20):
    good.append("E (modo 3, x1)")
if batch_scenario("F: lote modo 3, 2 respuestas por consulta", [0], 2, 20):
    good.append("F (modo 3, x2)")
print("\n" + ("Funcionan: " + "; ".join(good) if good else
      "Ninguna combinacion devolvio el banner de sshd."))
