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
apt-get install -y --no-install-recommends python3 python3-nacl python3-paramiko python3-cryptography openssl curl git ca-certificates >/dev/null 2>&1 || { echo "✘ No se pudieron instalar las dependencias"; exit 1; }
# Para las vistas previas de la apariencia de la app y para achicar el ícono y el fondo. Si no se
# pueden instalar, el bot anda igual (sin vistas previas).
apt-get install -y --no-install-recommends python3-pil fonts-dejavu-core >/dev/null 2>&1 || echo "Aviso: sin python3-pil el bot no manda vistas previas de la app (lo demás funciona)."
# Opcionales, cada uno por separado: los emojis a color (para ver tu emoji del logo en la vista previa) y
# las letras angosta y fina de verdad. Sin ellos la vista previa los simula o muestra un escudo de muestra.
for p in fonts-noto-color-emoji fonts-dejavu-extra; do
	apt-get install -y --no-install-recommends "$p" >/dev/null 2>&1 || echo "Aviso: no se pudo instalar $p (la vista previa lo aproxima)."
done

ARCHIVOS="zs.py accesos.py codigos_bot.py publico.py servidores.py compilar.py compilar_vps.py tema.py vista.py marca.py respaldo.py centro.py maquinas.py maquinas_bot.py zumo-bot.py"
# Si la dirección de descarga no responde (por ejemplo GitHub caído) se usa la copia del repo de esta misma VPS
# (/opt/zumo-repo, la que el bot mantiene al día), así el bot se puede reinstalar sin GitHub.
for f in $ARCHIVOS; do
	curl -fsSL "$BASE/$f$NC" -o "/opt/zumo-bot/$f" 2>/dev/null \
		|| { [ -s "/opt/zumo-repo/bot/$f" ] && cp "/opt/zumo-repo/bot/$f" "/opt/zumo-bot/$f" && echo "Aviso: $f salió de la copia local (/opt/zumo-repo)."; } \
		|| { echo "✘ No se pudo bajar $f"; exit 1; }
done
# Para compilar la app en esta VPS (sin GitHub): se corre una sola vez, a mano (ver README).
curl -fsSL "$BASE/instalar-compilador.sh$NC" -o /opt/zumo-bot/instalar-compilador.sh 2>/dev/null \
	|| { [ -s /opt/zumo-repo/bot/instalar-compilador.sh ] && cp /opt/zumo-repo/bot/instalar-compilador.sh /opt/zumo-bot/instalar-compilador.sh; } \
	|| { echo "✘ No se pudo bajar instalar-compilador.sh"; exit 1; }
chmod 755 /opt/zumo-bot/instalar-compilador.sh
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

# Dominio de esta VPS: reparte la lista de servidores de la app y los instaladores (con un código de un solo uso
# que da el bot), así todo sigue andando aunque GitHub esté caído. Enter = bot.zumoserver.com, "no" = sin dominio.
if ! grep -q '^ZUMO_DOMINIO=.\+' /etc/zumo/bot.env; then
	DOM="${DOMINIO:-}"
	if [ -z "$DOM" ]; then
		read -rp "Dominio de esta VPS para los instaladores y la lista de la app [bot.zumoserver.com] (no = sin dominio): " DOM </dev/tty || DOM=""
		DOM="${DOM:-bot.zumoserver.com}"
	fi
	DOM=$(printf '%s' "$DOM" | tr 'A-Z' 'a-z')
	if printf '%s' "$DOM" | grep -Eq '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$' && [ "$DOM" != "no" ]; then
		( umask 077; echo "ZUMO_DOMINIO=$DOM" >> /etc/zumo/bot.env )
		chmod 600 /etc/zumo/bot.env
	else
		echo "Sin dominio: la lista de la app y los instaladores seguirán saliendo de GitHub."
	fi
fi
DOMINIO_BOT=$(grep -m1 '^ZUMO_DOMINIO=' /etc/zumo/bot.env | cut -d= -f2-)

# Copia completa del repo en esta VPS (panel, app, bot): de ahí salen los instaladores y se compila sin GitHub.
# El bot la mantiene al día con GitHub; si GitHub no responde, queda la última copia. Solo se clona si falta.
if [ -n "$DOMINIO_BOT" ] && [ ! -d /opt/zumo-repo/.git ]; then
	echo "Copiando el repo a /opt/zumo-repo..."
	GHR=$(grep -m1 '^GITHUB_REPO=' /etc/zumo/bot.env | cut -d= -f2-); GHR="${GHR:-adri40606941-ui/Zumo}"
	GHT2=$(grep -m1 '^GITHUB_TOKEN=' /etc/zumo/bot.env | cut -d= -f2-)
	gitc() {
		if [ -n "$GHT2" ]; then
			git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GHT2" | base64 -w0)" "$@"
		else
			git "$@"
		fi
	}
	rm -rf /opt/zumo-repo
	if gitc clone --quiet "https://github.com/$GHR.git" /opt/zumo-repo 2>/dev/null || git clone --quiet "https://github.com/$GHR.git" /opt/zumo-repo; then
		( cd /opt/zumo-repo && { gitc submodule update --init --recursive --quiet 2>/dev/null || { GHT2=""; gitc submodule update --init --recursive --quiet; }; } ) \
			|| echo "Aviso: no se pudo bajar el submódulo del túnel (hace falta para compilar la app; probá: bash /opt/zumo-bot/instalar-compilador.sh)."
	else
		rm -rf /opt/zumo-repo
		echo "Aviso: no pude copiar el repo (¿GitHub no responde?). Repetí este instalador cuando vuelva, o clonalo a mano en /opt/zumo-repo."
	fi
fi

# Puertos web (80 y 443) para el dominio, si hay firewall ufw activo.
if [ -n "$DOMINIO_BOT" ] && command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
	ufw allow 80,443/tcp >/dev/null 2>&1 && echo "Puertos 80 y 443 abiertos en ufw."
fi

# Lista de servidores de la app desde esta VPS (rápido, sin GitHub): se activa con ZUMO_DOMINIO en bot.env.
# Si existe el dominio, el bot abre el puerto 80 y el 443 (certificado propio, para Cloudflare en modo "Full").
if grep -q '^ZUMO_DOMINIO=.\+' /etc/zumo/bot.env; then
	mkdir -p /etc/zumo/web
	if [ ! -s /etc/zumo/web/cert.pem ] || [ ! -s /etc/zumo/web/key.pem ]; then
		DOM=$(grep -m1 '^ZUMO_DOMINIO=' /etc/zumo/bot.env | cut -d= -f2-)
		( umask 077; openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=$DOM" \
			-keyout /etc/zumo/web/key.pem -out /etc/zumo/web/cert.pem >/dev/null 2>&1 ) || echo "Aviso: no pude crear el certificado (solo funcionará el puerto 80)."
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
if systemctl is-active --quiet zumo-bot; then
	echo "✔ Bot activo. Abrilo en Telegram y mandá /ayuda"
	if [ -n "$DOMINIO_BOT" ]; then
		echo "  Dominio: $DOMINIO_BOT (en Cloudflare: registro A hacia esta VPS, nube naranja, SSL Flexible o Full)."
		echo "  Para instalar el panel en una VPS nueva: en el bot, 🔑 Instalar en VPS nueva."
		[ -d /opt/zumo-repo/.git ] || echo "  ⚠ Falta la copia del repo en /opt/zumo-repo: sin ella el dominio no puede entregar los instaladores."
	fi
else echo "✘ No quedó activo: journalctl -u zumo-bot -n 30"; fi
