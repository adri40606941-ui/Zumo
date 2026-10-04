#!/bin/bash
# Limitador de 1 sesión SSH por usuario (revisa cada 3 segundos).
# Pensado para VPS con SSHPlus: reemplaza a /opt/sshplus/limiter.
#
# Instalar:     bash limitador-1sesion.sh
# Desinstalar:  bash limitador-1sesion.sh --desinstalar
#
# Por defecto conserva la sesión MÁS NUEVA y corta las demás, para que un
# usuario que cambia de red (WiFi <-> datos) pueda reconectar al instante.
# Si dos dispositivos comparten cuenta, se van cortando uno al otro.
# Para conservar la más vieja: MANTENER=vieja en /etc/default/limitador1.

V='\e[1;32m'; R='\e[1;31m'; N='\e[0m'
BIN=/usr/local/bin/limitador1
SVC=/etc/systemd/system/limitador1.service
CONF=/etc/default/limitador1

[ "$(id -u)" = 0 ] || { echo -e "${R}Ejecutalo como root${N}"; exit 1; }

if [ "$1" = "--desinstalar" ]; then
	systemctl disable --now limitador1 >/dev/null 2>&1
	rm -f "$BIN" "$SVC" "$CONF"
	systemctl daemon-reload
	# devolver el limitador de SSHPlus si lo habíamos desactivado
	if [ -f /etc/autostart.bak-limitador1 ]; then
		cp /etc/autostart.bak-limitador1 /etc/autostart
		echo -e "${V}✔ /etc/autostart restaurado (vuelve el limitador de SSHPlus)${N}"
	fi
	echo -e "${V}✔ limitador1 desinstalado${N}"
	exit 0
fi

cat > "$BIN" <<'EOF'
#!/bin/bash
# limitador1: 1 sesión SSH por usuario. Log en /var/log/limitador1.log
MANTENER=nueva
INTERVALO=3
[ -f /etc/default/limitador1 ] && . /etc/default/limitador1
LOG=/var/log/limitador1.log

while :; do
	# Procesos de sesión de sshd que corren con el uid del usuario
	# ("sshd: pepe" / "sshd: pepe@notty"; en OpenSSH nuevo "sshd-session:").
	# El proceso [priv] es de root y queda afuera por el filtro uid>=1000.
	ps -eo pid=,uid=,etimes=,args= | awk -v keep="$MANTENER" '
		$2 >= 1000 && $4 ~ /^sshd(-session)?:$/ {
			u = $5; sub(/@.*/, "", u)
			n[u]++; pid[u, n[u]] = $1; age[u, n[u]] = $3
		}
		END {
			for (u in n) {
				if (n[u] < 2) continue
				k = 1
				for (i = 2; i <= n[u]; i++)
					if ((keep == "vieja" && age[u,i] > age[u,k]) ||
					    (keep != "vieja" && age[u,i] < age[u,k])) k = i
				for (i = 1; i <= n[u]; i++)
					if (i != k) print pid[u,i], u
			}
		}' | while read -r p u; do
		kill -9 "$p" 2>/dev/null &&
			echo "$(date '+%F %T') cortada sesión extra de $u (pid $p)" >> "$LOG"
	done
	sleep "$INTERVALO"
done
EOF
chmod 0755 "$BIN"

[ -f "$CONF" ] || printf 'MANTENER=nueva   # nueva | vieja\nINTERVALO=3\n' > "$CONF"

cat > "$SVC" <<'EOF'
[Unit]
Description=Limitador de 1 sesion SSH por usuario
After=network.target
[Service]
ExecStart=/usr/local/bin/limitador1
Restart=always
[Install]
WantedBy=multi-user.target
EOF

# Desactivar el limitador de SSHPlus para que no corran dos a la vez
if [ -f /etc/autostart ] && grep -q 'sshplus/limiter' /etc/autostart; then
	[ -f /etc/autostart.bak-limitador1 ] || cp /etc/autostart /etc/autostart.bak-limitador1
	sed -i '/sshplus\/limiter/d' /etc/autostart
	echo -e "${V}✔ limitador de SSHPlus quitado de /etc/autostart (copia en /etc/autostart.bak-limitador1)${N}"
fi
pkill -f /opt/sshplus/limiter 2>/dev/null
screen -S limiter -X quit >/dev/null 2>&1

systemctl daemon-reload
systemctl enable --now limitador1 >/dev/null 2>&1
if systemctl is-active --quiet limitador1; then
	echo -e "${V}✔ limitador1 activo: 1 sesión por usuario, revisa cada 3 s${N}"
	echo "  Log:    tail -f /var/log/limitador1.log"
	echo "  Config: $CONF"
else
	echo -e "${R}✘ no quedó activo, mirá: journalctl -u limitador1${N}"
	exit 1
fi
