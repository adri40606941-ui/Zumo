#!/bin/bash
# Contador de datos por usuario (se instala como /usr/local/bin/zumo-datos).
# Mide cuántos bytes mueve cada sesión SSH (proceso sshd del usuario, leyendo
# /proc/PID/io) y los suma en /etc/zumo/datos.db (usuario:bytes). Cada byte del
# túnel se lee y se escribe dos veces dentro de sshd (celular↔sshd y sshd↔internet),
# por eso el total se divide por 2: queda bajada + subida del usuario.
# Cuenta desde que se crea el usuario (o desde que se instaló esto, si ya existía).
DB="${ZUMO_DB:-/etc/zumo/usuarios.db}"
DATOS="${ZUMO_DATOS:-/etc/zumo/datos.db}"
LOCK="${ZUMO_DATOS_LOCK:-/etc/zumo/datos.lock}"
PASSWD="${ZUMO_PASSWD:-/etc/passwd}"
INT="${INTERVAL:-2}"

# Migración: la versión anterior (iptables) medía mal; se borra su cuenta y sus reglas.
if [ ! -e "$DATOS.v2" ]; then
	rm -f "$DATOS"; : > "$DATOS.v2"
	if command -v iptables >/dev/null 2>&1; then
		iptables -w -D OUTPUT -j ZUMO_DATOS 2>/dev/null
		iptables -w -F ZUMO_DATOS 2>/dev/null; iptables -w -X ZUMO_DATOS 2>/dev/null
	fi
fi

declare -A UIDNAME LAST
primera=1; n=0

mapa() {
	local -A en=(); local u nom uid
	UIDNAME=()
	while IFS=: read -r u _; do [ -n "$u" ] && en[$u]=1; done < "$DB"
	while IFS=: read -r nom _ uid _; do [ -n "${en[$nom]:-}" ] && UIDNAME[$uid]=$nom; done < "$PASSWD"
}

guardar() { # $1 = líneas "usuario bytes_a_sumar" (puede ir vacío)
	(
		flock -w 5 9 || exit 0
		touch "$DATOS"
		awk -v db="$DB" '
			BEGIN { while ((getline l < db) > 0) { split(l, f, ":"); ok[f[1]]=1 } }
			NR==FNR { if ($1!="") add[$1]+=$2; next }
			{ split($0, f, ":"); if (!(f[1] in ok)) next; tot[f[1]]=f[2]; vis[f[1]]=1; ord[++c]=f[1] }
			END {
				for (u in add) if (u in ok && !(u in vis)) { ord[++c]=u; tot[u]=0 }
				for (i=1;i<=c;i++) { u=ord[i]; printf "%s:%.0f\n", u, tot[u]+add[u] }
			}' <(printf '%s\n' "$1") "$DATOS" > "$DATOS.tmp" && mv -f "$DATOS.tmp" "$DATOS"
	) 9>"$LOCK"
}

ciclo() {
	local c p nombre uid u rc wc k v linea resto ini cur d lista
	local -A ADD=() VISTO=()
	mapa
	for c in /proc/[0-9]*/comm; do
		p=${c#/proc/}; p=${p%/comm}
		read -r nombre 2>/dev/null < "$c" || continue
		case $nombre in sshd*) ;; *) continue ;; esac
		uid=""
		while read -r k v _; do [ "$k" = "Uid:" ] && { uid=$v; break; }; done 2>/dev/null < "/proc/$p/status"
		u=${UIDNAME[$uid]:-}; [ -n "$u" ] || continue
		rc=""; wc=""
		while read -r k v; do case $k in rchar:) rc=$v ;; wchar:) wc=$v ;; esac; done 2>/dev/null < "/proc/$p/io"
		[ -n "$rc" ] && [ -n "$wc" ] || continue
		read -r linea 2>/dev/null < "/proc/$p/stat" || continue
		resto=${linea##*) }; set -- $resto; ini=${20:-0}
		k="$p:$ini"; cur=$((rc + wc)); VISTO[$k]=1
		if [ -n "${LAST[$k]:-}" ]; then d=$((cur - LAST[$k]))
		elif [ "$primera" = 1 ]; then d=0
		else d=$cur; fi
		LAST[$k]=$cur
		[ "$d" -gt 0 ] && ADD[$u]=$(( ${ADD[$u]:-0} + d ))
	done
	for k in "${!LAST[@]}"; do [ -n "${VISTO[$k]:-}" ] || unset 'LAST[$k]'; done
	primera=0
	lista=""
	for u in "${!ADD[@]}"; do lista+="$u $(( ADD[$u] / 2 ))"$'\n'; done
	n=$((n + 1))
	# Se escribe si hubo tráfico, y de vez en cuando para limpiar usuarios borrados.
	if [ -n "$lista" ] || [ $((n % 15)) -eq 0 ]; then guardar "$lista"; fi
}

if [ "${1:-}" = "--once" ]; then ciclo; exit 0; fi
while true; do ciclo; sleep "$INT"; done
