#!/bin/bash
# Prueba de "Eliminar usuario" del panel: extrae las funciones de panel.sh y las
# ejecuta con una base temporal y usuarios de prueba. Escribe lo que escribiría
# la persona (nombre, nombre de cliente o HWID) y mira qué se borró.
#
# Uso: sudo bash tests/prueba-borrar.sh   (necesita root y useradd)
set -u
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-borrar.XXXXXX)
FALLOS=0
USERS="zbpedro zbjuan zbana zbn1 zbn2 HWIDAAAA1111 HWIDBBBB2222 HWIDCCCC3333"

limpiar() { for u in $USERS; do userdel "$u" 2>/dev/null; done; rm -rf "$T"; }
trap limpiar EXIT

DB="$T/usuarios.db"; TEMPDB="$T/temporales.db"
export ZUMO_DB="$DB" ZUMO_LOCK="$T/lock"
N='\e[0m'; L='---'
# shellcheck source=/dev/null
source "$AQUI/zumo-lib.sh"

# Funciones reales de panel.sh (se copian tal cual, sin ejecutar el menú).
extraer() { sed -n "/^$1() {/,/^}/p" "$AQUI/panel.sh"; }
for f in db_orden etiqueta_de es_hwid en_linea msg_ok msg_err lista_para_borrar buscar_usuario \
	borrar_usuario_completo eliminar_usuario; do
	src=$(extraer "$f")
	[ -n "$src" ] || { echo "no encontré la función $f en panel.sh"; exit 1; }
	eval "$src"
done
banner() { :; }
pausa() { PAUSAS=$((${PAUSAS:-0}+1)); }

mk() { useradd -M -s /bin/false "$1" 2>/dev/null; }
mkhwid() { useradd --badname -M -s /bin/false -c "hwid,$2" "$1" 2>/dev/null; }
mk zbpedro; mk zbjuan; mk zbana
mkhwid HWIDAAAA1111 "Carlos"; mkhwid HWIDBBBB2222 "Marta"; mkhwid HWIDCCCC3333 "Marta"
printf 'zbpedro:1:2099-01-01\nzbjuan:1:2099-01-01\nzbana:1:2099-01-01\nHWIDAAAA1111:1:2099-01-01\nHWIDBBBB2222:1:2099-01-01\nHWIDCCCC3333:1:2099-01-01\n' > "$DB"
printf 'zbana:9999999999\n' > "$TEMPDB"

en_db() { awk -F: -v u="$1" '$1==u{f=1} END{exit !f}' "$DB" && echo si || echo no; }
existe() { id "$1" >/dev/null 2>&1 && echo si || echo no; }
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }

echo "1) La lista muestra comunes y HWID juntos"
SAL=$(lista_para_borrar | sed 's/\x1b\[[0-9;]*m//g')
chequear "aparece zbpedro" "si" "$(grep -q 'zbpedro' <<<"$SAL" && echo si || echo no)"
chequear "aparece cliente Carlos (HWID)" "si" "$(grep -q 'Carlos (HWID)' <<<"$SAL" && echo si || echo no)"
chequear "la lista está numerada [1] y [2]" "si" "$(grep -q '\[1\]' <<<"$SAL" && grep -q '\[2\]' <<<"$SAL" && echo si || echo no)"
chequear "aparece su HWID debajo" "si" "$(grep -q 'HWIDAAAA1111' <<<"$SAL" && echo si || echo no)"

echo "2) Escribir el usuario común y Enter lo borra (y Enter vacío vuelve)"
printf 'zbpedro\n\n' | eliminar_usuario >/dev/null
chequear "zbpedro fuera de la base" "no" "$(en_db zbpedro)"
chequear "zbpedro fuera del sistema" "no" "$(existe zbpedro)"
chequear "zbjuan intacto" "si" "$(en_db zbjuan)"

echo "3) Escribir el HWID lo borra"
printf 'HWIDAAAA1111\n\n' | eliminar_usuario >/dev/null
chequear "HWIDAAAA1111 fuera de la base" "no" "$(en_db HWIDAAAA1111)"
chequear "HWIDAAAA1111 fuera del sistema" "no" "$(existe HWIDAAAA1111)"

echo "4) Nombre de cliente repetido: pide el HWID y no borra nada"
printf 'Marta\n\n' | eliminar_usuario >/dev/null
chequear "HWIDBBBB2222 sigue" "si" "$(en_db HWIDBBBB2222)"
chequear "HWIDCCCC3333 sigue" "si" "$(en_db HWIDCCCC3333)"
printf 'HWIDBBBB2222\n\n' | eliminar_usuario >/dev/null
chequear "con el HWID se borra solo ese" "no" "$(en_db HWIDBBBB2222)"
chequear "el otro Marta sigue" "si" "$(en_db HWIDCCCC3333)"

echo "5) Un nombre que no existe no borra nada (ni cuentas del sistema)"
printf 'root\nnoexiste\n\n' | eliminar_usuario >/dev/null
chequear "root sigue existiendo" "si" "$(existe root)"
chequear "zbjuan sigue" "si" "$(en_db zbjuan)"

echo "6) Al borrar avisa y vuelve al menú sin recargar la lista; el temporal sale de temporales.db"
printf 'zbjuan\nzbana\n' | eliminar_usuario >/dev/null
chequear "zbjuan borrado" "no" "$(en_db zbjuan)"
chequear "vuelve al menú: no sigue pidiendo (zbana no se borra)" "si" "$(en_db zbana)"
printf 'zbana\n' | eliminar_usuario >/dev/null
chequear "zbana borrado en otra pasada" "no" "$(en_db zbana)"
chequear "zbana fuera de temporales.db" "0" "$(grep -c '^zbana:' "$TEMPDB")"

echo "7) Escribir el número de la lista borra a ese usuario"
mk zbn1; mk zbn2
printf 'zbn1:1:2099-01-01\nzbn2:1:2099-01-01\n' >> "$DB"
# orden actual: 1 = HWIDCCCC3333, 2 = zbn1, 3 = zbn2
printf '3\n\n' | eliminar_usuario >/dev/null
chequear "el número 3 borró zbn2" "no" "$(en_db zbn2)"
chequear "zbn1 sigue" "si" "$(en_db zbn1)"
chequear "el cliente HWID sigue" "si" "$(en_db HWIDCCCC3333)"
printf '99\n0\n\n' | eliminar_usuario >/dev/null
chequear "un número fuera de la lista no borra nada" "si" "$(en_db zbn1)"
SAL=$(lista_para_borrar | sed 's/\x1b\[[0-9;]*m//g')
chequear "la lista se renumera sola" "si" "$(grep -q '\[2\].*zbn1' <<<"$SAL" && echo si || echo no)"

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS prueba(s) fallaron"; exit 1; fi
