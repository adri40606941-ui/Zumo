#!/bin/bash
# Prueba de las funciones de scripts/endurecer.sh (sin root ni cambiar nada del sistema).
# Uso: bash tests/prueba-endurecer.sh
set -u
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; FALLOS=0
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }
export ZUMO_SOLO_FUNCIONES=1 ZUMO_SSHD_DROPIN="$T/sshd_config.d/zumo.conf"
. "$AQUI/scripts/endurecer.sh"

SS='tcp   LISTEN 0 128 0.0.0.0:22        0.0.0.0:*
tcp   LISTEN 0 128 [::]:22           [::]:*
tcp   LISTEN 0 511 0.0.0.0:80        0.0.0.0:*
tcp   LISTEN 0 511 *:443             *:*
tcp   LISTEN 0 128 127.0.0.1:7391    0.0.0.0:*
tcp   LISTEN 0 4096 [::1]:5432       [::]:*
udp   UNCONN 0 0   127.0.0.53%lo:53  0.0.0.0:*
udp   UNCONN 0 0   0.0.0.0:7300      0.0.0.0:*
udp   UNCONN 0 0   [::]:7300         [::]:*'
chequear "puertos hacia afuera" "22/tcp 443/tcp 7300/udp 80/tcp" "$(echo "$SS" | puertos_escuchando | sort -t/ -k1,1n | tr '\n' ' ' | sed 's/ $//' | awk '{print}' | tr ' ' '\n' | sort | tr '\n' ' ' | sed 's/ $//')"
chequear "sin los de loopback" "no" "$(echo "$SS" | puertos_escuchando | grep -Eq '^(7391|5432|53)/' && echo si || echo no)"

printf 'ssh-ed25519 AAAAC3Nza... yo@pc\n' > "$T/k1"; : > "$T/k2"; printf '# solo un comentario\n' > "$T/k3"
tiene_llave "$T/k1" && r=si || r=no; chequear "con llave" "si" "$r"
tiene_llave "$T/k2" && r=si || r=no; chequear "archivo vacío" "no" "$r"
tiene_llave "$T/k3" && r=si || r=no; chequear "solo comentarios" "no" "$r"
tiene_llave "$T/no-existe" && r=si || r=no; chequear "sin archivo" "no" "$r"

escribir_dropin_ssh
chequear "drop-in de sshd" "PermitRootLogin prohibit-password" "$(grep -v '^#' "$T/sshd_config.d/zumo.conf")"
chequear "auto-upgrades" 'APT::Periodic::Unattended-Upgrade "1";' "$(texto_auto_upgrades | grep Unattended)"

# --ver no cambia nada (y como no es root real en la prueba, solo se comprueba que la ayuda y las opciones funcionan)
chequear "ayuda" "0" "$(bash "$AQUI/scripts/endurecer.sh" --help >/dev/null 2>&1; echo $?)"
chequear "opción rara" "1" "$(bash "$AQUI/scripts/endurecer.sh" --nada >/dev/null 2>&1; echo $?)"
bash -n "$AQUI/scripts/endurecer.sh" && chequear "sintaxis" "ok" "ok"
[ "$FALLOS" -eq 0 ] && echo "TODO OK" || { echo "$FALLOS fallos"; exit 1; }
