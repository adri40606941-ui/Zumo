#!/bin/bash
# Prepara esta VPS (el centro) para compilar la app Android sin GitHub:
#   bash /opt/zumo-bot/instalar-compilador.sh
# Instala Java 17, Gradle, el Android SDK/NDK y una copia del repo en /opt/zumo-repo.
# Se puede correr de nuevo sin problema: lo que ya está instalado se salta.
# Necesita unos 8 GB libres y descarga varios cientos de MB (la primera compilación baja más).
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
set -u

SDK=/opt/android-sdk
GRADLE_VER=8.7
CMDLINE=commandlinetools-linux-11076708_latest.zip
NDK=27.0.12077973
REPO_DIR=/opt/zumo-repo
ENV=/etc/zumo/bot.env
export DEBIAN_FRONTEND=noninteractive

leer_env() { grep -m1 "^$1=" "$ENV" 2>/dev/null | cut -d= -f2-; }
GH_REPO="$(leer_env GITHUB_REPO)"; GH_REPO="${GH_REPO:-adri40606941-ui/Zumo}"
GH_TOKEN="$(leer_env GITHUB_TOKEN)"
fallo() { echo "✘ $*"; exit 1; }

# git con el token de GitHub solo para este comando (no se guarda en ningún archivo)
gitc() {
	if [ -n "$GH_TOKEN" ]; then
		git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)" "$@"
	else
		git "$@"
	fi
}

LIBRE_GB=$(df -BG --output=avail /opt | tail -1 | tr -dc '0-9')
[ "${LIBRE_GB:-0}" -ge 8 ] || fallo "Hacen falta 8 GB libres en /opt y hay ${LIBRE_GB:-0} GB."

# --- memoria: compilar con 2 vCPU y 4 GB entra justo; con 4 GB de swap de reserva no se cae
MEM_MB=$(awk '/^MemTotal/{print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/^SwapTotal/{print int($2/1024)}' /proc/meminfo)
if [ $((MEM_MB + SWAP_MB)) -lt 6000 ] && [ ! -f /swapfile-zumo ]; then
	echo "Agregando 4 GB de swap (memoria de reserva para compilar)..."
	fallocate -l 4G /swapfile-zumo 2>/dev/null || dd if=/dev/zero of=/swapfile-zumo bs=1M count=4096 status=none
	chmod 600 /swapfile-zumo
	mkswap /swapfile-zumo >/dev/null && swapon /swapfile-zumo || fallo "No pude activar el swap"
	grep -q '^/swapfile-zumo' /etc/fstab || echo '/swapfile-zumo none swap sw 0 0' >> /etc/fstab
fi

echo "Instalando Java 17 y herramientas..."
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends openjdk-17-jdk-headless git unzip curl ca-certificates openssl python3 >/dev/null 2>&1 \
	|| fallo "No se pudieron instalar los paquetes (apt)."

# --- Gradle (se comprueba contra la suma que publica Gradle)
if [ ! -x /opt/gradle/bin/gradle ]; then
	echo "Bajando Gradle $GRADLE_VER..."
	T=$(mktemp -d)
	curl -fL --retry 3 -o "$T/gradle.zip" "https://services.gradle.org/distributions/gradle-$GRADLE_VER-bin.zip" || fallo "No pude bajar Gradle"
	SUMA=$(curl -fsSL "https://services.gradle.org/distributions/gradle-$GRADLE_VER-bin.zip.sha256" | tr -dc 'a-f0-9')
	[ -n "$SUMA" ] && [ "$(sha256sum "$T/gradle.zip" | cut -d' ' -f1)" = "$SUMA" ] || fallo "Gradle bajó dañado (la suma no coincide)"
	unzip -q "$T/gradle.zip" -d "$T" && rm -rf /opt/gradle && mv "$T/gradle-$GRADLE_VER" /opt/gradle || fallo "No pude instalar Gradle"
	rm -rf "$T"
fi

# --- Android SDK + NDK
if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
	echo "Bajando las herramientas del Android SDK..."
	T=$(mktemp -d)
	curl -fL --retry 3 -o "$T/cmd.zip" "https://dl.google.com/android/repository/$CMDLINE" || fallo "No pude bajar el Android SDK"
	mkdir -p "$SDK/cmdline-tools"
	unzip -q "$T/cmd.zip" -d "$T" && rm -rf "$SDK/cmdline-tools/latest" && mv "$T/cmdline-tools" "$SDK/cmdline-tools/latest" || fallo "No pude instalar el Android SDK"
	rm -rf "$T"
fi
SDKM="$SDK/cmdline-tools/latest/bin/sdkmanager"
if [ ! -d "$SDK/platforms/android-34" ] || [ ! -d "$SDK/build-tools/34.0.0" ] || [ ! -d "$SDK/ndk/$NDK" ]; then
	echo "Instalando plataforma 34, build-tools y NDK (varios GB, tarda)..."
	yes | "$SDKM" --sdk_root="$SDK" --licenses >/dev/null 2>&1
	"$SDKM" --sdk_root="$SDK" "platforms;android-34" "build-tools;34.0.0" "ndk;$NDK" >/dev/null || fallo "No pude instalar el SDK/NDK"
fi

# --- copia del repo (con el submódulo del túnel)
if [ ! -d "$REPO_DIR/.git" ]; then
	echo "Clonando el repo en $REPO_DIR..."
	rm -rf "$REPO_DIR"
	URL="https://github.com/$GH_REPO.git"
	# si el token no sirve (venció) pero el repo es público, se intenta sin token
	gitc clone --quiet "$URL" "$REPO_DIR" 2>/dev/null || git clone --quiet "$URL" "$REPO_DIR" || fallo "No pude clonar $URL"
	# (en el subshell: si el token no sirve, el segundo intento va sin token)
	( cd "$REPO_DIR" && { gitc submodule update --init --recursive --quiet 2>/dev/null || { GH_TOKEN=""; gitc submodule update --init --recursive --quiet; }; } ) \
		|| fallo "No pude bajar el submódulo hev-socks5-tunnel"
else
	echo "La copia del repo ya está en $REPO_DIR."
	# (si la copió el instalador del bot, puede faltar el submódulo del túnel)
	( cd "$REPO_DIR" && { gitc submodule update --init --recursive --quiet 2>/dev/null || { GH_TOKEN=""; gitc submodule update --init --recursive --quiet; }; } ) \
		|| fallo "No pude bajar el submódulo hev-socks5-tunnel"
fi

echo "Comprobando..."
FALTA=$(cd /opt/zumo-bot 2>/dev/null && python3 -c "import compilar_vps as c; print(', '.join(c.faltantes()))" 2>&1)
if [ -z "$FALTA" ]; then
	echo "✔ Listo. En el bot: 📱 App Android → 🖥 Compilar en la VPS."
	echo "  La primera compilación baja las dependencias de Gradle y tarda más (puede pasar de 20 minutos)."
else
	echo "✘ Todavía falta: $FALTA"; exit 1
fi
