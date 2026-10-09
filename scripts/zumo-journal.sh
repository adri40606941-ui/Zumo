#!/bin/bash
# Limita el registro del sistema (journald) para que no se coma el disco: 200 MB y 2 semanas.
# Alcanza de sobra para el baneo automático de fail2ban, que lee estos registros.
# Si ya tenías otro límite propio en /etc/systemd/journald.conf.d/, no se toca.
D="${ZUMO_JOURNAL_DIR:-/etc/systemd/journald.conf.d}"
[ "$(id -u)" -eq 0 ] || [ -n "$ZUMO_JOURNAL_DIR" ] || { echo "Ejecutá como root"; exit 1; }
if [ -z "$ZUMO_JOURNAL_DIR" ] && ! command -v journalctl >/dev/null 2>&1; then echo "sin journald: no hace falta"; exit 0; fi
# ¿ya hay un límite puesto por otro archivo que no sea el nuestro?
OTROS=$(ls "$D"/*.conf /etc/systemd/journald.conf 2>/dev/null | grep -v '/zumo\.conf$')
if [ -n "$OTROS" ] && grep -qsE '^[[:space:]]*SystemMaxUse=' $OTROS </dev/null; then
	echo "journald ya tiene un límite propio: no se cambia"; exit 0
fi
mkdir -p "$D"
printf '[Journal]\nSystemMaxUse=200M\nMaxRetentionSec=2week\n' > "$D/zumo.conf"
[ -n "$ZUMO_JOURNAL_DIR" ] && exit 0
systemctl restart systemd-journald 2>/dev/null
journalctl --rotate >/dev/null 2>&1          # cierra el archivo activo: si no, la limpieza no lo toca
journalctl --vacuum-size=200M >/dev/null 2>&1
echo "registro del sistema limitado a 200 MB / 2 semanas ($(journalctl --disk-usage 2>/dev/null | grep -o '[0-9.]*[KMG]' | head -1) ahora)"
