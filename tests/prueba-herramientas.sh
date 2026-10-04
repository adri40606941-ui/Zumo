#!/bin/bash
# Prueba de las herramientas del panel (test de velocidad, liberar RAM, procesos).
# El test de velocidad se prueba contra un servidor HTTP local que imita a
# Cloudflare (/__down y /__up), así no gasta datos ni depende de internet.
#
# Uso: sudo bash tests/prueba-herramientas.sh   (necesita python3, curl y awk)
set -u
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-herr.XXXXXX)
FALLOS=0
SRV_PID=""
limpiar() { [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null; rm -rf "$T"; }
trap limpiar EXIT

N='\e[0m'; L='---'
extraer() { sed -n "/^$1() {/,/^}/p" "$AQUI/panel.sh"; }
for f in msg_ok msg_err test_velocidad liberar_ram procesos_top; do
	src=$(extraer "$f")
	[ -n "$src" ] || { echo "no encontré la función $f en panel.sh"; exit 1; }
	eval "$src"
done
banner() { :; }
pausa() { :; }
limpio() { sed 's/\x1b\[[0-9;]*m//g'; }
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }

# Servidor de mentira: /__down?bytes=N devuelve hasta 4 MB; /__up lee el cuerpo.
cat > "$T/srv.py" <<'PY'
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        n = 0
        if "bytes=" in self.path:
            n = min(int(self.path.split("bytes=")[1] or 0), 4_000_000)
        self.send_response(200); self.send_header("Content-Length", str(n)); self.end_headers()
        self.wfile.write(b"\0" * n)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); left = n
        while left > 0:
            chunk = self.rfile.read(min(65536, left))
            if not chunk: break
            left -= len(chunk)
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 "$T/srv.py" "$PORT" >/dev/null 2>&1 &
SRV_PID=$!
sleep 1

echo "1) Test de velocidad"
export ZUMO_SPEED_URL="http://127.0.0.1:$PORT"
SAL=$(test_velocidad </dev/null | limpio)
chequear "muestra la latencia en ms" "si" "$(grep -qE 'Latencia: +[0-9]+ ms' <<<"$SAL" && echo si || echo no)"
chequear "muestra la bajada en Mbps (> 0)" "si" "$(grep -E 'Bajada: +[0-9.]+ Mbps' <<<"$SAL" | awk '{print ($2>0)?"si":"no"}')"
chequear "muestra la subida en Mbps (> 0)" "si" "$(grep -E 'Subida: +[0-9.]+ Mbps' <<<"$SAL" | awk '{print ($2>0)?"si":"no"}')"

echo "2) Sin conexión avisa en vez de mostrar ceros"
export ZUMO_SPEED_URL="http://127.0.0.1:1"
SAL=$(test_velocidad </dev/null | limpio)
chequear "avisa que no se pudo medir" "si" "$(grep -q 'No se pudo medir' <<<"$SAL" && echo si || echo no)"
unset ZUMO_SPEED_URL

echo "3) Liberar RAM y limpiar"
SAL=$(liberar_ram </dev/null 2>&1 | limpio)
chequear "muestra RAM libre" "si" "$(grep -qE 'RAM libre: +[0-9]+ → [0-9]+ MB' <<<"$SAL" && echo si || echo no)"
chequear "muestra caché liberada (número, no negativo)" "si" "$(grep -qE 'Caché liberada: +[0-9]+ MB' <<<"$SAL" && echo si || echo no)"
chequear "muestra disco libre" "si" "$(grep -qE 'Disco libre: +[0-9]+ → [0-9]+ MB' <<<"$SAL" && echo si || echo no)"

echo "4) Procesos que más consumen"
SAL=$(procesos_top </dev/null | limpio)
chequear "lista por RAM" "si" "$(grep -q 'Más RAM' <<<"$SAL" && echo si || echo no)"
chequear "lista por CPU" "si" "$(grep -q 'Más CPU' <<<"$SAL" && echo si || echo no)"
chequear "trae 6 líneas de cada una (cabecera + 5)" "12" "$(grep -cE '^ +(PID|[0-9]+) ' <<<"$SAL")"

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
