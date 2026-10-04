#!/bin/bash
# Actualiza una instalación Zumo existente con la última versión del repo:
# panel de terminal, librería compartida, panel web y limitador (recompilado).
# No reinstala los protocolos; solo actualiza lo que cambió.
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }

BASE="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main"
NC="?nocache=$(date +%s)"
V='\e[1;38;5;141m'; G='\e[1;32m'; R='\e[1;31m'; N='\e[0m'
ok()  { echo -e " ${G}✔ $1${N}"; }
err() { echo -e " ${R}✘ $1${N}"; }

mkdir -p /etc/zumo

# 1) Librería compartida -------------------------------------------------------
echo -e "${V}[1/5] Librería compartida (zumo-lib.sh)...${N}"
TMP=$(mktemp)
if curl -fsSL "$BASE/zumo-lib.sh$NC" -o "$TMP" && bash -n "$TMP" 2>/dev/null; then
	install -m 0644 "$TMP" /etc/zumo/zumo-lib.sh
	ok "actualizada"
else
	err "no se pudo descargar/validar zumo-lib.sh"; rm -f "$TMP"; exit 1
fi
rm -f "$TMP"

# 2) Panel de terminal ---------------------------------------------------------
echo -e "${V}[2/5] Panel de terminal (zumo)...${N}"
TMP=$(mktemp)
if curl -fsSL "$BASE/panel.sh$NC" -o "$TMP" && bash -n "$TMP" 2>/dev/null; then
	[ -f /usr/local/bin/zumo ] && cp /usr/local/bin/zumo /usr/local/bin/zumo.bak
	install -m 0755 "$TMP" /usr/local/bin/zumo
	ok "actualizado (backup: /usr/local/bin/zumo.bak)"
else
	err "no se pudo descargar/validar panel.sh"; rm -f "$TMP"; exit 1
fi
rm -f "$TMP"

# 3) Panel web (solo si ya estaba instalado) -----------------------------------
echo -e "${V}[3/5] Panel web (panelweb.py)...${N}"
if [ -f /etc/zumo/panelweb.py ] || systemctl list-unit-files 2>/dev/null | grep -q '^zumo-web.service'; then
	TMP=$(mktemp)
	if curl -fsSL "$BASE/panelweb.py$NC" -o "$TMP" && python3 -m py_compile "$TMP" 2>/dev/null; then
		cp /etc/zumo/panelweb.py /etc/zumo/panelweb.py.bak 2>/dev/null
		install -m 0644 "$TMP" /etc/zumo/panelweb.py
		systemctl restart zumo-web 2>/dev/null && ok "actualizado y reiniciado" || ok "actualizado (no estaba corriendo)"
	else
		err "no se pudo descargar/validar panelweb.py (se dejó el actual)"
	fi
	rm -f "$TMP"
else
	echo -e " ${V}—${N} no está instalado, se omite"
fi

# 4) Limitador (recompilar desde el C embebido en install.sh) ------------------
echo -e "${V}[4/5] Limitador (zumo-limit)...${N}"
if ! command -v gcc >/dev/null 2>&1; then
	export DEBIAN_FRONTEND=noninteractive
	apt-get update >/dev/null 2>&1
	apt-get install -y --no-install-recommends gcc libc6-dev >/dev/null 2>&1
fi
TMP=$(mktemp -d)
if curl -fsSL "$BASE/install.sh$NC" -o "$TMP/install.sh"; then
	awk "/ZUMO_LIMIT_C'/{f=1;next} /^ZUMO_LIMIT_C\$/{f=0} f" "$TMP/install.sh" > "$TMP/zumo-limit.c"
	if [ -s "$TMP/zumo-limit.c" ] && gcc -O2 -o "$TMP/zumo-limit" "$TMP/zumo-limit.c" 2>"$TMP/err.log"; then
		systemctl stop zumo-limit 2>/dev/null
		install -m 0755 "$TMP/zumo-limit" /usr/local/bin/zumo-limit
		systemctl start zumo-limit 2>/dev/null
		if systemctl is-active --quiet zumo-limit; then ok "recompilado y reiniciado"; else err "recompilado pero no quedó activo (journalctl -u zumo-limit)"; fi
	else
		err "no se pudo compilar el limitador:"; sed 's/^/   /' "$TMP/err.log"
	fi
else
	err "no se pudo descargar install.sh para recompilar el limitador"
fi

# 5) Refrescar los activadores de protocolos (traen el código fuente embebido:
#    pdirect, etc.). Sin esto, reactivar un protocolo recompila la versión vieja.
echo -e "${V}[5/5] Activadores de protocolos...${N}"
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

echo
ok "Actualización terminada. Abrí el panel con: zumo"
