#!/bin/bash
# Endurece una VPS Zumo sin dejarte afuera: cada paso se explica y pide confirmación.
#   bash scripts/endurecer.sh [--centro | --panel] [--ver] [--si]
#
#   --centro  VPS centro (bot, compilador, instaladores): llave para root, firewall, fail2ban, actualizaciones.
#   --panel   VPS con panel de clientes: solo llave para root, actualizaciones y permisos. NO toca el firewall
#             (el panel abre puertos nuevos al activar protocolos) ni pone fail2ban (los clientes que entran
#             con usuario y clave desde datos móviles comparten IP y los bloquearía).
#   --ver     muestra qué haría, sin cambiar nada.
#   --si      no pregunta (para usarlo sin pantalla). Los pasos riesgosos se saltean si no hay seguridad.
# Sin --centro/--panel: si existe /opt/zumo-src es el centro, si no es una VPS con panel.
# Se puede repetir sin problema. Para deshacer: ver el resumen final.

SSHD_DROPIN="${ZUMO_SSHD_DROPIN:-/etc/ssh/sshd_config.d/zumo-endurecer.conf}"
AUTH_KEYS="${ZUMO_AUTHKEYS:-/root/.ssh/authorized_keys}"
ETC_ZUMO="${ZUMO_ETC:-/etc/zumo}"
AUTH_LOG="${ZUMO_AUTHLOG:-/var/log/auth.log}"

V='\e[1;38;5;141m'; G='\e[1;32m'; R='\e[1;31m'; Y='\e[1;33m'; N='\e[0m'
ok()    { echo -e " ${G}✔ $1${N}"; }
aviso() { echo -e " ${Y}! $1${N}"; }
paso()  { echo -e "\n${V}[$1] $2${N}"; }

# --- funciones puras (se prueban sin root: ver tests/prueba-endurecer.sh) -----------------------------
# Puertos que escuchan hacia afuera, a partir de la salida de `ss -H -ltnu` (entrada estándar): "22/tcp".
puertos_escuchando() {
	awk '{
		proto=$1; loc=$5
		if (loc ~ /^127\./ || loc ~ /^\[::1\]/ || loc ~ /^localhost/) next
		sub(/%[^:]*:/, ":", loc)
		n=split(loc, a, ":"); p=a[n]
		if (p ~ /^[0-9]+$/ && (proto=="tcp" || proto=="udp")) print p "/" proto
	}' | sort -u
}

# ¿Hay al menos una llave pública en el archivo?
tiene_llave() {
	[ -s "$1" ] && grep -Eq '^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-[a-z0-9]+|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com) ' "$1"
}

# Escribe el archivo de configuración de sshd que prohíbe la clave de root (solo llave).
escribir_dropin_ssh() {
	mkdir -p "$(dirname "$SSHD_DROPIN")" || return 1
	{
		echo "# Zumo: root solo entra con llave. Los clientes (usuarios del panel) siguen con usuario y clave."
		echo "PermitRootLogin prohibit-password"
	} > "$SSHD_DROPIN"
}

# Texto del archivo de actualizaciones automáticas (solo seguridad, sin reinicio).
texto_auto_upgrades() {
	echo 'APT::Periodic::Update-Package-Lists "1";'
	echo 'APT::Periodic::Unattended-Upgrade "1";'
}

# Si se está probando (ZUMO_SOLO_FUNCIONES=1) se termina acá: solo quedan definidas las funciones.
[ "${ZUMO_SOLO_FUNCIONES:-}" = 1 ] && return 0 2>/dev/null

# --- argumentos ---------------------------------------------------------------------------------------
MODO=""; VER=0; SI=0
for a in "$@"; do
	case "$a" in
		--centro) MODO=centro ;;
		--panel) MODO=panel ;;
		--ver) VER=1 ;;
		--si) SI=1 ;;
		-h|--help) sed -n '2,15p' "$0"; exit 0 ;;
		*) echo "Opción desconocida: $a"; exit 1 ;;
	esac
done
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
if [ -z "$MODO" ]; then [ -d /opt/zumo-src ] && MODO=centro || MODO=panel; fi
export DEBIAN_FRONTEND=noninteractive
CAMBIOS=()

confirmar() { # confirmar "qué se va a hacer" -> 0 si hay que hacerlo
	echo "   $1"
	if [ "$VER" = 1 ]; then echo "   (--ver: no se cambia nada)"; return 1; fi
	[ "$SI" = 1 ] && return 0
	local r; read -rp "   ¿Lo hago? [s/N]: " r </dev/tty
	[ "$r" = s ] || [ "$r" = S ]
}

echo -e "${V}╔══════════════════════════════════════╗\n║        ZUMO · ENDURECER LA VPS       ║\n╚══════════════════════════════════════╝${N}"
echo " Modo: $MODO$([ "$VER" = 1 ] && echo ' (solo ver)')"

# --- 1) root solo con llave ---------------------------------------------------------------------------
paso "1/5" "Root solo con llave SSH (los clientes siguen con usuario y clave)"
if [ -f "$SSHD_DROPIN" ]; then
	ok "ya está aplicado ($SSHD_DROPIN)"
elif ! tiene_llave "$AUTH_KEYS"; then
	aviso "root no tiene ninguna llave en $AUTH_KEYS: si lo activara, te quedarías afuera. No se toca."
	aviso "Primero poné tu llave pública (ssh-copy-id root@esta-vps desde tu compu, o pegala en ese archivo) y probá entrar con ella; después corré esto de nuevo."
else
	if ! grep -qs "Accepted publickey for root" "$AUTH_LOG" 2>/dev/null; then
		aviso "No vi ingresos recientes de root con llave. Antes de seguir, abrí OTRA conexión y comprobá que entrás con la llave."
	fi
	if confirmar "Se crea $SSHD_DROPIN con 'PermitRootLogin prohibit-password' y se recarga SSH. Esta conexión no se corta."; then
		if escribir_dropin_ssh && sshd -t 2>/dev/null; then
			systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null
			ok "root ya no entra con contraseña (solo con llave)"
			CAMBIOS+=("root solo con llave -> para deshacer: rm $SSHD_DROPIN && systemctl reload ssh")
		else
			rm -f "$SSHD_DROPIN"
			aviso "sshd rechazó la configuración: se deshizo, no se cambió nada."
		fi
	fi
fi

# --- 2) actualizaciones de seguridad automáticas -----------------------------------------------------
paso "2/5" "Actualizaciones de seguridad automáticas"
if [ -f /etc/apt/apt.conf.d/20auto-upgrades ] && grep -q 'Unattended-Upgrade "1"' /etc/apt/apt.conf.d/20auto-upgrades; then
	ok "ya están activadas"
elif confirmar "Se instala unattended-upgrades (solo parches de seguridad, sin reiniciar la VPS)."; then
	if apt-get install -y -qq unattended-upgrades >/dev/null 2>&1; then
		texto_auto_upgrades > /etc/apt/apt.conf.d/20auto-upgrades
		ok "actualizaciones de seguridad automáticas"
		CAMBIOS+=("actualizaciones automáticas -> para deshacer: rm /etc/apt/apt.conf.d/20auto-upgrades")
	else
		aviso "No se pudo instalar unattended-upgrades."
	fi
fi

# --- 3) permisos de /etc/zumo -------------------------------------------------------------------------
paso "3/5" "Permisos de $ETC_ZUMO (claves, token del bot, clave de firma)"
if [ -d "$ETC_ZUMO" ]; then
	if confirmar "Archivos con secretos solo legibles por root; el resto de usuarios no entra a la carpeta."; then
		chmod o-rwx "$ETC_ZUMO"
		find "$ETC_ZUMO" -maxdepth 3 -type f \( -name '*.env' -o -name 'claves.db' -o -name 'pass' -o -name '*.jks' -o -name 'codigos.json' -o -name '*.enc' \) -exec chmod 600 {} + 2>/dev/null
		ok "permisos cerrados"
	fi
else
	aviso "No existe $ETC_ZUMO: nada que hacer."
fi

# --- 4) firewall (solo el centro) --------------------------------------------------------------------
paso "4/5" "Firewall"
if [ "$MODO" != centro ]; then
	ok "se saltea en una VPS con panel: el panel abre puertos nuevos al activar protocolos y el firewall los bloquearía"
elif ufw status 2>/dev/null | grep -q "Status: active"; then
	ok "ufw ya está activo"
else
	PUERTOS=$(ss -H -ltnu 2>/dev/null | puertos_escuchando)
	if ! echo "$PUERTOS" | grep -q '/tcp'; then
		aviso "No pude leer los puertos abiertos (ss): no se toca el firewall."
	elif confirmar "ufw bloquea todo lo entrante salvo los puertos que hoy están en uso: $(echo $PUERTOS | tr '\n' ' ')"; then
		apt-get install -y -qq ufw >/dev/null 2>&1 || aviso "No se pudo instalar ufw."
		if command -v ufw >/dev/null; then
			ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
			for p in $PUERTOS; do ufw allow "$p" >/dev/null; done
			ufw --force enable >/dev/null && ok "firewall activo (puertos: $(echo $PUERTOS | tr '\n' ' '))"
			CAMBIOS+=("firewall -> para deshacer: ufw disable. Si agregás un servicio nuevo: ufw allow <puerto>/tcp")
		fi
	fi
fi

# --- 5) fail2ban (solo el centro) --------------------------------------------------------------------
paso "5/5" "fail2ban (bloquea a quien insiste con contraseñas en SSH)"
if [ "$MODO" != centro ]; then
	ok "se saltea en una VPS con panel: los clientes con datos móviles comparten IP y los bloquearía"
elif [ -f /etc/fail2ban/jail.d/zumo.local ]; then
	ok "ya está configurado"
elif confirmar "Se instala fail2ban: 5 intentos fallidos en 10 minutos bloquean esa IP 1 hora."; then
	if apt-get install -y -qq fail2ban >/dev/null 2>&1; then
		mkdir -p /etc/fail2ban/jail.d
		printf '[sshd]\nenabled = true\nmaxretry = 5\nfindtime = 10m\nbantime = 1h\n' > /etc/fail2ban/jail.d/zumo.local
		systemctl enable --now fail2ban >/dev/null 2>&1; systemctl restart fail2ban >/dev/null 2>&1
		ok "fail2ban activo"
		CAMBIOS+=("fail2ban -> para deshacer: systemctl disable --now fail2ban; rm /etc/fail2ban/jail.d/zumo.local")
	else
		aviso "No se pudo instalar fail2ban."
	fi
fi

echo
if [ "${#CAMBIOS[@]}" -gt 0 ]; then
	echo -e "${V}Resumen de lo que cambió y cómo deshacerlo:${N}"
	for c in "${CAMBIOS[@]}"; do echo "  • $c"; done
else
	echo " No cambió nada."
fi
[ "$MODO" = centro ] && echo " Falta (a mano): revocar el token del bot en @BotFather si se mostró en alguna captura, y exportar la clave de firma desde el bot (💾 Respaldo)."
exit 0
