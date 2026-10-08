#!/bin/bash
# Instala el bot de Telegram de Zumo VPN en la VPS (servicio systemd "zumo-bot").
#   curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/bot/instalar-bot.sh | bash
# Pide el token del bot (@BotFather) y tu ID de Telegram (lo ves con /id en el bot).
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
# Si se instala/actualiza desde otra dirección (ZUMO_BASE), se recuerda para las próximas veces.
if [ -n "${ZUMO_BASE:-}" ]; then case "$ZUMO_BASE" in https://*) mkdir -p /etc/zumo; printf '%s' "${ZUMO_BASE%/}" > /etc/zumo/base.url ;; esac; fi
BASE="${ZUMO_BASE:-$(cat /etc/zumo/base.url 2>/dev/null || echo https://raw.githubusercontent.com/adri40606941-ui/Zumo/main)}/bot"
NC="?nocache=$(date +%s)"
mkdir -p /etc/zumo /opt/zumo-bot

echo "Instalando dependencias..."
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends python3 python3-nacl python3-paramiko openssl curl ca-certificates >/dev/null 2>&1 || { echo "✘ No se pudieron instalar las dependencias"; exit 1; }
# Para las vistas previas de la apariencia de la app y para achicar el ícono y el fondo. Si no se
# pueden instalar, el bot anda igual (sin vistas previas).
apt-get install -y --no-install-recommends python3-pil fonts-dejavu-core >/dev/null 2>&1 || echo "Aviso: sin python3-pil el bot no manda vistas previas de la app (lo demás funciona)."
# Opcionales, cada uno por separado: los emojis a color (para ver tu emoji del logo en la vista previa) y
# las letras angosta y fina de verdad. Sin ellos la vista previa los simula o muestra un escudo de muestra.
for p in fonts-noto-color-emoji fonts-dejavu-extra; do
	apt-get install -y --no-install-recommends "$p" >/dev/null 2>&1 || echo "Aviso: no se pudo instalar $p (la vista previa lo aproxima)."
done

ARCHIVOS="servidores.py compilar.py compilar_vps.py tema.py vista.py marca.py respaldo.py centro.py maquinas.py maquinas_bot.py zumo-bot.py"
for f in $ARCHIVOS; do
	curl -fsSL "$BASE/$f$NC" -o "/opt/zumo-bot/$f" || { echo "✘ No se pudo bajar $f"; exit 1; }
done
# Para compilar la app en esta VPS (sin GitHub): se corre una sola vez, a mano (ver README).
curl -fsSL "$BASE/instalar-compilador.sh$NC" -o /opt/zumo-bot/instalar-compilador.sh || { echo "✘ No se pudo bajar instalar-compilador.sh"; exit 1; }
chmod 755 /opt/zumo-bot/instalar-compilador.sh
rm -f /opt/zumo-bot/zs.py   # el bot ya no arma archivos .zs
( cd /opt/zumo-bot && python3 -m py_compile $ARCHIVOS ) || { echo "✘ El bot bajado tiene errores"; exit 1; }
chmod 755 /opt/zumo-bot/zumo-bot.py

if [ ! -f /etc/zumo/bot.env ]; then
	TOK="${BOT_TOKEN:-}"; ADM="${ADMINS:-}"
	[ -n "$TOK" ] || read -rp "Token del bot (@BotFather): " TOK </dev/tty
	[ -n "${BOT_TOKEN:-}" ] || read -rp "Tu ID de Telegram (si no lo sabés, dejalo vacío y mandá /id al bot): " ADM </dev/tty
	( umask 077
	  {
	  echo "BOT_TOKEN=$TOK"
	  echo "ADMINS=$ADM"
	  } > /etc/zumo/bot.env )
else
	echo "Se conserva /etc/zumo/bot.env (editalo si querés cambiar el token o los admins)."
fi

# Compilar la app desde el bot: token de GitHub (fine-grained, solo el repo Zumo, permisos
# Actions: Read and write, Secrets: Read and write, Contents: Read-only). Se puede dejar vacío.
if ! grep -q '^GITHUB_TOKEN=.\+' /etc/zumo/bot.env; then
	echo
	echo "Para compilar la app desde el bot hace falta un token de GitHub (ver README, sección Bot)."
	read -rp "Token de GitHub (vacío = sin compilar desde el bot): " GHT </dev/tty
	if [ -n "$GHT" ]; then
		sed -i '/^GITHUB_TOKEN=/d;/^GITHUB_REPO=/d' /etc/zumo/bot.env
		( umask 077; { echo "GITHUB_TOKEN=$GHT"; echo "GITHUB_REPO=adri40606941-ui/Zumo"; } >> /etc/zumo/bot.env )
		chmod 600 /etc/zumo/bot.env
	fi
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
