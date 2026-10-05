#!/bin/bash
# Instala el bot de Telegram de Zumo VPN en la VPS (servicio systemd "zumo-bot").
#   curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/bot/instalar-bot.sh | bash
# Pide el token del bot (@BotFather) y tu ID de Telegram (lo ves con /id en el bot).
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
BASE="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/bot"
NC="?nocache=$(date +%s)"
mkdir -p /etc/zumo /opt/zumo-bot

echo "Instalando dependencias..."
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends python3 python3-cryptography curl ca-certificates >/dev/null 2>&1 || { echo "✘ No se pudieron instalar las dependencias"; exit 1; }

for f in zs.py zumo-bot.py; do
	curl -fsSL "$BASE/$f$NC" -o "/opt/zumo-bot/$f" || { echo "✘ No se pudo bajar $f"; exit 1; }
done
python3 -m py_compile /opt/zumo-bot/zs.py /opt/zumo-bot/zumo-bot.py || { echo "✘ El bot bajado tiene errores"; exit 1; }
chmod 755 /opt/zumo-bot/zumo-bot.py

if [ ! -f /etc/zumo/bot.env ]; then
	read -rp "Token del bot (@BotFather): " TOK </dev/tty
	read -rp "Tu ID de Telegram (si no lo sabés, dejalo vacío y mandá /id al bot): " ADM </dev/tty
	( umask 077
	  {
	  echo "BOT_TOKEN=$TOK"
	  echo "ADMINS=$ADM"
	  echo "# Secreto del cifrado de los .zs: tiene que ser el mismo que lleva la app"
	  echo "ZS_SECRET=f14a3636d2aef23c893604756b861ea2"
	  } > /etc/zumo/bot.env )
else
	echo "Se conserva /etc/zumo/bot.env (editalo si querés cambiar el token o los admins)."
fi

cat > /etc/systemd/system/zumo-bot.service <<'U'
[Unit]
Description=Zumo VPN - bot de Telegram
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/python3 /opt/zumo-bot/zumo-bot.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload
systemctl enable zumo-bot >/dev/null 2>&1
systemctl restart zumo-bot
sleep 2
if systemctl is-active --quiet zumo-bot; then echo "✔ Bot activo. Abrilo en Telegram y mandá /ayuda"; else echo "✘ No quedó activo: journalctl -u zumo-bot -n 30"; fi
