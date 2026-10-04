#!/bin/bash
# Actualiza una instalación Zumo existente con la última versión del repo:
# panel de terminal, librería compartida y limitador (recompilado).
# No reinstala los protocolos; solo actualiza lo que cambió.
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }

BASE="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main"
NC="?nocache=$(date +%s)"
V='\e[1;38;5;141m'; G='\e[1;32m'; R='\e[1;31m'; N='\e[0m'
ok()  { echo -e " ${G}✔ $1${N}"; }
err() { echo -e " ${R}✘ $1${N}"; }

mkdir -p /etc/zumo

# 1) Librería compartida -------------------------------------------------------
echo -e "${V}[1/4] Librería compartida (zumo-lib.sh)...${N}"
TMP=$(mktemp)
if curl -fsSL "$BASE/zumo-lib.sh$NC" -o "$TMP" && bash -n "$TMP" 2>/dev/null; then
	install -m 0644 "$TMP" /etc/zumo/zumo-lib.sh
	ok "actualizada"
else
	err "no se pudo descargar/validar zumo-lib.sh"; rm -f "$TMP"; exit 1
fi
rm -f "$TMP"

# 2) Panel de terminal ---------------------------------------------------------
echo -e "${V}[2/4] Panel de terminal (zumo)...${N}"
TMP=$(mktemp)
if curl -fsSL "$BASE/panel.sh$NC" -o "$TMP" && bash -n "$TMP" 2>/dev/null; then
	[ -f /usr/local/bin/zumo ] && cp /usr/local/bin/zumo /usr/local/bin/zumo.bak
	install -m 0755 "$TMP" /usr/local/bin/zumo
	ok "actualizado (backup: /usr/local/bin/zumo.bak)"
else
	err "no se pudo descargar/validar panel.sh"; rm -f "$TMP"; exit 1
fi
rm -f "$TMP"

# 3) Limitador (se baja zumo-limit.c del repo y se recompila) ------------------
echo -e "${V}[3/4] Limitador (zumo-limit)...${N}"
if ! command -v gcc >/dev/null 2>&1; then
	export DEBIAN_FRONTEND=noninteractive
	apt-get update >/dev/null 2>&1
	apt-get install -y --no-install-recommends gcc libc6-dev >/dev/null 2>&1
fi
TMP=$(mktemp -d)
if curl -fsSL "$BASE/zumo-limit.c$NC" -o "$TMP/zumo-limit.c" && [ -s "$TMP/zumo-limit.c" ]; then
	if gcc -O2 -o "$TMP/zumo-limit" "$TMP/zumo-limit.c" 2>"$TMP/err.log"; then
		systemctl stop zumo-limit 2>/dev/null
		install -m 0755 "$TMP/zumo-limit" /usr/local/bin/zumo-limit
		# Unidad al día (reinicio rápido) y configuración, sin pisar la que ya tenés.
		if [ -f /etc/systemd/system/zumo-limit.service ] && ! grep -q '^RestartSec=' /etc/systemd/system/zumo-limit.service; then
			sed -i 's/^Restart=always$/Restart=always\nRestartSec=2/' /etc/systemd/system/zumo-limit.service
			systemctl daemon-reload 2>/dev/null
		fi
		[ -f /etc/zumo/limit.conf ] || curl -fsSL "$BASE/limit.conf$NC" -o /etc/zumo/limit.conf 2>/dev/null || true
		systemctl start zumo-limit 2>/dev/null
		if systemctl is-active --quiet zumo-limit; then ok "recompilado y reiniciado"; else err "recompilado pero no quedó activo (journalctl -u zumo-limit)"; fi
	else
		err "no se pudo compilar el limitador:"; sed 's/^/   /' "$TMP/err.log"
	fi
else
	err "no se pudo descargar zumo-limit.c del repo (¿ya lo subiste?)"
fi
# install.sh se baja aparte: el paso 5 extrae de ahí los activadores de protocolos.
curl -fsSL "$BASE/install.sh$NC" -o "$TMP/install.sh" 2>/dev/null || rm -f "$TMP/install.sh"

# 4) Refrescar los activadores de protocolos (traen el código fuente embebido:
#    pdirect, etc.). Sin esto, reactivar un protocolo recompila la versión vieja.
echo -e "${V}[4/4] Activadores de protocolos...${N}"
if [ -f "$TMP/install.sh" ]; then
extraer_bloque() {
# $1=marcador del heredoc  $2=archivo de salida
awk -v m="$1" 'index($0,"<<\x27"m"\x27"){f=1;next} f&&$0==m{f=0;next} f' "$TMP/install.sh"
}
refrescar() {
# $1=nombre  $2=marcador
local out="/etc/zumo/$1" tmp; tmp=$(mktemp)
extraer_bloque "$2" > "$tmp"
if [ -s "$tmp" ] && bash -n "$tmp" 2>/dev/null; then
install -m 0755 "$tmp" "$out"; echo -e "   ${G}✔${N} $1"
else
echo -e "   ${R}✘${N} $1 (no se pudo extraer/validar, se dejó el actual)"
fi
rm -f "$tmp"
}
refrescar activar-pdirect.sh   ZUMOPDIRECTACT
refrescar desactivar-pdirect.sh DESPDEOF
refrescar activar-bhttp.sh     ZUMOBHTTPACT
refrescar desactivar-bhttp.sh  DESBHTTPEOF
refrescar activar-hcr.sh       ZUMOHCRACT
refrescar desactivar-hcr.sh    DESHCREOF
refrescar activar-badvpn.sh    ZUMOBADVPNACT
refrescar desactivar-badvpn.sh DESBVEOF
refrescar borrar-temporal.sh   BORRARTEMP
echo -e " \e[2mReactivá los protocolos (PDirect, etc.) para recompilar con la versión nueva.${N}"
else
err "no se pudo refrescar los activadores (sin install.sh)"
fi
rm -rf "$TMP"

if systemctl list-unit-files 2>/dev/null | grep -q '^zumo-web.service'; then
	echo -e " ${V}—${N} El panel web ya no forma parte de Zumo y no se actualiza. Para quitarlo de esta VPS:"
	echo -e "   curl -fsSL \"$BASE/quitar-panelweb.sh\" | bash"
fi

echo
ok "Actualización terminada. Abrí el panel con: zumo"
