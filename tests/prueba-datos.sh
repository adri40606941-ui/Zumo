#!/bin/bash
# Prueba del contador de datos (zumo-datos.sh): procesos "sshd" de mentira
# (una copia de cat con ese nombre, corriendo como un usuario de prueba) mueven
# datos y se mira que se sumen bien (bajada + subida, sin contar doble).
# Uso: sudo bash tests/prueba-datos.sh   (necesita root y setpriv)
set -u
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-datos.XXXXXX); chmod 755 "$T"; FALLOS=0; PIDS=""
limpiar() { kill $PIDS 2>/dev/null; exec 7>&- 8>&- 2>/dev/null; userdel zdt1 2>/dev/null; userdel zdt2 2>/dev/null; rm -rf "$T"; }
trap limpiar EXIT
export ZUMO_DB="$T/db" ZUMO_HIST="$T/hist" ZUMO_DATOS="$T/datos" ZUMO_DATOS_LOCK="$T/lock" INTERVAL=1
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }
useradd -M -s /bin/false zdt1; useradd -M -s /bin/false zdt2
printf 'zdt1:1:2099-01-01\nzdt2:1:2099-01-01\n' > "$ZUMO_DB"
cp /bin/cat "$T/sshd"
datos() { awk -F: -v u="$1" '$1==u{print $2}' "$ZUMO_DATOS" 2>/dev/null; }
rango() { [ "${1:-0}" -ge "$2" ] && [ "${1:-0}" -le "$3" ] && echo si || echo no; }

# Una sesión de mentira ya abierta antes de arrancar el contador: no se cuenta lo viejo.
mkfifo "$T/f0"; exec 6<>"$T/f0"
setpriv --reuid=zdt2 --regid=zdt2 --clear-groups "$T/sshd" < "$T/f0" > /dev/null & PIDS="$!"
head -c 2000000 /dev/zero >&6; sleep 0.5

bash "$AQUI/scripts/zumo-datos.sh" >/dev/null 2>&1 & PIDS="$PIDS $!"
sleep 2

echo "1) Una sesión nueva mueve 3 MB (leídos y escritos por sshd)"
mkfifo "$T/f1"; exec 7<>"$T/f1"
setpriv --reuid=zdt1 --regid=zdt1 --clear-groups "$T/sshd" < "$T/f1" > /dev/null & PIDS="$PIDS $!"
sleep 1.2
head -c 3000000 /dev/zero >&7
sleep 3
chequear "zdt1 suma ~3 MB (no el doble)" "si" "$(rango "$(datos zdt1)" 2900000 3200000)"
chequear "lo que ya tenía la sesión vieja de zdt2 no se cuenta" "" "$(datos zdt2)"

echo "2) Sigue sumando"
head -c 2000000 /dev/zero >&7
sleep 3
chequear "zdt1 ~5 MB" "si" "$(rango "$(datos zdt1)" 4900000 5300000)"
V=$(datos zdt1); sleep 3
chequear "sin tráfico nuevo no cambia" "$V" "$(datos zdt1)"

echo "3) Si la sesión se cierra, no se pierde lo ya contado"
exec 7>&-; sleep 3
chequear "zdt1 sigue ~5 MB" "si" "$(rango "$(datos zdt1)" 4900000 5300000)"

HOY=$(date +%F)
chequear "el historial tiene la fila de hoy (~5 MB)" "si" "$(rango "$(awk -F: -v h="$HOY" '$1==h && $2=="zdt1"{print $3}' "$T/hist")" 4900000 5300000)"

echo "4) Un usuario fuera de la base se limpia (se prueba con 15 ciclos)"
printf 'zdt2:1:2099-01-01\n' > "$ZUMO_DB"
sleep 17
chequear "zdt1 fuera de datos.db" "" "$(datos zdt1)"
chequear "zdt1 fuera del historial" "0" "$(grep -c ':zdt1:' "$T/hist")"
echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
