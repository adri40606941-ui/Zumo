#!/bin/bash
# Prueba de las funciones de dispositivo (Android ID) del panel y de zumo-lib.sh, con el binario
# zumoid real y archivos temporales. No toca /etc/zumo.
#
# Uso: bash tests/prueba-dispositivos.sh   (necesita el binario zumoid-amd64 del repo o Go para compilarlo)
set -u
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-disp.XXXXXX)
trap 'rm -rf "$T"' EXIT
FALLOS=0
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }

BIN="$T/zumoid"
if [ "$(uname -m)" = "x86_64" ] && [ -x "$AQUI/zumoid-amd64" ]; then cp "$AQUI/zumoid-amd64" "$BIN"
else (cd "$AQUI/zumoid" && CGO_ENABLED=0 go build -o "$BIN" .) || { echo "no pude obtener zumoid"; exit 1; }; fi

export ZUMO_ZUMOID="$BIN" ZUMO_DISP_DB="$T/d.db" ZUMO_DISP_LOCK="$T/d.lock" ZUMO_DISP_LOG="$T/d.log"
N=; source "$AQUI/zumo-lib.sh"
eval "$(sed -n '/^mostrar_dispositivo() {/,/^}$/p' "$AQUI/panel.sh")"
limpio() { sed 's/\x1b\[[0-9;]*m//g'; }
# corre mostrar_dispositivo en este mismo proceso (para ver ID_DISP/LOCK_DISP) y deja el texto limpio en $out
ver() { mostrar_dispositivo "$1" > "$T/o.txt"; RC=$?; out=$(limpio < "$T/o.txt"); }
ID1=aaaaaaaaaaaaaaaa; ID2=bbbbbbbbbbbbbbbb

echo "sin registro"
ver ana; chequear "rc 1 sin datos" 1 "$RC"
chequear "texto sin datos" 1 "$(grep -c 'sin datos' <<<"$out")"
chequear "get vacío" "" "$(zumo_disp_get ana)"
chequear "lock sin ID falla" 1 "$(zumo_disp_lock ana 2>/dev/null; echo $?)"

echo "con registro (lo escribe el servicio al recibir el ID de la app)"
printf 'ana:%s:0:1700000000:1700003600\n' "$ID1" > "$T/d.db"
ver ana
chequear "muestra el ID" 1 "$(grep -c "$ID1" <<<"$out")"
chequear "sin vincular" 1 "$(grep -c 'sin vincular' <<<"$out")"
chequear "ID_DISP" "$ID1" "$ID_DISP"
chequear "LOCK_DISP" 0 "$LOCK_DISP"

echo "vincular / desvincular"
zumo_disp_lock ana
chequear "queda vinculado" "$ID1	1	1700000000	1700003600" "$(zumo_disp_get ana)"
ver ana; chequear "muestra vinculado" 1 "$(grep -c 'vinculado' <<<"$out")"
chequear "LOCK_DISP=1" 1 "$LOCK_DISP"
zumo_disp_unlock ana
chequear "desvinculado conserva el ID" "$ID1	0	1700000000	1700003600" "$(zumo_disp_get ana)"

echo "intento bloqueado en el registro"
printf '1700007200\tana\totro-dispositivo\t%s\n1700007300\tbeto\totro-dispositivo\t%s\n' "$ID2" "$ID1" > "$T/d.log"
ver ana
chequear "muestra el intento de otro celular" 1 "$(grep -c "Intento bloqueado.*$ID2" <<<"$out")"
printf '1700009999\tana\tsin-verificar\t-\n' >> "$T/d.log"
ver ana
chequear "muestra sesión cortada" 1 "$(grep -c 'Sesión cortada' <<<"$out")"

echo "renombrar y olvidar"
zumo_disp_rename ana carla
chequear "ana ya no existe" "" "$(zumo_disp_get ana)"
chequear "carla tiene el ID" "$ID1" "$(zumo_disp_get carla | cut -f1)"
zumo_disp_forget carla
chequear "olvidar borra" "" "$(zumo_disp_get carla)"
chequear "olvidar un inexistente no falla" 0 "$(zumo_disp_forget nadie; echo $?)"

echo "VPS sin el binario (sin actualizar): nada se rompe"
ZUMO_ZUMOID=/no/existe
chequear "get no imprime" "" "$(zumo_disp_get ana)"
chequear "forget no falla" 0 "$(zumo_disp_forget ana; echo $?)"
chequear "rename no falla" 0 "$(zumo_disp_rename a b; echo $?)"

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO OK"; else echo "$FALLOS FALLO(S)"; exit 1; fi
