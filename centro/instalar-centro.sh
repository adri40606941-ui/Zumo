#!/bin/bash
# Instala el "centro" de Zumo en una VPS vacía (Ubuntu 22.04/24.04 o Debian 12):
#   - copia del repo (/opt/zumo-src) y publicación de los instaladores con tu dominio (nginx + HTTPS)
#   - compilador de la app Android (Java 17, SDK y NDK, Gradle) para compilar acá sin GitHub
#   - bot de Telegram (compila acá, respaldo cifrado diario, clave de firma)
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/centro/instalar-centro.sh)
#
# Se puede correr de nuevo sin problema (no pisa lo que ya está). Variables opcionales para no
# responder preguntas: DOMINIO, EMAIL, BOT_TOKEN, ADMINS, RESPALDO_PASS, GITHUB_TOKEN, REPO_URL.
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

V='\e[1;38;5;141m'; G='\e[1;32m'; R='\e[1;31m'; Y='\e[1;33m'; N='\e[0m'
ok()   { echo -e " ${G}✔ $1${N}"; }
err()  { echo -e " ${R}✘ $1${N}"; }
aviso(){ echo -e " ${Y}! $1${N}"; }
paso() { echo -e "\n${V}[$1] $2${N}"; }
fallar() { err "$1"; exit 1; }
preguntar() { # preguntar VAR "texto" [secreto]
	local v="$1" t="$2" r=""
	[ -n "${!v:-}" ] && return 0
	if [ "${3:-}" = s ]; then read -rsp " $t: " r </dev/tty; echo; else read -rp " $t: " r </dev/tty; fi
	printf -v "$v" '%s' "$r"
}

REPO_URL="${REPO_URL:-https://github.com/adri40606941-ui/Zumo.git}"
SRC=/opt/zumo-src
SDK=/opt/android-sdk
WEB=/var/www/zumo
CMDLINE_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
GRADLE_URL="https://services.gradle.org/distributions/gradle-8.7-bin.zip"
mkdir -p /etc/zumo
chmod 700 /etc/zumo

[ -r /etc/os-release ] && . /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) fallar "Este instalador es para Ubuntu o Debian (esta VPS es ${ID:-desconocida}).";; esac
command -v apt-get >/dev/null || fallar "Falta apt-get"

echo -e "${V}╔══════════════════════════════════════╗\n║        ZUMO · INSTALAR EL CENTRO     ║\n╚══════════════════════════════════════╝${N}"

# --- datos -------------------------------------------------------------------
[ -f /etc/zumo/centro.env ] && . /etc/zumo/centro.env
paso "0/7" "Datos"
preguntar DOMINIO "Dominio o subdominio de esta VPS (ej: panel.tudominio.com; vacío = sin dominio por ahora)"
preguntar EMAIL "Email para el certificado HTTPS (vacío = sin email)"
preguntar BOT_TOKEN "Token del bot de Telegram (@BotFather)"
preguntar ADMINS "Tu ID de Telegram (si no lo sabés, dejalo vacío y mandá /id al bot)"
preguntar RESPALDO_PASS "Contraseña del respaldo (mínimo 8, guardala aparte; se usa para cifrar)" s
[ "${#RESPALDO_PASS}" -ge 8 ] || fallar "La contraseña del respaldo tiene que tener al menos 8 caracteres."
[ -n "$BOT_TOKEN" ] || fallar "Falta el token del bot."
if [ -z "${SECRETO:-}" ]; then SECRETO=$(openssl rand -hex 12); fi
( umask 077; { echo "DOMINIO=$DOMINIO"; echo "SECRETO=$SECRETO"; echo "REPO_URL=$REPO_URL"; } > /etc/zumo/centro.env )

# --- 1) swap -----------------------------------------------------------------
paso "1/7" "Memoria"
RAM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ] && [ "$RAM_MB" -lt 6000 ]; then
	if fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
		chmod 600 /swapfile; mkswap /swapfile >/dev/null && swapon /swapfile && \
		{ grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab; } && ok "swap de 2 GB creado (compilar la app lo necesita)"
	else aviso "No se pudo crear el swap; si la compilación se queda sin memoria, agregalo a mano."; fi
else ok "memoria: ${RAM_MB} MB"
fi
LIBRE_GB=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
[ "${LIBRE_GB:-0}" -ge 12 ] || aviso "Quedan ${LIBRE_GB} GB libres; para compilar conviene tener 12 GB o más."

# --- 2) paquetes -------------------------------------------------------------
paso "2/7" "Paquetes"
apt-get update -qq >/dev/null 2>&1
PAQ="git curl ca-certificates unzip openssl python3 python3-cryptography python3-nacl openjdk-17-jdk-headless nginx"
[ -n "$DOMINIO" ] && PAQ="$PAQ certbot python3-certbot-nginx"
apt-get install -y --no-install-recommends $PAQ >/dev/null 2>&1 || fallar "No se pudieron instalar los paquetes (apt-get install $PAQ)"
ok "paquetes instalados"

# --- 3) código ---------------------------------------------------------------
paso "3/7" "Código del proyecto"
URL_CLON="$REPO_URL"
[ -n "${GITHUB_TOKEN:-}" ] && URL_CLON="${REPO_URL/https:\/\//https://x-access-token:${GITHUB_TOKEN}@}"
if [ -d "$SRC/.git" ]; then
	ok "ya estaba clonado (se actualiza con zumo-publicar)"
else
	git clone --recurse-submodules "$URL_CLON" "$SRC" >/dev/null 2>&1 || fallar "No se pudo clonar $REPO_URL (si el repo es privado, pasá GITHUB_TOKEN)"
	ok "repo clonado en $SRC"
fi
chmod 700 "$SRC"

# --- 4) compilador de la app -------------------------------------------------
paso "4/7" "Compilador de la app (SDK y NDK de Android, Gradle)"
if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
	T=$(mktemp -d)
	curl -fsSL "$CMDLINE_URL" -o "$T/c.zip" || fallar "No se pudo bajar las herramientas de Android ($CMDLINE_URL)"
	unzip -q "$T/c.zip" -d "$T" && mkdir -p "$SDK/cmdline-tools" && rm -rf "$SDK/cmdline-tools/latest" && mv "$T/cmdline-tools" "$SDK/cmdline-tools/latest"
	rm -rf "$T"
fi
export ANDROID_HOME="$SDK" ANDROID_SDK_ROOT="$SDK"
SM="$SDK/cmdline-tools/latest/bin/sdkmanager"
yes | "$SM" --licenses >/dev/null 2>&1
"$SM" "platforms;android-34" "build-tools;34.0.0" "ndk;27.0.12077973" >/dev/null 2>&1 || fallar "sdkmanager no pudo bajar el SDK/NDK"
ok "SDK y NDK de Android"
if [ ! -x /opt/gradle/bin/gradle ]; then
	T=$(mktemp -d)
	curl -fsSL "$GRADLE_URL" -o "$T/g.zip" || fallar "No se pudo bajar Gradle"
	unzip -q "$T/g.zip" -d "$T" && rm -rf /opt/gradle && mv "$T"/gradle-8.7 /opt/gradle
	rm -rf "$T"
fi
ok "Gradle 8.7"
echo "sdk.dir=$SDK" > "$SRC/android/local.properties"

# --- 5) publicar instaladores con el dominio -------------------------------
paso "5/7" "Publicar los instaladores"
cat > /usr/local/bin/zumo-publicar <<'PUB'
#!/bin/bash
# Actualiza el clon del repo y deja publicados los instaladores (sin .git ni la app).
. /etc/zumo/centro.env
SRC=/opt/zumo-src; WEB=/var/www/zumo/$SECRETO
git -C "$SRC" checkout -q -- android/servidores.txt 2>/dev/null
git -C "$SRC" pull -q --ff-only --recurse-submodules >/dev/null 2>&1 || echo "(no se pudo actualizar desde GitHub: se publica lo que hay)"
NUEVO=$(mktemp -d)
git -C "$SRC" archive HEAD | tar -x -C "$NUEVO" || { echo "no se pudo preparar la publicación"; rm -rf "$NUEVO"; exit 1; }
rm -rf "$NUEVO/android"
mkdir -p "$(dirname "$WEB")"
rm -rf "$WEB.viejo"; [ -d "$WEB" ] && mv "$WEB" "$WEB.viejo"
mv "$NUEVO" "$WEB" && rm -rf "$WEB.viejo"
chmod -R a+rX /var/www/zumo
echo "publicado: $(git -C "$SRC" log -1 --format='%h %s' 2>/dev/null)"
PUB
chmod 755 /usr/local/bin/zumo-publicar
cat > /etc/systemd/system/zumo-publicar.service <<'U'
[Unit]
Description=Zumo - actualizar y publicar los instaladores
[Service]
Type=oneshot
ExecStart=/usr/local/bin/zumo-publicar
U
cat > /etc/systemd/system/zumo-publicar.timer <<'U'
[Unit]
Description=Zumo - publicar los instaladores cada hora
[Timer]
OnBootSec=2min
OnUnitActiveSec=1h
[Install]
WantedBy=timers.target
U
systemctl daemon-reload
systemctl enable --now zumo-publicar.timer >/dev/null 2>&1
/usr/local/bin/zumo-publicar >/dev/null 2>&1 || aviso "zumo-publicar falló; revisá: /usr/local/bin/zumo-publicar"
ok "instaladores publicados en $WEB/$SECRETO"

if [ -n "$DOMINIO" ]; then
	cat > /etc/nginx/sites-available/zumo <<NG
server {
	listen 80;
	listen [::]:80;
	server_name $DOMINIO;
	root $WEB;
	autoindex off;
	server_tokens off;
	location /$SECRETO/ { try_files \$uri =404; }
	location / { return 404; }
}
NG
	ln -sf /etc/nginx/sites-available/zumo /etc/nginx/sites-enabled/zumo
	rm -f /etc/nginx/sites-enabled/default
	nginx -t >/dev/null 2>&1 || fallar "La configuración de nginx no es válida (nginx -t)"
	systemctl enable nginx >/dev/null 2>&1; systemctl reload nginx 2>/dev/null || systemctl restart nginx
	CB=(certbot --nginx -d "$DOMINIO" --non-interactive --agree-tos --redirect)
	if [ -n "$EMAIL" ]; then CB+=(-m "$EMAIL"); else CB+=(--register-unsafely-without-email); fi
	if "${CB[@]}" >/tmp/zumo-certbot.log 2>&1; then
		ok "HTTPS activo en https://$DOMINIO"
		BASE_PUB="https://$DOMINIO/$SECRETO"
		( umask 077; echo "$BASE_PUB" > /etc/zumo/base.url )
	else
		aviso "No se pudo sacar el certificado HTTPS (¿el dominio ya apunta a esta VPS?). Log: /tmp/zumo-certbot.log"
		aviso "En Cloudflare dejá el registro en 'Solo DNS' (nube gris) hasta que salga; después podés volver a activar el proxy (modo SSL: Full)."
		aviso "Cuando el dominio apunte acá: corré de nuevo este instalador."
	fi
fi

# --- 6) bot ------------------------------------------------------------------
paso "6/7" "Bot de Telegram"
BOT_TOKEN="$BOT_TOKEN" ADMINS="$ADMINS" RESPALDO_PASS="$RESPALDO_PASS" ZUMO_COMPILAR=local ZUMO_BOT_SRC="$SRC/bot" \
	bash "$SRC/bot/instalar-bot.sh" || fallar "No se pudo instalar el bot"

# --- 7) listo ----------------------------------------------------------------
paso "7/7" "Resumen"
echo
if [ -n "${BASE_PUB:-}" ]; then
	echo -e " Para instalar en una VPS nueva:"
	echo -e "   ${G}ZUMO_BASE=$BASE_PUB bash <(curl -fsSL $BASE_PUB/install.sh)${N}"
	echo -e " Para actualizar una VPS que ya tenés:"
	echo -e "   ${G}ZUMO_BASE=$BASE_PUB bash <(curl -fsSL $BASE_PUB/actualizar.sh)${N}"
	echo -e " (la dirección lleva un código secreto: no la compartas)"
else
	aviso "Todavía no hay dirección pública (falta el dominio o el HTTPS)."
fi
echo
echo " Siguiente paso, en Telegram (abrí el bot):"
echo "   💾 Respaldo → 📥 Importar clave de firma   (la clave de tu app actual, para que no cambie)"
echo "   📱 App Android → 🔨 Compilar y enviarme el APK"
echo " Si todavía no mandaste /id al bot, hacelo y poné ese número en ADMINS de /etc/zumo/bot.env."
