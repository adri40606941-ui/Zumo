#!/bin/bash
# Prueba del contador de datos (zumo-datos.sh) con iptables de verdad:
# un usuario de prueba descarga/sube datos contra un servidor local y se mira
# que se sumen, que no se dupliquen reglas y que borrar al usuario limpie todo.
# Uso: sudo bash tests/prueba-datos.sh   (necesita root, iptables, curl, python3)
set -u
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-datos.XXXXXX); FALLOS=0; SP=""; chmod 755 "$T"
limpiar() {
	[ -n "$SP" ] && kill "$SP" 2>/dev/null
	iptables -w -D OUTPUT -j ZUMO_DATOS 2>/dev/null; iptables -w -F ZUMO_DATOS 2>/dev/null; iptables -w -X ZUMO_DATOS 2>/dev/null
	userdel zdt1 2>/dev/null; userdel zdt2 2>/dev/null; rm -rf "$T"
}
trap limpiar EXIT
export ZUMO_DB="$T/db" ZUMO_DATOS="$T/datos" ZUMO_DATOS_LOCK="$T/lock"
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }
useradd -M -s /bin/false zdt1; useradd -M -s /bin/false zdt2
printf 'zdt1:1:2099-01-01\nzdt2:1:2099-01-01\n' > "$ZUMO_DB"
cat > "$T/srv.py" <<'PY'
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def log_message(s,*a): pass
    def do_POST(s):
        n=int(s.headers.get("Content-Length",0)); l=n
        while l>0:
            c=s.rfile.read(min(65536,l))
            if not c: break
            l-=len(c)
        s.send_response(200); s.send_header("Content-Length","0"); s.end_headers()
http.server.ThreadingHTTPServer(("127.0.0.1",8766),H).serve_forever()
PY
python3 "$T/srv.py" >/dev/null 2>&1 & SP=$!; sleep 1
como() { setpriv --reuid="$1" --regid="$1" --clear-groups "${@:2}"; }
datos() { awk -F: -v u="$1" '$1==u{print $2}' "$ZUMO_DATOS" 2>/dev/null; }

echo "1) Cuenta lo que sube un usuario"
head -c 3000000 /dev/zero > "$T/p"; chmod 644 "$T/p"
bash "$AQUI/zumo-datos.sh" --once
como zdt1 curl -s -X POST --data-binary @"$T/p" http://127.0.0.1:8766/ -o /dev/null
bash "$AQUI/zumo-datos.sh" --once
V=$(datos zdt1)
chequear "zdt1 suma ~3 MB" "si" "$([ "${V:-0}" -ge 3000000 ] && [ "${V:-0}" -lt 3300000 ] && echo si || echo no)"
chequear "zdt2 (sin tráfico) no suma" "" "$(datos zdt2)"

echo "2) Se acumula y no duplica reglas"
como zdt1 curl -s -X POST --data-binary @"$T/p" http://127.0.0.1:8766/ -o /dev/null
bash "$AQUI/zumo-datos.sh" --once; bash "$AQUI/zumo-datos.sh" --once
V2=$(datos zdt1)
chequear "ahora ~6 MB" "si" "$([ "${V2:-0}" -ge 6000000 ] && [ "${V2:-0}" -lt 6600000 ] && echo si || echo no)"
chequear "2 reglas (una por usuario)" "2" "$(iptables -w -S ZUMO_DATOS | grep -c -- '-A ZUMO_DATOS')"
bash "$AQUI/zumo-datos.sh" --once
chequear "sin tráfico nuevo no cambia el total" "$V2" "$(datos zdt1)"

echo "3) Un usuario borrado de la base se limpia"
printf 'zdt2:1:2099-01-01\n' > "$ZUMO_DB"
bash "$AQUI/zumo-datos.sh" --once
chequear "zdt1 fuera de datos.db" "" "$(datos zdt1)"
chequear "queda 1 regla" "1" "$(iptables -w -S ZUMO_DATOS | grep -c -- '-A ZUMO_DATOS')"
echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
