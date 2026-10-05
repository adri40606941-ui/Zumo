#!/bin/bash
# Prueba de respaldo/restauración: crea usuarios con contraseña, un HWID y un
# temporal, hace el respaldo, borra todo, restaura y compara. También prueba
# el servidor de un solo uso (IP y puerto) y la clave incorrecta.
# Uso: sudo bash tests/prueba-respaldo.sh   (necesita root, openssl, python3, curl)
set -u
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-resp.XXXXXX); chmod 755 "$T"; FALLOS=0
USERS="zrpedro zrana HWIDRESP0001 zrtemp"
limpiar() { for u in $USERS; do userdel "$u" 2>/dev/null; done; rm -rf "$T"; }
trap limpiar EXIT
export ZUMO_DB="$T/usuarios.db" ZUMO_LOCK="$T/lock" ZUMO_DATOS="$T/datos.db" ZUMO_HIST="$T/hist.db" ZUMO_LIMCONF="$T/limit.conf" ZUMO_RESP_DIR="$T/resp"
DB="$ZUMO_DB"; TEMPDB="$T/temporales.db"; N='\e[0m'; L='---'; CLAVES="$T/claves.db"
source "$AQUI/zumo-lib.sh"
extraer() { sed -n "/^$1() {/,/^}$/p" "$AQUI/panel.sh"; }
for f in msg_ok msg_err fecha_cuenta _merge_por_usuario _respaldo_crear _respaldo_restaurar _respaldo_servidor_py _respaldos_lista; do
	src=$(extraer "$f"); [ -n "$src" ] || { echo "no encontré $f"; exit 1; }; eval "$src"
done
RESP_DIR="$ZUMO_RESP_DIR"
programar_borrado_temp() { echo "PROGRAMADO $1 $2" >> "$T/prog.log"; }
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }
hash_de() { getent shadow "$1" | cut -d: -f2; }
existe() { id "$1" >/dev/null 2>&1 && echo si || echo no; }

useradd -M -s /bin/false -e 2099-01-01 zrpedro; echo "zrpedro:clave123" | chpasswd
useradd -M -s /bin/false -e 2099-02-02 zrana; echo "zrana:otra456" | chpasswd; usermod -L zrana
useradd --badname -M -s /bin/false -e 2099-03-03 -c "hwid,Carlos" HWIDRESP0001; echo "HWIDRESP0001:HWIDRESP0001" | chpasswd
useradd -M -s /bin/false -e 2099-01-01 zrtemp; echo "zrtemp:tmp789" | chpasswd
printf 'zrpedro:2:2099-01-01\nzrana:1:2099-02-02\nHWIDRESP0001:1:2099-03-03\nzrtemp:1:2099-01-01\n' > "$DB"
EP=$(( $(date +%s) + 3000 )); printf 'zrtemp:%s\n' "$EP" > "$TEMPDB"
printf 'zrpedro:5000\nzrana:700\n' > "$ZUMO_DATOS"
printf '2026-10-01:zrpedro:3000\n2026-10-02:zrpedro:2000\n2026-10-02:zrana:700\n' > "$ZUMO_HIST"
echo "KICK=oldest" > "$ZUMO_LIMCONF"
H1=$(hash_de zrpedro); H2=$(hash_de zrana); H3=$(hash_de HWIDRESP0001)

echo "1) Crear respaldo"
ARCH="$T/resp/prueba.zbk"
_respaldo_crear "$ARCH" "Clave1234"; chequear "se creó el archivo" "si" "$([ -s "$ARCH" ] && echo si || echo no)"
chequear "4 cuentas guardadas" "4" "$BK_CUENTAS"
chequear "el archivo está cifrado (no se lee el usuario)" "0" "$(grep -c zrpedro "$ARCH")"

echo "2) Clave incorrecta no restaura nada"
_respaldo_restaurar "$ARCH" "mala"; chequear "falla con clave mala" "1" "$?"

echo "3) Servidor de un solo uso (IP y puerto) + descarga"
_respaldo_servidor_py > "$T/srv.py"
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 "$T/srv.py" "$ARCH" "$PORT" > "$T/srv.out" 2>&1 & SP=$!; sleep 1
chequear "ruta distinta da 404" "404" "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/otra")"
curl -fsS -o "$T/bajado.zbk" "http://127.0.0.1:$PORT/respaldo"
chequear "lo descargado es igual al respaldo" "si" "$(cmp -s "$ARCH" "$T/bajado.zbk" && echo si || echo no)"
sleep 1
chequear "el servidor se cierra tras 1 descarga" "no" "$(kill -0 $SP 2>/dev/null && echo si || echo no)"
chequear "avisa DESCARGADO" "si" "$(grep -q DESCARGADO "$T/srv.out" && echo si || echo no)"

echo "4) Borro todo y restauro (como una VPS nueva)"
for u in $USERS; do userdel "$u" 2>/dev/null; done
: > "$DB"; rm -f "$TEMPDB" "$ZUMO_DATOS" "$ZUMO_HIST" "$ZUMO_LIMCONF"
_respaldo_restaurar "$T/bajado.zbk" "Clave1234"; chequear "restaura con la clave buena" "0" "$?"
chequear "usuarios creados" "4" "$RS_NUEVOS"
for u in zrpedro zrana HWIDRESP0001 zrtemp; do chequear "existe $u" "si" "$(existe $u)"; done
chequear "misma contraseña zrpedro" "$H1" "$(hash_de zrpedro)"
chequear "zrana sigue bloqueada (mismo hash)" "$H2" "$(hash_de zrana)"
chequear "misma contraseña HWID" "$H3" "$(hash_de HWIDRESP0001)"
chequear "etiqueta del HWID" "hwid,Carlos" "$(getent passwd HWIDRESP0001 | cut -d: -f5)"
chequear "la cuenta de Linux vence un día después (chage)" "si" "$(chage -l zrpedro | grep -q 'Jan 02, 2099' && echo si || echo no)"
chequear "base: límite y vencimiento" "zrpedro:2:2099-01-01" "$(grep '^zrpedro:' "$DB")"
chequear "base: 4 filas" "4" "$(grep -c : "$DB")"
chequear "datos.db vuelve" "zrpedro:5000" "$(grep '^zrpedro:' "$ZUMO_DATOS")"
chequear "historial vuelve (3 filas)" "3" "$(grep -c : "$ZUMO_HIST")"
chequear "limit.conf vuelve" "KICK=oldest" "$(cat "$ZUMO_LIMCONF")"
chequear "el temporal se reprograma con minutos que quedan" "si" "$(awk '/PROGRAMADO zrtemp/{print ($3>=48 && $3<=50)?"si":"no"}' "$T/prog.log")"

echo "5) Restaurar encima: lo que ya existe se deja"
echo "zrpedro:5000999" > /dev/null
_respaldo_restaurar "$T/bajado.zbk" "Clave1234"
chequear "0 creados, 4 existentes" "0/4" "$RS_NUEVOS/$RS_EXISTENTES"
chequear "base sin duplicados" "4" "$(grep -c : "$DB")"
chequear "datos sin duplicados" "2" "$(grep -c : "$ZUMO_DATOS")"

echo "6) Un temporal ya vencido no se recrea"
for u in $USERS; do userdel "$u" 2>/dev/null; done
useradd -M -s /bin/false -e 2099-01-01 zrtemp; echo "zrtemp:x1" | chpasswd
printf 'zrtemp:1:2099-01-01\n' > "$DB"
printf 'zrtemp:%s\n' "$(( $(date +%s) - 100 ))" > "$TEMPDB"
_respaldo_crear "$T/resp/venc.zbk" "Clave1234"
for u in $USERS; do userdel "$u" 2>/dev/null; done; : > "$DB"
_respaldo_restaurar "$T/resp/venc.zbk" "Clave1234" 2>/dev/null
chequear "el temporal vencido no se creó" "no" "$(existe zrtemp)"
chequear "se cuenta como vencido" "1" "$RS_VENCIDOS"
echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
