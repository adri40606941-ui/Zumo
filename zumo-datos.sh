#!/bin/bash
# Contador de datos por usuario (se instala como /usr/local/bin/zumo-datos).
# Cuenta con iptables (módulo owner) lo que mueven las sesiones SSH de cada
# usuario de usuarios.db y lo va sumando en /etc/zumo/datos.db (usuario:bytes).
# Mide lo que sale de las sesiones del usuario: hacia su celular (bajada) y hacia
# internet (subida), o sea el tráfico total del usuario. Cuenta desde que se
# crea el usuario (o desde que se instaló esto, si el usuario ya existía).
DB="${ZUMO_DB:-/etc/zumo/usuarios.db}"
DATOS="${ZUMO_DATOS:-/etc/zumo/datos.db}"
LOCK="${ZUMO_DATOS_LOCK:-/etc/zumo/datos.lock}"
CH=ZUMO_DATOS
INT="${INTERVAL:-10}"
ipt() { iptables -w "$@"; }

preparar() {
	ipt -N "$CH" 2>/dev/null
	ipt -C OUTPUT -j "$CH" 2>/dev/null || ipt -I OUTPUT 1 -j "$CH"
}

# Un ciclo: crea las reglas que falten, borra las de usuarios que ya no están,
# lee y pone a cero los contadores, y suma lo leído a datos.db.
ciclo() {
	preparar
	local u uid want have key
	want=$(while IFS=: read -r u _; do
		[ -n "$u" ] || continue
		uid=$(id -u "$u" 2>/dev/null) || continue
		echo "$u:$uid"
	done < "$DB")
	have=$(ipt -S "$CH" 2>/dev/null | sed -n 's/.*--comment "\?zumo:\([^" ]*\)"\? .*/\1/p')
	# sobran
	while IFS=: read -r u uid; do
		[ -n "$u" ] || continue
		grep -qxF "$u:$uid" <<<"$want" || ipt -D "$CH" -m owner --uid-owner "$uid" -m comment --comment "zumo:$u:$uid" -j RETURN 2>/dev/null
	done <<<"$have"
	# faltan
	while IFS=: read -r u uid; do
		[ -n "$u" ] || continue
		grep -qxF "$u:$uid" <<<"$have" || ipt -A "$CH" -m owner --uid-owner "$uid" -m comment --comment "zumo:$u:$uid" -j RETURN
	done <<<"$want"
	# leer y poner a cero (atómico) y acumular
	local lectura
	lectura=$(ipt -L "$CH" -Z -n -v -x 2>/dev/null | awk '
		/zumo:/ { n=$0; sub(/.*zumo:/,"",n); sub(/ .*/,"",n); sub(/:[0-9]+$/,"",n); if ($2>0) print n, $2 }')
	(
		flock -w 5 9 || exit 0
		touch "$DATOS"
		awk -v db="$DB" '
			BEGIN { while ((getline l < db) > 0) { split(l, f, ":"); ok[f[1]]=1 } }
			NR==FNR { add[$1]+=$2; next }
			{ split($0, f, ":"); if (!(f[1] in ok)) next; tot[f[1]]=f[2]; vis[f[1]]=1; ord[++n]=f[1] }
			END {
				for (u in add) if (u in ok && !(u in vis)) { ord[++n]=u; tot[u]=0 }
				for (i=1;i<=n;i++) { u=ord[i]; printf "%s:%.0f\n", u, tot[u]+add[u] }
			}' <(echo "$lectura") "$DATOS" > "$DATOS.tmp" && mv -f "$DATOS.tmp" "$DATOS"
	) 9>"$LOCK"
}

if [ "${1:-}" = "--once" ]; then ciclo; exit 0; fi
while true; do ciclo; sleep "$INT"; done
