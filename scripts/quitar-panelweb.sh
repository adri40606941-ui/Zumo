#!/bin/bash
# Quita el panel web de Zumo de esta VPS (servicio, archivos y configuración).
# No toca usuarios, limitador ni protocolos. python3-flask queda instalado
# (podés sacarlo con: apt-get remove python3-flask).
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }

systemctl disable --now zumo-web 2>/dev/null
rm -f /etc/systemd/system/zumo-web.service
systemctl daemon-reload
systemctl reset-failed zumo-web 2>/dev/null
rm -f /etc/zumo/panelweb.py /etc/zumo/panelweb.py.bak /etc/zumo/web.conf
echo "✔ Panel web quitado."
