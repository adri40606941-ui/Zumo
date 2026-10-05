#!/bin/bash
# Prueba de "Eliminar usuario" del panel: extrae las funciones de panel.sh y las
# ejecuta con una base temporal y usuarios de prueba. Escribe lo que escribiría
# la persona (nombre o número) y mira qué se borró.
#
# Uso: sudo bash tests/prueba-borrar.sh   (necesita root y useradd)
set -u
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-borrar.XXXXXX)
FALLOS=0
USERS="zbpedro zbjuan zbana zbn1 zbn2 ZbMayus"

limpiar() { for u in $USERS; do userdel "$u" 2>/dev/null; done; rm -rf "$T"; }
trap limpiar EXIT

DB="$T/usuarios.db"; TEMPDB="$T/temporales.db"
export ZUMO_DB="$DB" ZUMO_LOCK="$T/lock" ZUMO_CLAVES="$T/claves.db" ZUMO_TOKENS="$T/tokens.db"
CLAVES="$ZUMO_CLAVES"; TOKENS="$ZUMO_TOKENS"
N='\e[0m'; L='---'
# shellcheck source=/dev/null
source "$AQUI/zumo-lib.sh"

# Funciones reales de panel.sh (se copian tal cual, sin ejecutar el menú).
extraer() { sed -n "/^$1() {/,/^}/p" "$AQUI/panel.sh"; }
for f in db_orden etiqueta_de clave_del token_del en_linea msg_ok msg_err lista_para_borrar buscar_usuario \
	borrar_usuario_completo eliminar_usuario; do
	src=$(extraer "$f")
	[ -n "$src" ] || { echo "no encontré la función $f en panel.sh"; exit 1; }
	eval "$src"
done
banner() { :; }
pausa() { :; }

mk() { useradd -M -s /bin/false "$1" 2>/dev/null; }
mk zbpedro; mk zbjuan; mk zbana; mk ZbMayus 2>/dev/null || useradd --badname -M -s /bin/false ZbMayus 2>/dev/null
printf 'zbpedro:1:2099-01-01\nzbjuan:1:2099-01-01\nzbana:1:2099-01-01\nZbMayus:1:2099-01-01\n' > "$DB"
printf 'zbana:9999999999\n' > "$TEMPDB"
printf 'zbpedro:pw1\nzbjuan:pw2\n' > "$CLAVES"
printf 'zbpedro:A1B2C3D4E5F6\nzbjuan:111122223333\n' > "$TOKENS"

en_db() { awk -F: -v u="$1" '$1==u{f=1} END{exit !f}' "$DB" && echo si || echo no; }
existe() { id "$1" >/dev/null 2>&1 && echo si || echo no; }
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }

echo "1) La lista muestra todos los usuarios numerados"
SAL=$(lista_para_borrar | sed 's/\x1b\[[0-9;]*m//g')
chequear "aparece zbpedro" "si" "$(grep -q 'zbpedro' <<<"$SAL" && echo si || echo no)"
chequear "la lista está numerada [1] y [2]" "si" "$(grep -q '\[1\]' <<<"$SAL" && grep -q '\[2\]' <<<"$SAL" && echo si || echo no)"

echo "2) Escribir el usuario y Enter lo borra (y Enter vacío vuelve); limpia clave y token"
printf 'zbpedro\n\n' | eliminar_usuario >/dev/null
chequear "zbpedro fuera de la base" "no" "$(en_db zbpedro)"
chequear "zbpedro fuera del sistema" "no" "$(existe zbpedro)"
chequear "zbpedro sin token" "0" "$(grep -c '^zbpedro:' "$TOKENS")"
chequear "zbpedro sin clave guardada" "0" "$(grep -c '^zbpedro:' "$CLAVES")"
chequear "zbjuan intacto" "si" "$(en_db zbjuan)"
chequear "token de zbjuan intacto" "1" "$(grep -c '^zbjuan:' "$TOKENS")"

echo "3) Sin distinguir mayúsculas"
printf 'zbmayus\n\n' | eliminar_usuario >/dev/null
chequear "ZbMayus borrado escribiendo zbmayus" "no" "$(en_db ZbMayus)"

echo "5) Un nombre que no existe no borra nada (ni cuentas del sistema)"
printf 'root\nnoexiste\n\n' | eliminar_usuario >/dev/null
chequear "root sigue existiendo" "si" "$(existe root)"
chequear "zbjuan sigue" "si" "$(en_db zbjuan)"

echo "6) Se puede borrar uno tras otro sin salir; el temporal sale de temporales.db"
printf 'zbjuan\nzbana\n\n' | eliminar_usuario >/dev/null
chequear "zbjuan borrado" "no" "$(en_db zbjuan)"
chequear "zbana borrado" "no" "$(en_db zbana)"
chequear "zbana fuera de temporales.db" "0" "$(grep -c '^zbana:' "$TEMPDB")"

echo "7) Escribir el número de la lista borra a ese usuario"
mk zbn1; mk zbn2
printf 'zbn1:1:2099-01-01\nzbn2:1:2099-01-01\n' >> "$DB"
# orden actual: 1 = zbn1, 2 = zbn2
printf '2\n\n' | eliminar_usuario >/dev/null
chequear "el número 2 borró zbn2" "no" "$(en_db zbn2)"
chequear "zbn1 sigue" "si" "$(en_db zbn1)"
printf '99\n0\n\n' | eliminar_usuario >/dev/null
chequear "un número fuera de la lista no borra nada" "si" "$(en_db zbn1)"
SAL=$(lista_para_borrar | sed 's/\x1b\[[0-9;]*m//g')
chequear "la lista se renumera sola" "si" "$(grep -q '\[1\].*zbn1' <<<"$SAL" && echo si || echo no)"

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
