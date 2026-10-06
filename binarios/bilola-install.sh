#!/bin/bash
# Instala el servidor BilolaGo (BHTTP v1/v2 + XHTTP opcional) como servicio systemd "bilola".
#   bash <(curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/binarios/bilola-install.sh) --port 8081
# Opciones:
#   --port N          puerto BHTTP (por defecto 8081; no pisa el 80 ni el 8880 del panel)
#   --ssh N           puerto SSH de destino (22)
#   --xhttp HOST:PORT activa XHTTP (TLS/HTTP2), ej: 0.0.0.0:443  (pide --cert y --key)
#   --cert / --key    certificado y clave TLS para XHTTP
#   --uninstall       lo quita
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
BASE="${ZUMO_BASE:-$(cat /etc/zumo/base.url 2>/dev/null || echo https://raw.githubusercontent.com/adri40606941-ui/Zumo/main)}/binarios"
PORT=8081; SSH=22; XH=""; CERT=""; KEY=""; DEL=0
while [ $# -gt 0 ]; do
	case "$1" in
		--port) PORT="$2"; shift 2 ;;
		--ssh) SSH="$2"; shift 2 ;;
		--xhttp) XH="$2"; shift 2 ;;
		--cert) CERT="$2"; shift 2 ;;
		--key) KEY="$2"; shift 2 ;;
		--uninstall) DEL=1; shift ;;
		*) echo "Opción desconocida: $1"; exit 1 ;;
	esac
done
if [ "$DEL" = 1 ]; then
	systemctl disable --now bilola >/dev/null 2>&1
	rm -f /etc/systemd/system/bilola.service /usr/local/lib/bilola-server
	systemctl daemon-reload
	echo "✔ Bilola quitado"; exit 0
fi
case "$PORT$SSH" in *[!0-9]*|"") echo "Los puertos deben ser números"; exit 1 ;; esac
if [ -n "$XH" ] && { [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; }; then echo "XHTTP necesita --cert y --key (archivos que existan)"; exit 1; fi
if ss -ltn "( sport = :$PORT )" 2>/dev/null | grep -q LISTEN && ! systemctl is-active --quiet bilola; then
	echo "✘ El puerto $PORT ya está en uso. Elegí otro con --port"; exit 1
fi
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
D="$(dirname "$(readlink -f "$0")")"
if [ -f "$D/bilola-server" ] && [ -f "$D/bilola-server.sha256" ]; then cp "$D/bilola-server" "$D/bilola-server.sha256" "$T/"
else
	curl -fsSL "$BASE/bilola-server?n=$(date +%s)" -o "$T/bilola-server" && curl -fsSL "$BASE/bilola-server.sha256?n=$(date +%s)" -o "$T/bilola-server.sha256" || { echo "✘ No se pudo bajar"; exit 1; }
fi
( cd "$T" && sha256sum -c bilola-server.sha256 >/dev/null 2>&1 ) || { echo "✘ El archivo no coincide con su sha256"; exit 1; }
case "$(uname -m)" in x86_64|amd64) ;; *) echo "✘ Este binario es solo para x86_64 (amd64)"; exit 1 ;; esac
install -m 755 "$T/bilola-server" /usr/local/lib/bilola-server
EXTRA=""; [ -n "$XH" ] && EXTRA=" -xhttp-listen $XH -tls-cert $CERT -tls-key $KEY"
cat > /etc/systemd/system/bilola.service <<U
[Unit]
Description=Bilola - BHTTP/XHTTP hacia SSH
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/local/lib/bilola-server -listen 0.0.0.0:$PORT -target 127.0.0.1:$SSH$EXTRA
Restart=always
RestartSec=5
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload
systemctl enable bilola >/dev/null 2>&1
systemctl restart bilola
sleep 2
if systemctl is-active --quiet bilola; then echo "✔ Bilola activo en el puerto $PORT → SSH $SSH${XH:+ · XHTTP $XH}"; else echo "✘ No quedó activo: journalctl -u bilola -n 30"; exit 1; fi
