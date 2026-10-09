#!/bin/bash
# Prueba de PDirect v2 (fuentes/pdirect2.c): compila el servicio de verdad y lo prueba
# contra un "SSH" falso. Comprueba el handshake WebSocket RFC 6455 (Sec-WebSocket-Accept),
# que sin Sec-WebSocket-Key conteste igual que PDirect v1 y que el túnel pase datos.
#
# Uso: bash tests/prueba-pdirect2.sh      (necesita gcc, libevent-dev y python3)
#      EVENT_PREFIX=/ruta/libevent bash tests/prueba-pdirect2.sh   (libevent en otro lugar)
set -u
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-pd2.XXXXXX)
PIDS=""
FALLOS=0
trap 'kill $PIDS 2>/dev/null; rm -rf "$T"' EXIT
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }

INC=""; LIB="-levent_core"
[ -n "${EVENT_PREFIX:-}" ] && { INC="-I$EVENT_PREFIX/include"; LIB="$EVENT_PREFIX/lib/libevent_core.a"; }
gcc -O2 -Wall -o "$T/pdirect2-c" "$AQUI/fuentes/pdirect2.c" $INC $LIB 2>"$T/cc.log" || { cat "$T/cc.log"; echo "no compila"; exit 1; }

cat > "$T/ssh.py" <<'PY'
import socket, sys, threading
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(5)
def h(c):
    c.sendall(b"SSH-2.0-falso\r\n")
    d = c.recv(100)
    c.sendall(b"eco:" + d)
    c.close()
while True:
    c, _ = s.accept(); threading.Thread(target=h, args=(c,), daemon=True).start()
PY
python3 "$T/ssh.py" 24022 & PIDS="$PIDS $!"
"$T/pdirect2-c" 24022 28080 & PIDS="$PIDS $!"
sleep 1

cat > "$T/cli.py" <<'PY'
import socket, sys, time
req = sys.argv[1].encode().decode("unicode_escape").encode("latin1")
s = socket.create_connection(("127.0.0.1", 28080)); s.settimeout(3)
s.sendall(req); time.sleep(0.5)
buf = b""
try:
    while len(buf) < 4000:
        d = s.recv(4096)
        if not d: break
        buf += d
        if b"eco:" in buf: break
        if b"SSH-2.0-falso" in buf and b"eco:" not in buf:
            s.sendall(b"hola"); 
except Exception: pass
sys.stdout.buffer.write(buf)
PY

echo "WebSocket con la clave del ejemplo del RFC:"
R=$(python3 "$T/cli.py" 'GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n')
chequear "responde 101 Switching Protocols" "1" "$(printf '%s' "$R" | grep -c '^HTTP/1.1 101 Switching Protocols')"
chequear "Sec-WebSocket-Accept correcto (RFC 6455)" "1" "$(printf '%s' "$R" | grep -c '^Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=')"
chequear "lleva Upgrade y Connection" "2" "$(printf '%s' "$R" | grep -ciE '^(Upgrade: websocket|Connection: Upgrade)')"
chequear "una sola respuesta 101" "1" "$(printf '%s' "$R" | grep -c '^HTTP/1.1 101')"
chequear "pasa el túnel hasta el SSH" "1" "$(printf '%s' "$R" | grep -c 'SSH-2.0-falso')"
chequear "el SSH recibe y contesta" "1" "$(printf '%s' "$R" | grep -c 'eco:hola')"

echo "Sin Sec-WebSocket-Key (igual que PDirect v1):"
R=$(python3 "$T/cli.py" 'GET / HTTP/1.1\r\nHost: x\r\n\r\n')
chequear "dos respuestas 101 como v1" "2" "$(printf '%s' "$R" | grep -c '^HTTP/1.1 101')"
chequear "sin cabecera Sec-WebSocket-Accept" "0" "$(printf '%s' "$R" | grep -c 'Sec-WebSocket-Accept')"
chequear "igual pasa al SSH" "1" "$(printf '%s' "$R" | grep -c 'SSH-2.0-falso')"

echo "Clave en minúsculas de cabecera:"
R=$(python3 "$T/cli.py" 'GET / HTTP/1.1\r\nHost: x\r\nsec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
chequear "acepta la cabecera sin importar mayúsculas" "1" "$(printf '%s' "$R" | grep -c '^Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=')"

[ "$FALLOS" -eq 0 ] && echo "TODO OK" || { echo "$FALLOS fallo(s)"; exit 1; }
