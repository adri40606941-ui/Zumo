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
extraer() { sed -n "/^$1() {/,/^}$/p" "$AQUI/panel.sh"; }
for f in msg_ok msg_err _medir_velocidad test_velocidad liberar_ram desc_proceso _snap_cpu _col_pct procesos_top; do
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
DENY = len(sys.argv) > 2 and sys.argv[2] == "deny"
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        n = 0
        if "bytes=" in self.path:
            n = int(self.path.split("bytes=")[1] or 0)
        if DENY and n > 0 or n > 100_000_000:   # como el real: tope por pedido
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
        n = min(n, 4_000_000)
        self.send_response(200); self.send_header("Content-Length", str(n)); self.end_headers()
        self.wfile.write(b"\0" * n)
    def do_POST(self):
        if DENY:
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
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

echo "2b) Servidor que rechaza todo (403): avisa en vez de mostrar ceros"
PORT2=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 "$T/srv.py" "$PORT2" deny >/dev/null 2>&1 &
SRV2=$!; sleep 1
export ZUMO_SPEED_URL="http://127.0.0.1:$PORT2"
SAL=$(test_velocidad </dev/null | limpio)
kill "$SRV2" 2>/dev/null; unset ZUMO_SPEED_URL
chequear "avisa que no se pudo medir" "si" "$(grep -q 'No se pudo medir' <<<"$SAL" && echo si || echo no)"

echo "3) Liberar RAM y limpiar"
SAL=$(liberar_ram </dev/null 2>&1 | limpio)
chequear "muestra RAM libre" "si" "$(grep -qE 'RAM libre: +[0-9]+ → [0-9]+ MB' <<<"$SAL" && echo si || echo no)"
chequear "muestra caché liberada (número, no negativo)" "si" "$(grep -qE 'Caché liberada: +[0-9]+ MB' <<<"$SAL" && echo si || echo no)"
chequear "muestra disco libre" "si" "$(grep -qE 'Disco libre: +[0-9]+ → [0-9]+ MB' <<<"$SAL" && echo si || echo no)"

echo "4) Uso de CPU y RAM"
chequear "sshd se describe como SSH" "si" "$(desc_proceso sshd | grep -q SSH && echo si || echo no)"
chequear "badvpn-udpgw se describe como BadVPN" "si" "$(desc_proceso badvpn-udpgw | grep -q BadVPN && echo si || echo no)"
chequear "zumo-limit se describe como Limitador" "si" "$(desc_proceso zumo-limit | grep -q Limitador && echo si || echo no)"
chequear "un proceso desconocido muestra -" "-" "$(desc_proceso algo-raro)"
# un proceso que gasta CPU de verdad tiene que salir con % > 0 y subir el total
( timeout 6 sh -c 'while :; do :; done' ) >/dev/null 2>&1 &
BURN=$!
SAL=$(procesos_top </dev/null | limpio)
kill "$BURN" 2>/dev/null
chequear "lista por RAM" "si" "$(grep -q 'Más RAM' <<<"$SAL" && echo si || echo no)"
chequear "lista por CPU" "si" "$(grep -q 'Más CPU' <<<"$SAL" && echo si || echo no)"
chequear "tiene la columna DE QUÉ ES" "2" "$(grep -c 'DE QUÉ ES' <<<"$SAL")"
chequear "muestra el uso real de RAM en %" "si" "$(grep -qE 'RAM: +[0-9]+\.[0-9]+%' <<<"$SAL" && echo si || echo no)"
chequear "muestra el uso real de CPU en %" "si" "$(grep -qE 'CPU: +[0-9]+\.[0-9]+%' <<<"$SAL" && echo si || echo no)"
chequear "el uso real de CPU es mayor que 0 con un proceso trabajando" "si" "$(grep -E 'CPU: +[0-9.]+%' <<<"$SAL" | tail -1 | awk '{gsub("%","",$2); print ($2>0)?"si":"no"}')"
chequear "el proceso que gasta CPU sale arriba en la lista de CPU" "si" "$(sed -n '/Más CPU/,$p' <<<"$SAL" | grep -qE '^ +[0-9]+ sh +[0-9.]+' && echo si || echo no)"

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
