#!/bin/bash
# Baneo automático de IP que fallan la contraseña en el puerto 22 (fail2ban).
# - Los clientes que entran por PDirect (80), WebSocket, BHTTP, etc. llegan al sshd como
#   127.0.0.1: están en ignoreip, así que sus fallos NUNCA banean a nadie.
# - El baneo es solo del puerto SSH real: no toca 80/443 ni al limitador (zumo-limit).
# - No pisa un jail.local que ya exista (si lo cambiaste a mano se respeta).
JAIL="${ZUMO_F2B_JAIL:-/etc/fail2ban/jail.local}"
[ "$(id -u)" -eq 0 ] || [ -n "$ZUMO_F2B_JAIL" ] || { echo "Ejecutá como root"; exit 1; }

puerto=$(sshd -T 2>/dev/null | awk '$1=="port"{print $2; exit}')
puerto="${puerto:-22}"
propias=$(ip -o addr show 2>/dev/null | awk '{split($4,a,"/"); print a[1]}' | grep -v ':' | tr '\n' ' ')

if [ ! -f "$JAIL" ]; then
	mkdir -p "$(dirname "$JAIL")"
	cat > "$JAIL" <<EOJ
[sshd]
enabled  = true
backend  = systemd
port     = $puerto
maxretry = 5
findtime = 10m
bantime  = 1h
ignoreip = 127.0.0.1/8 ::1 $propias
EOJ
fi
[ -n "$ZUMO_F2B_JAIL" ] && exit 0

if ! command -v fail2ban-client >/dev/null 2>&1; then
	export DEBIAN_FRONTEND=noninteractive
	apt-get update >/dev/null 2>&1
	apt-get install -y fail2ban python3-systemd >/dev/null 2>&1 || { echo "No se pudo instalar fail2ban"; exit 1; }
fi
systemctl enable fail2ban >/dev/null 2>&1
systemctl restart fail2ban 2>/dev/null
sleep 1
if systemctl is-active --quiet fail2ban; then echo "fail2ban activo (puerto $puerto, ignora loopback)"; else echo "fail2ban no quedó activo"; exit 1; fi
