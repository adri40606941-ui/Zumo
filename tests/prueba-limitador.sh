#!/bin/bash
# Prueba del limitador con sesiones sshd simuladas (procesos "sshd" de usuarios
# de prueba). Necesita root, gcc, useradd y setpriv. No toca /etc/zumo ni el
# sshd real: compila una copia del limitador apuntando a un directorio temporal.
#
# Uso: sudo bash tests/prueba-limitador.sh
set -u
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-test.XXXXXX)
FALLOS=0
PIDS=()

limpiar() {
	for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
	for u in ztest1 ztest2 ztest3 ztest4 ztest5 ztest6; do userdel "$u" 2>/dev/null; done
	rm -rf "$T"
}
trap limpiar EXIT

mkdir -p "$T/run"
gcc -O2 -Wall -Wextra \
	-DDB_PATH="\"$T/usuarios.db\"" -DCONF_PATH="\"$T/limit.conf\"" \
	-DTEMP_DB_PATH="\"$T/temporales.db\"" -DTEMP_SCRIPT="\"$T/borrar.sh\"" \
	-DRUN_DIR="\"$T/run\"" -DTOKEN_DB_PATH="\"$T/tokens.db\"" -DVENC_LOCK_PATH="\"$T/vencidos.lock\"" -DEXCESOS_PATH="\"$T/excesos.log\"" -o "$T/zumo-limit" "$AQUI/zumo-limit.c" || { echo "no compila"; exit 1; }

for u in ztest1 ztest2 ztest3 ztest4 ztest5 ztest6; do id "$u" >/dev/null 2>&1 || useradd -M -s /bin/false "$u"; done
for u in ztest1 ztest2 ztest3 ztest4 ztest5 ztest6; do echo "$u:Prueba1" | chpasswd; done   # con contraseña, como los reales
cp /bin/sleep "$T/sshd"   # proceso con nombre "sshd"

printf 'ztest1:1:2099-01-01\nztest2:2:2099-01-01\nztest3:1:2020-01-01\nztest4:1:2099-01-01\n' > "$T/usuarios.db"
printf 'ztest4:1\n' > "$T/temporales.db"
printf '#!/bin/bash\necho "$1" >> "%s/borrados.txt"\n' "$T" > "$T/borrar.sh"
chmod +x "$T/borrar.sh"

# Abre una "sesión" del usuario y deja su pid en $NUEVO_PID.
abrir() {
	setpriv --reuid="$1" --regid="$1" --clear-groups "$T/sshd" 600 >/dev/null 2>&1 </dev/null &
	NUEVO_PID=$!
	PIDS+=("$NUEVO_PID")
	disown "$NUEVO_PID" 2>/dev/null
}
vivo() { kill -0 "$1" 2>/dev/null && echo vivo || echo cortado; }
chequear() { # descripción, esperado, real
	if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi
}
estado() { local o=""; for p in "$@"; do o+="$(vivo "$p") "; done; echo "${o% }"; }

abrir ztest1; A1=$NUEVO_PID; sleep 0.3
abrir ztest1; A2=$NUEVO_PID; sleep 0.3
abrir ztest1; A3=$NUEVO_PID
abrir ztest2; B1=$NUEVO_PID; sleep 0.3
abrir ztest2; B2=$NUEVO_PID
abrir ztest3; C1=$NUEVO_PID
sleep 0.5

echo "1) --dry-run no corta nada"
timeout 20 "$T/zumo-limit" --once --dry-run 2>"$T/dry.log"
chequear "todas vivas" "vivo vivo vivo vivo vivo vivo" "$(estado $A1 $A2 $A3 $B1 $B2 $C1)"
chequear "informa lo que cortaría" "si" "$(grep -q 'dry-run.*ztest1' "$T/dry.log" && echo si || echo no)"

echo "2) KICK=newest: conserva la más vieja"
echo "KICK=newest" > "$T/limit.conf"
timeout 20 "$T/zumo-limit" --once 2>"$T/real.log"; sleep 0.3
chequear "ztest1 límite 1 (vieja, media, nueva)" "vivo cortado cortado" "$(estado $A1 $A2 $A3)"
chequear "ztest2 límite 2 (ambas se quedan)" "vivo vivo" "$(estado $B1 $B2)"
chequear "ztest3 vencido" "cortado" "$(estado $C1)"
chequear "el exceso de ztest1 queda anotado (2 intentos)" "2" "$(grep -c '^[0-9]*|ztest1|' "$T/excesos.log" 2>/dev/null)"
chequear "no anota al vencido ni al que está dentro del límite" "0" "$(grep -cE '\|ztest[23]\|' "$T/excesos.log" 2>/dev/null)"
chequear "el dry-run no anota nada (se vio antes: solo hay 2 líneas)" "2" "$(wc -l < "$T/excesos.log")"
chequear "el temporal vencido se borra; el vencido común NO (se puede renovar)" "ztest4" "$(sort "$T/borrados.txt" 2>/dev/null | uniq | tr '\n' ' ' | sed 's/ $//')"

echo "3) Una sesión nueva del mismo usuario se corta en la siguiente vuelta"
abrir ztest1; N1=$NUEVO_PID; sleep 0.3
timeout 20 "$T/zumo-limit" --once 2>/dev/null; sleep 0.3
chequear "ztest1 vieja sigue, nueva cortada" "vivo cortado" "$(estado $A1 $N1)"

echo "4) Sin limit.conf el valor por defecto es KICK=oldest: conserva la más nueva"
rm -f "$T/limit.conf"
abrir ztest1; N2=$NUEVO_PID; sleep 0.3
timeout 20 "$T/zumo-limit" --once 2>/dev/null; sleep 0.3
chequear "ztest1 por defecto (vieja cortada, nueva sigue)" "cortado vivo" "$(estado $A1 $N2)"

echo "5) GRACE=60: no corta una sesión extra recién abierta"
printf 'KICK=newest\nGRACE=60\n' > "$T/limit.conf"
abrir ztest1; N3=$NUEVO_PID; sleep 0.3
timeout 20 "$T/zumo-limit" --once 2>/dev/null; sleep 0.3
chequear "ztest1 las dos siguen" "vivo vivo" "$(estado $N2 $N3)"

echo "6) Un usuario que no está en la base no se toca"
printf 'ztest2:2:2099-01-01\n' > "$T/usuarios.db"
rm -f "$T/limit.conf"
timeout 20 "$T/zumo-limit" --once 2>/dev/null; sleep 0.3
chequear "ztest1 fuera de la base: sin cambios" "vivo vivo" "$(estado $N2 $N3)"

echo "7) Vence el día indicado a la hora de corte: se bloquea (y se desbloquea al renovar)"
HOY=$(date +%F); HORA=$(date +%-H)
est() { passwd -S "$1" 2>/dev/null | awk '{print $2}'; }
printf 'ztest5:1:%s\nztest6:1:%s\n' "$HOY" "$HOY" > "$T/usuarios.db"
: > "$T/temporales.db"; rm -f "$T/borrados.txt" "$T/vencidos.lock"
abrir ztest5; V1=$NUEVO_PID; abrir ztest6; V2=$NUEVO_PID; sleep 0.3
printf 'EXPIRE_HOUR=%s\n' "$HORA" > "$T/limit.conf"
timeout 20 "$T/zumo-limit" --once 2>/dev/null; sleep 0.3
chequear "pasada la hora de corte, se cortan las sesiones" "cortado cortado" "$(estado $V1 $V2)"
chequear "y las cuentas quedan bloqueadas" "L L" "$(est ztest5) $(est ztest6)"
chequear "no se borra nada" "" "$(cat "$T/borrados.txt" 2>/dev/null)"
chequear "quedan anotadas como bloqueadas por vencimiento" "ztest5 ztest6" "$(sort "$T/vencidos.lock" | tr '\n' ' ' | sed 's/ $//')"
echo "   renovar ztest5 (fecha futura)"
printf 'ztest5:1:2099-01-01\nztest6:1:%s\n' "$HOY" > "$T/usuarios.db"
timeout 20 "$T/zumo-limit" --once 2>/dev/null
chequear "ztest5 se desbloquea solo" "P" "$(est ztest5)"
chequear "ztest6 sigue bloqueado" "L" "$(est ztest6)"
chequear "la lista queda solo con ztest6" "ztest6" "$(cat "$T/vencidos.lock")"
echo "   un usuario bloqueado a mano no se desbloquea al renovar"
usermod -L ztest5
printf 'ztest5:1:%s\n' "$(date -d yesterday +%F)" > "$T/usuarios.db"
timeout 20 "$T/zumo-limit" --once 2>/dev/null
chequear "no se anota (lo bloqueó el administrador)" "0" "$(grep -c ztest5 "$T/vencidos.lock")"
printf 'ztest5:1:2099-01-01\n' > "$T/usuarios.db"
timeout 20 "$T/zumo-limit" --once 2>/dev/null
chequear "sigue bloqueado tras renovar" "L" "$(est ztest5)"
chequear "los que ya no están en la base salen de la lista" "0" "$(grep -c ztest6 "$T/vencidos.lock")"
usermod -U ztest5

echo "8) Antes de la hora de corte del mismo día no pasa nada"
if [ "$HORA" -lt 23 ]; then
printf 'ztest5:1:%s\n' "$HOY" > "$T/usuarios.db"
abrir ztest5; V3=$NUEVO_PID; sleep 0.3
printf 'EXPIRE_HOUR=%s\n' "$((HORA+1))" > "$T/limit.conf"
timeout 20 "$T/zumo-limit" --once 2>/dev/null; sleep 0.3
chequear "sigue conectado y sin bloquear" "vivo P" "$(estado $V3) $(est ztest5)"
else echo "  (se omite: son las 23 h)"; fi

echo "9) EXPIRE_DELETE=1 borra en vez de bloquear"
printf 'ztest5:1:%s\n' "$(date -d yesterday +%F)" > "$T/usuarios.db"
printf 'EXPIRE_HOUR=23\nEXPIRE_DELETE=1\n' > "$T/limit.conf"; rm -f "$T/borrados.txt" "$T/vencidos.lock"
timeout 20 "$T/zumo-limit" --once 2>/dev/null
chequear "se manda a borrar" "ztest5" "$(sort -u "$T/borrados.txt" 2>/dev/null)"
chequear "y no queda bloqueado" "P" "$(est ztest5)"

echo "10) Token por dispositivo: la cuenta con token solo acepta sesiones que lleguen con él"
printf 'ztest5:1:2099-01-01\nztest6:1:2099-01-01\n' > "$T/usuarios.db"
printf 'ztest5:A1B2C3D4E5F6\n' > "$T/tokens.db"
: > "$T/limit.conf"; rm -f "$T/vencidos.lock"
abrir ztest5; K1=$NUEVO_PID; abrir ztest6; K2=$NUEVO_PID; sleep 0.3
timeout 20 "$T/zumo-limit" --once --dry-run 2>"$T/tok.log"
chequear "dry-run no corta" "vivo vivo" "$(estado $K1 $K2)"
timeout 20 "$T/zumo-limit" --once 2>"$T/tok.log"; sleep 0.3
chequear "sesión sin token de la cuenta con token: cortada; cuenta sin token: sigue" "cortado vivo" "$(estado $K1 $K2)"
chequear "queda en el registro" "si" "$(grep -q 'ztest5.*sin-token' "$T/tok.log" && echo si || echo no)"

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
