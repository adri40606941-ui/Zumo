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
for f in _red_iface _red_bytes _red_calc _red_fmt_bytes _red_capacidad control_red mensaje_renovacion clave_get db_orden op msg_ok msg_err _medir_velocidad test_velocidad liberar_ram desc_proceso _snap_cpu _col_pct procesos_top _fmt_bytes uso_datos datos_de datos_reset datos_rename hora_vps puertos_activos mensaje_cliente mensaje_comun mensaje_hwid es_hwid hora_corte vencido_ya fecha_cuenta vencidos bhttp_port hcr_port dias es_temporal temp_restante; do
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

echo "5) Uso de datos"
etiqueta_de() { echo "$1"; }
export ZUMO_DB="$T/usuarios.db"; ZUMO_LOCK="$T/lock"
# shellcheck source=/dev/null
source "$AQUI/zumo-lib.sh"
DB="$T/usuarios.db"; printf 'ana:1:2099-01-01\nbeto:1:2099-01-01\nnuevo:1:2099-01-01\n' > "$DB"
export ZUMO_DATOS="$T/datos.db" ZUMO_HIST="$T/hist.db"
printf 'ana:1610612736\nbeto:5242880\n' > "$ZUMO_DATOS"
HOY=$(date +%F); MES=${HOY:0:7}
printf '%s:ana:1048576\n%s-01:ana:2097152\n%s:beto:5242880\n2020-01-05:ana:9999999\n' "$HOY" "$MES" "$HOY" > "$ZUMO_HIST"
SAL=$(uso_datos </dev/null | limpio)
chequear "ana: hoy 1.0 MB, total 1.50 GB" "si" "$(grep -qE 'ana +1\.0 MB +1\.50 GB' <<<"$SAL" && echo si || echo no)"
chequear "beto: hoy 5.0 MB, total 5.0 MB" "si" "$(grep -qE 'beto +5\.0 MB +5\.0 MB' <<<"$SAL" && echo si || echo no)"
chequear "usuario sin datos en 0" "si" "$(grep -qE 'nuevo +0\.0 MB +0\.0 MB' <<<"$SAL" && echo si || echo no)"
chequear "total de los 3 usuarios" "si" "$(grep -qE 'TOTAL\(3\) +6\.0 MB +1\.50 GB' <<<"$SAL" && echo si || echo no)"

echo "6) Renovar pone el contador en 0; cambiar HWID lo traslada"
export ZUMO_DATOS_LOCK="$T/lock"
datos_rename ana ana2
chequear "rename traslada los datos" "1610612736" "$(datos_de ana2)"
datos_reset ana2
chequear "renovar deja en 0" "" "$(datos_de ana2)"
chequear "los demás no se tocan" "5242880" "$(datos_de beto)"
unset ZUMO_DATOS ZUMO_HIST

echo "7) Hora de la VPS y datos del cliente al crear un usuario"
chequear "hora VPS con día y hora" "si" "$(hora_vps | grep -qE '^(Dom|Lun|Mar|Mié|Jue|Vie|Sáb) [0-9]{2}/[0-9]{2}/[0-9]{4} +[0-9]{2}:[0-9]{2}:[0-9]{2}' && echo si || echo no)"
ip_publica() { echo "1.2.3.4"; }
puertos_activos() { echo "SSH 22 · WebSocket 80"; }
SAL=$(mensaje_cliente ana clave99 "30 días" | limpio)
chequear "mensaje: servidor" "si" "$(grep -q 'Servidor: *1.2.3.4' <<<"$SAL" && echo si || echo no)"
chequear "mensaje: usuario y contraseña" "si" "$(grep -q 'Usuario: *ana' <<<"$SAL" && grep -q 'Contraseña: *clave99' <<<"$SAL" && echo si || echo no)"
chequear "mensaje: vence y puertos" "si" "$(grep -q 'Vence: *30 días' <<<"$SAL" && grep -q 'Puertos: *SSH 22' <<<"$SAL" && echo si || echo no)"
SAL=$(mensaje_cliente ana "" "30 días" | limpio)
chequear "mensaje sin clave avisa" "si" "$(grep -q 'la que le diste' <<<"$SAL" && echo si || echo no)"
echo "8) Mensaje corto para usuarios comunes"
printf 'PDIRECT_BANNER=MiBanner\nPDIRECT_COLOR=yellow\n' > "$T/pd.env"; export ZUMO_PDIRECT_ENV="$T/pd.env"
printf 'ana:2:2099-01-01\nbeto:1:2099-01-01\n' > "$DB"
SAL=$(mensaje_comun ana clave99 "22/08" | limpio)
chequear "usuario" "si" "$(grep -q '👤 ana' <<<"$SAL" && echo si || echo no)"
chequear "contraseña" "si" "$(grep -q '🔒 clave99' <<<"$SAL" && echo si || echo no)"
chequear "fecha" "si" "$(grep -q '📅 22/08' <<<"$SAL" && echo si || echo no)"
chequear "2 dispositivos" "si" "$(grep -q '🔌 2 dispositivos' <<<"$SAL" && echo si || echo no)"
chequear "banner del 101" "si" "$(grep -q '📄 MiBanner' <<<"$SAL" && echo si || echo no)"
SAL=$(mensaje_comun beto x "01/01" | limpio)
chequear "1 dispositivo (singular)" "si" "$(grep -q '🔌 1 dispositivo$' <<<"$SAL" && echo si || echo no)"
export ZUMO_PDIRECT_ENV="$T/no-existe"
chequear "sin config usa ZUMO" "si" "$(mensaje_comun beto x "01/01" | limpio | grep -q '📄 ZUMO' && echo si || echo no)"
unset ZUMO_PDIRECT_ENV
printf 'PDIRECT_BANNER=MiBanner\n' > "$T/pd.env"; export ZUMO_PDIRECT_ENV="$T/pd.env"
etiqueta_de() { [ "$1" = "HWIDX1234" ] && echo "lucrecia" || echo "$1"; }
SAL=$(mensaje_hwid HWIDX1234 "20/08/2026" | limpio)
chequear "hwid: título" "si" "$(grep -q '🔐 DATOS DE ACCESO' <<<"$SAL" && echo si || echo no)"
chequear "hwid: plan" "si" "$(grep -q '├ ☁️ Plan: Privado' <<<"$SAL" && echo si || echo no)"
chequear "hwid: máquina = banner del 101" "si" "$(grep -q '├ ⚙️ Máquina: MiBanner' <<<"$SAL" && echo si || echo no)"
chequear "hwid: usuario = nombre del cliente" "si" "$(grep -q '├ 👤 Usuario: lucrecia' <<<"$SAL" && echo si || echo no)"
chequear "hwid: vence" "si" "$(grep -q '├ ⏳ Vence: 20/08/2026' <<<"$SAL" && echo si || echo no)"
SAL=$(mensaje_hwid HWIDX1234 "10 minutos" | limpio)
chequear "hwid temporal: vence en minutos" "si" "$(grep -q 'Vence: 10 minutos' <<<"$SAL" && echo si || echo no)"
unset ZUMO_PDIRECT_ENV

echo "9) Vencimiento a las 21:00"
export ZUMO_LIMCONF="$T/lc.conf"
chequear "sin configuración la hora de corte es 21" "21" "$(rm -f "$ZUMO_LIMCONF"; hora_corte)"
echo "EXPIRE_HOUR=0" > "$ZUMO_LIMCONF"
chequear "hoy con corte a las 0 h: vencido" "vencido" "$(dias "$(date +%F)")"
chequear "ayer: vencido" "vencido" "$(dias "$(date -d yesterday +%F)")"
if [ "$(date +%-H)" -lt 23 ]; then
echo "EXPIRE_HOUR=23" > "$ZUMO_LIMCONF"
chequear "hoy antes de la hora de corte: vence hoy" "vence hoy" "$(dias "$(date +%F)")"
fi
chequear "mañana: vence 1 día" "vence 1 día" "$(dias "$(date -d tomorrow +%F)")"
chequear "la cuenta de Linux vence un día después" "2026-10-25" "$(fecha_cuenta 2026-10-24)"
unset ZUMO_LIMCONF

echo "10) Usuarios vencidos: renovar"
etiqueta_de() { echo "$1"; }
DB="$T/usuarios.db"; printf 'viejo1:1:2020-01-01\nvigente:1:2099-01-01\nviejo2:1:2021-05-05\n' > "$DB"
export ZUMO_LIMCONF="$T/lc2.conf"; echo "EXPIRE_HOUR=21" > "$ZUMO_LIMCONF"
usermod() { echo "usermod $*" >> "$T/usermod.log"; }
SAL=$(printf '0\n' | vencidos | limpio)
chequear "lista numerada con los vencidos" "si" "$(grep -q '\[1\] viejo1' <<<"$SAL" && grep -q '\[2\] viejo2' <<<"$SAL" && echo si || echo no)"
chequear "el vigente no aparece" "no" "$(grep -q vigente <<<"$SAL" && echo si || echo no)"
CLAVES="$T/claves.db"; echo 'viejo2:pw2' > "$CLAVES"
SALR=$(printf '2\n2\n30\n\n' | vencidos | limpio)
NUEVA=$(date -d "+30 days" +%F)
chequear "al renovar muestra el mensaje del cliente con usuario y clave" "si" "$(grep -q '👤 viejo2' <<<"$SALR" && grep -q '🔒 pw2' <<<"$SALR" && echo si || echo no)"
chequear "y con la nueva fecha" "si" "$(grep -q "📅 $(date -d "$NUEVA" +%d/%m)" <<<"$SALR" && echo si || echo no)"
chequear "renovar #2 cambia su vencimiento en la base" "viejo2:1:$NUEVA" "$(grep '^viejo2:' "$DB")"
chequear "y deja viejo1 como estaba" "viejo1:1:2020-01-01" "$(grep '^viejo1:' "$DB")"
chequear "y la cuenta de Linux vence un día después" "usermod -e $(date -d "$NUEVA +1 day" +%F) viejo2" "$(tail -1 "$T/usermod.log")"
unset ZUMO_LIMCONF
unset -f usermod

echo "5) Usuario compartido"
for f in usuario_compartido etiqueta_de es_hwid; do src=$(extraer "$f"); eval "$src"; done
export ZUMO_EXCESOS="$T/excesos.log"
: > "$DB"; printf 'ana:1:2030-01-01\nbob:2:2030-01-01\n' > "$DB"
SAL=$(usuario_compartido </dev/null | limpio)
chequear "sin intentos lo dice" "si" "$(grep -q 'Todavía no hubo' <<<"$SAL" && echo si || echo no)"
NOW=$(date +%s)
printf '%s|ana|1.1.1.1|2|1\n%s|ana|2.2.2.2|2|1\n%s|ana|1.1.1.1|2|1\n%s|bob|3.3.3.3|3|2\n%s|fantasma|9.9.9.9|2|1\n' "$NOW" "$NOW" "$NOW" "$NOW" "$NOW" > "$ZUMO_EXCESOS"
SAL=$(usuario_compartido </dev/null | limpio)
chequear "ana aparece primero con 3 intentos" "si" "$(grep -q '\[1\] ana.*3 intentos' <<<"$SAL" && echo si || echo no)"
chequear "cuenta 2 IPs distintas para ana" "si" "$(grep -q 'IPs: 2' <<<"$SAL" && echo si || echo no)"
chequear "bob con 1 intento (singular)" "si" "$(grep -q '\[2\] bob.*1 intento$' <<<"$SAL" && echo si || echo no)"
chequear "no muestra usuarios que ya no existen" "no" "$(grep -q fantasma <<<"$SAL" && echo si || echo no)"
printf 'vs\n' | usuario_compartido >/dev/null 2>&1
chequear "vaciar historial borra el archivo" "no" "$([ -f "$ZUMO_EXCESOS" ] && echo si || echo no)"
unset ZUMO_EXCESOS

echo "6) Control de red"
ND="$T/netdev"; export ZUMO_NETDEV="$ND"
cat > "$ND" <<'EOT'
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  500  5 0 0 0 0 0 0  500  5 0 0 0 0 0 0
  eth0: 1073741824 100 0 0 0 0 0 0 2097152 50 0 0 0 0 0 0
EOT
chequear "lee bytes de la interfaz" "1073741824 2097152" "$(_red_bytes eth0)"
chequear "no confunde lo con otra interfaz" "500 500" "$(_red_bytes lo)"
chequear "calcula Mbps (12,5 MB en 1 s = 100 Mbps)" "100.0 8.0" "$(_red_calc 0 0 12500000 1000000 1)"
chequear "contador que baja no da negativo" "0.0 0.0" "$(_red_calc 100 100 50 50 1)"
chequear "formato GB" "1.00 GB" "$(_red_fmt_bytes 1073741824)"
chequear "formato MB" "2.0 MB" "$(_red_fmt_bytes 2097152)"
chequear "capacidad desde variable" "1000" "$(ZUMO_LINK_MBPS=1000 _red_capacidad eth0)"
chequear "capacidad inválida se ignora" "" "$(ZUMO_LINK_MBPS=-1 _red_capacidad nope)"
banner() { :; }
_red_iface() { echo eth0; }
SAL=$( (sleep 2.5; echo q) | ZUMO_LINK_MBPS=1000 control_red 2>&1 | limpio)
chequear "muestra el título y la interfaz" "si" "$(grep -q 'CONTROL DE RED  *(eth0)' <<<"$SAL" && echo si || echo no)"
chequear "muestra bajada y subida" "si" "$(grep -q 'Bajada: .* Mbps' <<<"$SAL" && grep -q 'Subida: .* Mbps' <<<"$SAL" && echo si || echo no)"
chequear "muestra el total desde el arranque" "si" "$(grep -q '1.00 GB' <<<"$SAL" && echo si || echo no)"
chequear "muestra uso del enlace con capacidad" "si" "$(grep -q 'Uso del enlace (1000 Mbps)' <<<"$SAL" && echo si || echo no)"
chequear "sale sola si no hay teclado (EOF)" "ok" "$(timeout 10 bash -c 'true'; ( control_red </dev/null >/dev/null 2>&1 & p=$!; for i in 1 2 3 4 5 6; do sleep 0.5; kill -0 $p 2>/dev/null || { echo ok; exit; }; done; kill $p 2>/dev/null; echo cuelga ))"
unset ZUMO_NETDEV

echo "7) Usuarios HWID en línea y límite 2"
chequear "Ver usuarios lee nombres largos (HWID de 32) sin recortar" "si" "$(grep -q 'ps -eo user:32=,comm=,etimes=' "$AQUI/panel.sh" && echo si || echo no)"
chequear "HWID nuevo: límite por defecto 2" "si" "$(sed -n '/Pegá el token del celular del cliente/,/zumo_db_add "\$hwid" "\$lim"/p' "$AQUI/panel.sh" | grep -q 'lim=\${lim:-2}' && echo si || echo no)"
chequear "actualizar pasa HWID de límite 1 a 2 una sola vez" "si" "$(grep -q 'hwid-limite2' "$AQUI/actualizar.sh" && echo si || echo no)"

echo "8) Ficha HWID sin límite a la vista"
for f in _ficha_usuario _fmt_secs dias; do src=$(extraer "$f"); eval "$src"; done
N=$'\e[0m'; export LC_ALL=C.UTF-8
declare -gA _ON=() _TS=() _GE=() _CL=() _TM=() _DC=(); _PRE=1; _SEP="---"
_GE[hwidaaaa1111]="cliente1"; _ON[hwidaaaa1111]=1; _TS[hwidaaaa1111]=300
_CL[normal]="clave1"; _ON[normal]=1; _TS[normal]=300
SAL=$(_ficha_usuario 1 hwidaaaa1111 2 2030-01-01 | limpio)
chequear "HWID muestra vencimiento y tiempo conectado" "si" "$(grep -q 'Vence: 01/01/2030 *5m' <<<"$SAL" && echo si || echo no)"
chequear "HWID no muestra el límite" "no" "$(grep -q 'Límite' <<<"$SAL" && echo si || echo no)"
SAL=$(_ficha_usuario 2 normal 1 2030-01-01 | limpio)
chequear "usuario común sigue mostrando Límite 1/1" "si" "$(grep -q 'Límite: 1/1' <<<"$SAL" && echo si || echo no)"
unset _PRE

echo "9) Fail2ban: jail, tiempo de baneo y lista de IP"
export ZUMO_F2B_JAIL="$T/jail.local"
for f in f2b_escribir_jail f2b_bantime f2b_baneadas; do src=$(extraer "$f"); eval "$src"; done
F2B_JAIL="$ZUMO_F2B_JAIL"
f2b_escribir_jail "12h"
chequear "escribe el tiempo de baneo elegido" "12h" "$(f2b_bantime)"
chequear "ignora 127.0.0.1 (clientes por payload)" "si" "$(grep -q '^ignoreip = 127.0.0.1/8' "$F2B_JAIL" && echo si || echo no)"
chequear "banea tras 5 intentos" "si" "$(grep -q '^maxretry = 5' "$F2B_JAIL" && echo si || echo no)"
f2b_escribir_jail
chequear "sin argumento, el baneo por defecto es 1h" "1h" "$(f2b_bantime)"
# fail2ban-client de mentira para probar el parseo de la lista
fail2ban-client() { echo "Status for the jail: sshd"; echo "|- Banned IP list:	1.2.3.4 5.6.7.8"; }
chequear "lee las IP baneadas del estado" "1.2.3.4 5.6.7.8" "$(f2b_baneadas)"
chequear "cuenta cuántas hay baneadas" "2" "$(echo $(f2b_baneadas) | wc -w)"
unset -f fail2ban-client

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
