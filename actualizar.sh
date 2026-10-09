#!/bin/bash
# Actualiza una instalación Zumo existente con la última versión del repo:
# panel de terminal, librería compartida y limitador (recompilado).
# No reinstala los protocolos; solo actualiza lo que cambió.
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
# Si se instala/actualiza desde otra dirección (ZUMO_BASE), se recuerda para las próximas veces.
if [ -n "${ZUMO_BASE:-}" ]; then case "$ZUMO_BASE" in https://*) mkdir -p /etc/zumo; printf '%s' "${ZUMO_BASE%/}" > /etc/zumo/base.url ;; esac; fi

BASE="${ZUMO_BASE:-$(cat /etc/zumo/base.url 2>/dev/null || echo https://raw.githubusercontent.com/adri40606941-ui/Zumo/main)}"
NC="?nocache=$(date +%s)"

# Comando "zumo-actualizar": actualiza esta VPS desde la misma dirección de donde se instaló.
cat > /usr/local/bin/zumo-actualizar <<'ZACT'
#!/bin/bash
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }
B="${ZUMO_BASE:-$(cat /etc/zumo/base.url 2>/dev/null || echo https://raw.githubusercontent.com/adri40606941-ui/Zumo/main)}"
T=$(mktemp) || exit 1
if ! curl -fsSL "$B/actualizar.sh?nocache=$(date +%s)" -o "$T" || ! bash -n "$T" 2>/dev/null; then
	echo "No se pudo bajar actualizar.sh desde $B"; rm -f "$T"; exit 1
fi
bash "$T"; r=$?; rm -f "$T"; exit $r
ZACT
chmod 755 /usr/local/bin/zumo-actualizar
V='\e[1;38;5;141m'; G='\e[1;32m'; R='\e[1;31m'; N='\e[0m'
ok()  { echo -e " ${G}✔ $1${N}"; }
err() { echo -e " ${R}✘ $1${N}"; }

# Hora de la VPS en Buenos Aires (los vencimientos y la hora del panel salen de acá).
ZUMO_TZ=America/Argentina/Buenos_Aires
if [ "$(timedatectl show -p Timezone --value 2>/dev/null)" != "$ZUMO_TZ" ]; then
[ -e "/usr/share/zoneinfo/$ZUMO_TZ" ] || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata >/dev/null 2>&1
timedatectl set-timezone "$ZUMO_TZ" 2>/dev/null || { ln -sf "/usr/share/zoneinfo/$ZUMO_TZ" /etc/localtime; echo "$ZUMO_TZ" > /etc/timezone; }
fi
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

# Vencimientos a las 21:00: la cuenta de Linux vence un día después y el corte exacto
# lo hace el limitador (EXPIRE_HOUR en /etc/zumo/limit.conf).
if [ -s /etc/zumo/usuarios.db ]; then
while IFS=: read -r u _ e; do
[ -n "$u" ] && id "$u" >/dev/null 2>&1 && [[ "$e" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && usermod -e "$(date -d "$e +1 day" +%F)" "$u" >/dev/null 2>&1
done < /etc/zumo/usuarios.db
fi

# Los usuarios HWID tienen límite 2 por defecto: una sola vez, los que estaban en 1 pasan a 2.
if [ -s /etc/zumo/usuarios.db ] && [ ! -f /etc/zumo/.hwid-limite2 ]; then
( . /etc/zumo/zumo-lib.sh
while IFS=: read -r u l _; do
[ "$l" = "1" ] || continue
case "$(getent passwd "$u" 2>/dev/null | cut -d: -f5)" in hwid,*) zumo_db_set "$u" 2 2 ;; esac
done < /etc/zumo/usuarios.db ) 2>/dev/null
touch /etc/zumo/.hwid-limite2
fi

# SSH: aceptar muchas conexiones nuevas a la vez (si no, tras reiniciar PDirect sshd rechaza
# a los clientes que reconectan juntos). Recargar sshd no corta las sesiones abiertas.
MS_CONF=/etc/ssh/sshd_config.d/zumo-maxstartups.conf
if [ -f /etc/ssh/sshd_config ] && ! sshd -T 2>/dev/null | grep -qx 'maxstartups 500:30:1000'; then
	if grep -qE '^Include[[:space:]]+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config; then
		mkdir -p /etc/ssh/sshd_config.d
		echo "MaxStartups 500:30:1000" > "$MS_CONF"
		sshd -t 2>/dev/null || rm -f "$MS_CONF"
	else
		_t=$(mktemp); cp /etc/ssh/sshd_config "$_t"; sed -i '/^MaxStartups/d' "$_t"; echo "MaxStartups 500:30:1000" >> "$_t"
		sshd -t -f "$_t" 2>/dev/null && cp "$_t" /etc/ssh/sshd_config
		rm -f "$_t"
	fi
	if sshd -T 2>/dev/null | grep -qx 'maxstartups 500:30:1000'; then
		systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null
		ok "sshd acepta más conexiones a la vez (MaxStartups)"
	else
		err "no se pudo ajustar MaxStartups de sshd"
	fi
fi

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
if curl -fsSL "$BASE/fuentes/zumo-limit.c$NC" -o "$TMP/zumo-limit.c" && [ -s "$TMP/zumo-limit.c" ]; then
	if gcc -O2 -o "$TMP/zumo-limit" "$TMP/zumo-limit.c" 2>"$TMP/err.log"; then
		systemctl stop zumo-limit 2>/dev/null
		install -m 0755 "$TMP/zumo-limit" /usr/local/bin/zumo-limit
		# Si la VPS no tenía la unidad del limitador (instalación vieja o incompleta), se crea.
		if [ ! -f /etc/systemd/system/zumo-limit.service ]; then
			cat > /etc/systemd/system/zumo-limit.service <<'SVCEOF'
[Unit]
Description=ZUMO limitador de conexiones (1 sesion por usuario, revisa cada 3 s)
After=network.target
[Service]
ExecStart=/usr/local/bin/zumo-limit
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
SVCEOF
			systemctl daemon-reload 2>/dev/null
			systemctl enable zumo-limit >/dev/null 2>&1
		fi
		# Unidad al día (reinicio rápido) y configuración, sin pisar la que ya tenés.
		if [ -f /etc/systemd/system/zumo-limit.service ] && ! grep -q '^RestartSec=' /etc/systemd/system/zumo-limit.service; then
			sed -i 's/^Restart=always$/Restart=always\nRestartSec=2/' /etc/systemd/system/zumo-limit.service
			systemctl daemon-reload 2>/dev/null
		fi
		[ -f /etc/zumo/limit.conf ] || curl -fsSL "$BASE/config/limit.conf$NC" -o /etc/zumo/limit.conf 2>/dev/null || true
		systemctl start zumo-limit 2>/dev/null
		if systemctl is-active --quiet zumo-limit; then ok "recompilado y reiniciado"; else err "recompilado pero no quedó activo (journalctl -u zumo-limit)"; fi
	else
		err "no se pudo compilar el limitador:"; sed 's/^/   /' "$TMP/err.log"
	fi
else
	err "no se pudo descargar zumo-limit.c del repo (¿ya lo subiste?)"
fi
# Contador de datos por usuario
echo -e "${V}Contador de datos (zumo-datos)...${N}"

# Contador de datos por usuario (iptables owner + datos.db)
if curl -fsSL "$BASE/scripts/zumo-datos.sh$NC" -o /tmp/zumo-datos.sh && bash -n /tmp/zumo-datos.sh; then
install -m 0755 /tmp/zumo-datos.sh /usr/local/bin/zumo-datos
cat > /etc/systemd/system/zumo-datos.service <<'DATEOF'
[Unit]
Description=ZUMO contador de datos por usuario
After=network.target
[Service]
ExecStart=/usr/local/bin/zumo-datos
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
DATEOF
systemctl daemon-reload >/dev/null 2>&1
systemctl enable zumo-datos >/dev/null 2>&1
systemctl restart zumo-datos >/dev/null 2>&1
echo -e " ${G}✔ contador de datos activo${N}"
else
echo -e " ${R}✘ no se pudo bajar zumo-datos.sh${N}"
fi
rm -f /tmp/zumo-datos.sh

# BadVPN (si está instalado): tope de memoria y de conexiones UDP por cliente. Cada conexión UDP
# abierta guarda ~250 KB y no se cierra sola hasta llegar al tope, así que con muchos clientes y un
# tope alto (64/128) la memoria de BadVPN pasaba de 1 GB. Se respeta lo que hayas elegido desde el
# panel (/etc/zumo/badvpn.conf); solo se cambia el valor viejo por defecto (64) si nunca lo tocaste.
BVU=/etc/systemd/system/udpgw-7300.service
if [ -f "$BVU" ]; then
	BV_CAMBIO=0
	if [ "$(systemctl show udpgw-7300 -p MemoryMax --value 2>/dev/null)" = "infinity" ]; then
		mkdir -p /etc/systemd/system/udpgw-7300.service.d
		printf '[Service]\nMemoryHigh=900M\nMemoryMax=1G\n' > /etc/systemd/system/udpgw-7300.service.d/zumo-memoria.conf
		BV_CAMBIO=1
	fi
	BV_CC=$(sed -n 's/.*--max-connections-for-client \([0-9]\+\).*/\1/p' "$BVU" | head -n1)
	if [ ! -f /etc/zumo/badvpn.conf ] && [ "$BV_CC" = "64" ]; then
		sed -i 's/--max-connections-for-client [0-9]*/--max-connections-for-client 16/' "$BVU"
		BV_CAMBIO=1
	fi
	if [ "$BV_CAMBIO" = 1 ]; then
		systemctl daemon-reload 2>/dev/null
		systemctl is-active --quiet udpgw-7300 && systemctl restart udpgw-7300
		ok "BadVPN: tope de memoria y conexiones por cliente al día (los clientes reconectan solos)"
	fi
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
PD_ANTES=$(sha256sum /etc/zumo/activar-pdirect.sh 2>/dev/null | cut -d' ' -f1)
refrescar activar-pdirect.sh   ZUMOPDIRECTACT
refrescar desactivar-pdirect.sh DESPDEOF
refrescar activar-bhttp.sh     ZUMOBHTTPACT
refrescar desactivar-bhttp.sh  DESBHTTPEOF
refrescar activar-bhttp2.sh    ZUMOBHTTP2ACT
refrescar desactivar-bhttp2.sh DESBHTTP2EOF
refrescar activar-hcr.sh       ZUMOHCRACT
refrescar desactivar-hcr.sh    DESHCREOF
refrescar activar-badvpn.sh    ZUMOBADVPNACT
refrescar desactivar-badvpn.sh DESBVEOF
refrescar borrar-temporal.sh   BORRARTEMP
# PDirect: se recompila y reinicia SOLO si cambió su código. Reiniciarlo corta a todos los
# clientes conectados, así que si no hay cambios no se toca (conserva banner/color/modo).
PD_AHORA=$(sha256sum /etc/zumo/activar-pdirect.sh 2>/dev/null | cut -d' ' -f1)
PD_BASE=$(cat /etc/zumo/.pdirect.sha 2>/dev/null); PD_BASE=${PD_BASE:-$PD_ANTES}
if systemctl is-active --quiet pdirect-80 2>/dev/null; then
	if [ -n "$PD_AHORA" ] && [ "$PD_AHORA" = "$PD_BASE" ]; then
		ok "PDirect sin cambios: no se reinicia (los clientes siguen conectados)"
		[ -f /etc/zumo/.pdirect.sha ] || echo "$PD_AHORA" > /etc/zumo/.pdirect.sha
	else
		echo -e "${V}Recompilando PDirect (cambió su código; se corta a los conectados un momento)...${N}"
		if bash /etc/zumo/activar-pdirect.sh >/dev/null 2>&1 && systemctl is-active --quiet pdirect-80; then ok "PDirect recompilado y reiniciado"
		else err "PDirect no quedó activo: reactivalo desde el panel (o bash /etc/zumo/activar-pdirect.sh)"; fi
	fi
fi
echo -e " \e[2mLos demás protocolos (BHTTP, HCR, etc.) se reactivan desde el panel si querés actualizarlos.${N}"
else
err "no se pudo refrescar los activadores (sin install.sh)"
fi
rm -rf "$TMP"

if systemctl list-unit-files 2>/dev/null | grep -q '^zumo-web.service'; then
	echo -e " ${V}—${N} El panel web ya no forma parte de Zumo y no se actualiza. Para quitarlo de esta VPS:"
	echo -e "   curl -fsSL \"$BASE/scripts/quitar-panelweb.sh\" | bash"
fi

# El control de dispositivo por Android ID ya no forma parte de Zumo. Si una versión anterior lo
# instaló, se apaga y se borra: si quedara andando, cortaría a los usuarios que estaban vinculados.
if [ -f /etc/systemd/system/zumo-id.service ] || [ -x /usr/local/bin/zumoid ]; then
	systemctl disable --now zumo-id >/dev/null 2>&1
	rm -f /etc/systemd/system/zumo-id.service /usr/local/bin/zumoid /etc/zumo/activar-zumoid.sh
	systemctl daemon-reload 2>/dev/null
	ok "control de Android ID quitado"
fi

# Baneo automático de IP que fallan la contraseña en el puerto 22 (PDirect/loopback nunca se banea).
_bn=$(mktemp)
if curl -fsSL "$BASE/scripts/zumo-baneo.sh$NC" -o "$_bn" && bash -n "$_bn" 2>/dev/null; then
	if _r=$(bash "$_bn" 2>&1); then ok "$_r"; else err "baneo automático: $_r"; fi
fi
rm -f "$_bn"

echo
ok "Actualización terminada. Abrí el panel con: zumo"
