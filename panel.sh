#!/bin/bash
# DB: usuario:limite:vencimiento
DB=/etc/zumo/usuarios.db
N='\e[0m'
L='\e[38;5;97m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\e[0m'

# Librería compartida de operaciones sobre el DB (lock + escritura atómica),
# la misma que usa el borrador de temporales. Si falta, se baja del repo.
ZUMO_LIB=/etc/zumo/zumo-lib.sh
if [ ! -f "$ZUMO_LIB" ]; then
	curl -fsSL "https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/zumo-lib.sh" -o "$ZUMO_LIB" 2>/dev/null
fi
# shellcheck source=/dev/null
if ! source "$ZUMO_LIB" 2>/dev/null; then
	echo "Error: no se encontró $ZUMO_LIB (reinstalá el panel)." >&2
	exit 1
fi

stats() {
read -r _ mt mu _ <<< "$(free -m | awk '/^Mem:/{print $1,$2,$3}')"
local mfreep=$(( (mt-mu)*100/mt ))
local cores=$(nproc 2>/dev/null || echo 1)
local la=$(awk '{print $1}' /proc/loadavg)
local cpu=$(awk -v l="$la" -v c="$cores" 'BEGIN{p=l/c*100; if(p>100)p=100; printf "%d", p}')
local cpufree=$(( 100-cpu ))
local creadas=$(grep -c ':' "$DB" 2>/dev/null)
local online=$(ps -eo user:32,comm 2>/dev/null | awk '$2=="sshd" && $1!="root" && $1!="sshd"{print $1}' | sort -u | wc -l)
echo -e " \e[1;38;5;208mRAM:\e[0m ${mu}/${mt}MB (\e[1;32m${mfreep}% libre\e[0m)  \e[1;38;5;208mCPU:\e[0m ${cpu}% (\e[1;32m${cpufree}% libre\e[0m)"
echo -e " \e[1;38;5;208mCuentas:\e[0m ${creadas}  \e[1;38;5;208mEn línea:\e[0m \e[1;32m${online}\e[0m"
}

banner() {
clear
echo
echo -e "        \e[1;38;5;141m═══  P A N E L   Z U M O  ═══${N}"
echo -e " $L"
stats
echo -e " $L"
}

pausa() { echo; read -rp " Enter para volver..." _; }
op() { echo -e " \e[1;38;5;208m[$1]\e[0m \e[1;38;5;208m$2\e[0m \e[1;32m$3${N}"; }
msg_ok() { echo -e " \e[1;32m✔ $1${N}"; }
msg_err() { echo -e " \e[1;31m✘ $1${N}"; }

dias() {
local d=$(( ( $(date -d "$1" +%s) - $(date -d "$(date +%F)" +%s) ) / 86400 ))
if [ "$d" -lt 0 ]; then echo "vencido"; elif [ "$d" -eq 1 ]; then echo "vence 1 día"; else echo "vence $d días"; fi
}

# Base de usuarios temporales (username:epoch_de_vencimiento).
TEMPDB=/etc/zumo/temporales.db

es_temporal() { [ -f "$TEMPDB" ] && grep -q "^$1:" "$TEMPDB"; }

# Minutos restantes de un usuario temporal.
temp_restante() {
local ep now
ep=$(awk -F: -v u="$1" '$1==u{print $2; exit}' "$TEMPDB" 2>/dev/null)
[ -z "$ep" ] && { echo "?"; return; }
now=$(date +%s)
local m=$(( (ep - now + 59) / 60 ))
if [ "$m" -le 0 ]; then echo "venció"; else echo "${m}m restantes"; fi
}

# Hace cuánto está conectado un usuario: toma la sesión sshd más vieja (mayor
# tiempo transcurrido) y lo formatea en días/horas/minutos.
tiempo_conectado() {
local s
s=$(ps -u "$1" -o etimes=,comm= 2>/dev/null | awk '$2=="sshd"{if($1>m)m=$1} END{print m+0}')
[ -z "$s" ] || [ "$s" -le 0 ] && { echo ""; return; }
local d=$(( s/86400 )) h=$(( (s%86400)/3600 )) m=$(( (s%3600)/60 ))
if [ "$d" -gt 0 ]; then echo "${d}d ${h}h"
elif [ "$h" -gt 0 ]; then echo "${h}h ${m}m"
else echo "${m}m"; fi
}

# Si el usuario fue creado en modo HWID, devuelve el nombre del cliente
# (guardado en el campo GECOS como "hwid,<cliente>"); si no, el username tal cual.
etiqueta_de() {
local gecos
gecos=$(getent passwd "$1" 2>/dev/null | awk -F: '{print $5}')
case "$gecos" in
hwid,*) echo "${gecos#hwid,}" ;;
*) echo "$1" ;;
esac
}

es_hwid() {
case "$(getent passwd "$1" 2>/dev/null | awk -F: '{print $5}')" in hwid,*) return 0 ;; *) return 1 ;; esac
}

en_linea() { ps -u "$1" -o comm= 2>/dev/null | grep -c '^sshd$'; }

# Validación de usuario/contraseña: solo letras y números, máximo 10, sin
# espacios ni símbolos. El usuario además empieza con letra (minúscula).
nombre_valido() { [[ "$1" =~ ^[a-z][a-z0-9]{0,9}$ ]]; }
clave_valida() { [[ "$1" =~ ^[A-Za-z0-9]{1,10}$ ]]; }

# Lee entrada en vivo aceptando SOLO letras y números: el espacio o cualquier
# símbolo no se escribe y avisa en el momento. Resultado en $REPLY_ALNUM.
#   leer_alnum "Usuario: " [max=10] [minuscula]
leer_alnum() {
local prompt="$1" max="${2:-10}" lower="${3:-}" buf="" ch
printf " %s" "$prompt"
while IFS= read -rsn1 ch; do
[[ -z "$ch" ]] && break                       # Enter
if [[ "$ch" == $'\x7f' || "$ch" == $'\b' ]]; then  # borrar
[ -n "$buf" ] && { buf="${buf%?}"; printf '\b \b'; }
continue
fi
if [[ "$ch" == [A-Za-z0-9] ]]; then
[ "${#buf}" -ge "$max" ] && continue           # tope de largo
[ "$lower" = "lower" ] && ch="${ch,,}"         # forzar minúscula (usuarios)
buf+="$ch"; printf '%s' "$ch"
else
printf '\n \e[1;31m✘ Solo letras y números\e[0m\n %s%s' "$prompt" "$buf"
fi
done
printf '\n'
REPLY_ALNUM="$buf"
}

esta_bloqueado() {
local est
est=$(passwd -S "$1" 2>/dev/null | awk '{print $2}')
[ "$est" = "L" ]
}

elegir_usuario() {
mapfile -t USERS < <(cut -d: -f1 "$DB" | sed '/^$/d')
if [ ${#USERS[@]} -eq 0 ]; then msg_err "No hay usuarios registrados"; return 1; fi
for i in "${!USERS[@]}"; do
local etiq="$(etiqueta_de "${USERS[$i]}")"
if es_hwid "${USERS[$i]}"; then etiq="$etiq (HWID)"; fi
echo -e " \e[1;38;5;208m[$((i+1))]\e[0m \e[1;32m${etiq}\e[0m"
done
echo; read -rp " Número de usuario: " n
if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt ${#USERS[@]} ]; then msg_err "Opción inválida"; return 1; fi
SEL="${USERS[$((n-1))]}"
}

crear_usuario() {
banner; echo -e " \e[1;38;5;141mCREAR USUARIO${N}\n"
op 1 "●" "Normal"
op 2 "🔑" "HWID"
op 0 "◂" "Volver"
echo; read -rp " Modo [1]: " modo; modo=${modo:-1}
case "$modo" in
0) return ;;
1|2) ;;
*) msg_err "Opción inválida"; sleep 1; return ;;
esac
echo

if [ "$modo" = "2" ]; then
read -rp " Nombre del cliente (solo para identificarlo en el panel): " etiqueta
etiqueta=$(zumo_limpiar_etiqueta "$etiqueta")
leer_alnum "Pegá el HWID del cliente (8 a 32 caracteres): " 32; hwid="$REPLY_ALNUM"
if [ ${#hwid} -lt 8 ] || [ ${#hwid} -gt 32 ]; then
msg_err "HWID inválido (8 a 32 caracteres alfanuméricos; quedaron ${#hwid})"; pausa; return
fi
id "$hwid" &>/dev/null && { msg_err "Ese HWID ya está registrado"; pausa; return; }
read -rp " Días de duración: " d
[[ "$d" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; pausa; return; }
read -rp " Límite de conexiones [1]: " lim; lim=${lim:-1}
[[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido (mínimo 1)"; pausa; return; }
exp=$(date -d "+$d days" +%F)
if ! useradd --badname -M -s /bin/false -e "$exp" -c "hwid,$etiqueta" "$hwid" 2>/dev/null; then
msg_err "No se pudo crear el usuario (probá con otro HWID)"; pausa; return
fi
echo "$hwid:$hwid" | chpasswd
zumo_db_add "$hwid" "$lim" "$exp"
echo; echo -e " $L"
msg_ok "Usuario HWID creado"
echo -e "   Cliente:                      \e[1;38;5;214m$etiqueta${N}"
echo -e "   HWID (usuario y contraseña):  \e[1;38;5;214m$hwid${N}"
echo -e "   Duración:                     \e[1;38;5;214m$(dias "$exp")${N}"
echo -e "   Límite:                       \e[1;38;5;214m$lim conexión(es)${N}"
echo -e " $L"
echo -e " \e[2mEl cliente carga ese mismo ID como usuario Y como contraseña en la app.${N}"
pausa
return
fi

leer_alnum "Usuario: " 10 lower; u="$REPLY_ALNUM"
nombre_valido "$u" || { msg_err "Usuario inválido (debe empezar con letra)"; pausa; return; }
id "$u" &>/dev/null && { msg_err "El usuario ya existe"; pausa; return; }
leer_alnum "Contraseña: " 10; p="$REPLY_ALNUM"
clave_valida "$p" || { msg_err "Contraseña inválida (no puede estar vacía)"; pausa; return; }
read -rp " Días de duración: " d
[[ "$d" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; pausa; return; }
read -rp " Límite de conexiones [1]: " lim; lim=${lim:-1}
[[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido (mínimo 1)"; pausa; return; }
exp=$(date -d "+$d days" +%F)
if ! useradd -M -s /bin/false -e "$exp" "$u" 2>/dev/null; then
msg_err "No se pudo crear el usuario"; pausa; return
fi
echo "$u:$p" | chpasswd
zumo_db_add "$u" "$lim" "$exp"
echo; echo -e " $L"
msg_ok "Usuario creado"
echo -e "   Usuario:    \e[1;38;5;214m$u${N}"
echo -e "   Contraseña: \e[1;38;5;214m$p${N}"
echo -e "   Duración:   \e[1;38;5;214m$(dias "$exp")${N}"
echo -e "   Límite:     \e[1;38;5;214m$lim conexión(es)${N}"
echo -e " $L"; pausa
}

programar_borrado_temp() {
# Agenda el borrado del usuario temporal con systemd-run (preciso al minuto).
local u="$1" min="$2" ep
# Garantizar el script de borrado (self-healing para instalaciones actualizadas).
if [ ! -x /etc/zumo/borrar-temporal.sh ]; then
cat > /etc/zumo/borrar-temporal.sh <<'BORRARTEMP'
#!/bin/bash
u="$1"
[ -z "$u" ] && exit 0
[ -f /etc/zumo/zumo-lib.sh ] && source /etc/zumo/zumo-lib.sh
pkill -9 -u "$u" 2>/dev/null
userdel "$u" 2>/dev/null
if command -v zumo_db_del >/dev/null 2>&1; then zumo_db_del "$u"; else sed -i "/^$u:/d" /etc/zumo/usuarios.db 2>/dev/null; fi
if [ -f /etc/zumo/temporales.db ]; then grep -v "^$u:" /etc/zumo/temporales.db > /etc/zumo/temporales.db.tmp 2>/dev/null && mv /etc/zumo/temporales.db.tmp /etc/zumo/temporales.db; fi
exit 0
BORRARTEMP
chmod +x /etc/zumo/borrar-temporal.sh
fi
ep=$(( $(date +%s) + min*60 ))
mkdir -p /etc/zumo
grep -v "^$u:" "$TEMPDB" 2>/dev/null > "$TEMPDB.tmp"; mv "$TEMPDB.tmp" "$TEMPDB" 2>/dev/null
echo "$u:$ep" >> "$TEMPDB"
systemctl reset-failed "zumo-temp-$u.timer" 2>/dev/null
systemd-run --quiet --collect --unit="zumo-temp-$u" --on-active="${min}min" \
--timer-property=AccuracySec=5s /etc/zumo/borrar-temporal.sh "$u" 2>/dev/null
}

crear_temporal() {
banner; echo -e " \e[1;38;5;141mUSUARIO TEMPORAL${N}\n"
op 1 "●" "Común"
op 2 "🔑" "HWID"
op 0 "◂" "Volver"
echo; read -rp " Modo [1]: " modo; modo=${modo:-1}
case "$modo" in
0) return ;;
1|2) ;;
*) msg_err "Opción inválida"; sleep 1; return ;;
esac
echo

if [ "$modo" = "2" ]; then
read -rp " Nombre del cliente: " etiqueta
etiqueta=$(zumo_limpiar_etiqueta "$etiqueta")
leer_alnum "Pegá el HWID del cliente (8 a 32 caracteres): " 32; hwid="$REPLY_ALNUM"
if [ ${#hwid} -lt 8 ] || [ ${#hwid} -gt 32 ]; then
msg_err "HWID inválido (8 a 32 caracteres alfanuméricos; quedaron ${#hwid})"; pausa; return
fi
id "$hwid" &>/dev/null && { msg_err "Ese HWID ya está registrado"; pausa; return; }
read -rp " Minutos de duración: " min
[[ "$min" =~ ^[0-9]+$ ]] && [ "$min" -ge 1 ] || { msg_err "Minutos inválidos"; pausa; return; }
exp=$(date -d "+2 days" +%F)
if ! useradd --badname -M -s /bin/false -e "$exp" -c "hwid,$etiqueta" "$hwid" 2>/dev/null; then
msg_err "No se pudo crear el usuario temporal"; pausa; return
fi
echo "$hwid:$hwid" | chpasswd
zumo_db_add "$hwid" 1 "$exp"
programar_borrado_temp "$hwid" "$min"
echo; echo -e " $L"
msg_ok "Usuario HWID temporal creado"
echo -e "   Cliente:                      \e[1;38;5;214m$etiqueta${N}"
echo -e "   HWID (usuario y contraseña):  \e[1;38;5;214m$hwid${N}"
echo -e "   Duración:                     \e[1;38;5;214m$min minuto(s)${N}"
echo -e "   Límite:                       \e[1;38;5;214m1 conexión${N}"
echo -e " $L"
echo -e " \e[2mEl cliente carga ese ID como usuario Y contraseña. Se borra solo a los $min min.${N}"
pausa
return
fi

leer_alnum "Usuario: " 10 lower; u="$REPLY_ALNUM"
nombre_valido "$u" || { msg_err "Usuario inválido (debe empezar con letra)"; pausa; return; }
id "$u" &>/dev/null && { msg_err "El usuario ya existe"; pausa; return; }
leer_alnum "Contraseña: " 10; p="$REPLY_ALNUM"
clave_valida "$p" || { msg_err "Contraseña inválida (no puede estar vacía)"; pausa; return; }
read -rp " Minutos de duración: " min
[[ "$min" =~ ^[0-9]+$ ]] && [ "$min" -ge 1 ] || { msg_err "Minutos inválidos"; pausa; return; }
read -rp " Conexiones permitidas [1]: " lim; lim=${lim:-1}
[[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido"; pausa; return; }
exp=$(date -d "+2 days" +%F)
if ! useradd -M -s /bin/false -e "$exp" "$u" 2>/dev/null; then
msg_err "No se pudo crear el usuario temporal"; pausa; return
fi
echo "$u:$p" | chpasswd
zumo_db_add "$u" "$lim" "$exp"
programar_borrado_temp "$u" "$min"
echo; echo -e " $L"
msg_ok "Usuario temporal creado"
echo -e "   Usuario:    \e[1;38;5;214m$u${N}"
echo -e "   Contraseña: \e[1;38;5;214m$p${N}"
echo -e "   Duración:   \e[1;38;5;214m$min minuto(s)${N}"
echo -e "   Límite:     \e[1;38;5;214m$lim conexión(es)${N}"
echo -e " $L"
echo -e " \e[2mSe borra solo a los $min minutos.${N}"; pausa
}

# Lista todos los usuarios juntos (comunes y HWID). Los HWID muestran el nombre
# del cliente y, debajo, su HWID. Verde = conectado.
lista_para_borrar() {
local u lim exp on col lab i=0
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
i=$((i+1))
on=$(en_linea "$u")
if [ "${on:-0}" -gt 0 ]; then col='\e[1;32m●\e[0m'; else col='\e[2m○\e[0m'; fi
if es_hwid "$u"; then
lab=$(etiqueta_de "$u")
printf ' \e[1;38;5;208m[%d]\e[0m %b \e[1;38;5;214m%s\e[0m \e[2m(HWID)\e[0m\n' "$i" "$col" "$lab"
printf '      \e[2m%s\e[0m\n' "$u"
else
printf ' \e[1;38;5;208m[%d]\e[0m %b \e[1;38;5;214m%s\e[0m\n' "$i" "$col" "$u"
fi
done < "$DB"
}

# Busca lo que escribió la persona: usuario o HWID exacto, número de la lista,
# nombre del cliente (HWID) o usuario sin distinguir mayúsculas. El resultado queda en $SEL (siempre sale de
# la base, nunca del texto escrito). Devuelve 1 si no existe y 2 si el nombre
# coincide con varios clientes HWID (quedan en AMBIGUOS).
buscar_usuario() {
local q="$1" u lim exp lab i=0
local -a hits=()
AMBIGUOS=()
# 1) usuario o HWID exacto
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
if [ "$u" = "$q" ]; then SEL="$u"; return 0; fi
done < "$DB"
# 2) número de la lista (mismo orden que lista_para_borrar)
if [[ "$q" =~ ^[0-9]+$ ]]; then
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
i=$((i+1))
if [ "$i" -eq "$((10#$q))" ]; then SEL="$u"; return 0; fi
done < "$DB"
return 1
fi
# 3) nombre del cliente (HWID)
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
es_hwid "$u" || continue
lab=$(etiqueta_de "$u")
[ "${lab,,}" = "${q,,}" ] && hits+=("$u")
done < "$DB"
if [ ${#hits[@]} -eq 1 ]; then SEL="${hits[0]}"; return 0; fi
if [ ${#hits[@]} -gt 1 ]; then AMBIGUOS=("${hits[@]}"); return 2; fi
# 4) usuario sin distinguir mayúsculas
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
es_hwid "$u" && continue
if [ "${u,,}" = "${q,,}" ]; then SEL="$u"; return 0; fi
done < "$DB"
return 1
}

# Saca al usuario del sistema, de la base y de los temporales.
borrar_usuario_completo() {
local u="$1"
pkill -9 -u "$u" 2>/dev/null
userdel "$u" 2>/dev/null
zumo_db_del "$u"
systemctl stop "zumo-temp-$u.timer" 2>/dev/null
if [ -f "$TEMPDB" ]; then
awk -F: -v u="$u" '$1!=u' "$TEMPDB" > "$TEMPDB.tmp" && mv -f "$TEMPDB.tmp" "$TEMPDB"
fi
}

eliminar_usuario() {
local q rc nombre h
while true; do
banner; echo -e " \e[1;38;5;141mELIMINAR USUARIO${N}\n"
if [ ! -s "$DB" ]; then msg_err "No hay usuarios registrados"; pausa; return; fi
lista_para_borrar
echo; echo -e " $L"
read -rp " Número, usuario o HWID para borrar: " q
q="${q#"${q%%[![:space:]]*}"}"; q="${q%"${q##*[![:space:]]}"}"
[ -z "$q" ] && return
buscar_usuario "$q"; rc=$?
case $rc in
0) nombre="$(etiqueta_de "$SEL")"
borrar_usuario_completo "$SEL"
msg_ok "Usuario $nombre eliminado"; sleep 1 ;;
2) msg_err "Hay varios clientes con el nombre \"$q\". Escribí el HWID:"
for h in "${AMBIGUOS[@]}"; do echo -e "     \e[1;38;5;214m$h${N}"; done
pausa ;;
*) msg_err "No existe: $q"; sleep 1 ;;
esac
done
}

vencidos() {
banner; echo -e " \e[1;38;5;141mUSUARIOS VENCIDOS${N}\n"
if [ ! -s "$DB" ]; then msg_err "No hay usuarios"; pausa; return; fi
hoy=$(date -d "$(date +%F)" +%s)
VENC=()
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
e=$(date -d "$exp" +%s 2>/dev/null) || continue
[ "$e" -lt "$hoy" ] && VENC+=("$u|$exp")
done < "$DB"
if [ ${#VENC[@]} -eq 0 ]; then msg_ok "No hay usuarios vencidos"; pausa; return; fi
printf " \e[1;38;5;208m%-16s %s${N}\n" "USUARIO/CLIENTE" "VENCIÓ"
for item in "${VENC[@]}"; do
printf " \e[1;31m%-16s %s${N}\n" "$(etiqueta_de "${item%%|*}")" "${item#*|}"
done
echo; echo -e " $L"
op 1 "✖" "Borrar usuarios vencidos"
op 0 "◂" "Volver"
echo; read -rp " Opción: " o
case $o in
1) read -rp " ¿Borrar estos ${#VENC[@]} usuario(s)? [s/N]: " c
[[ "$c" =~ ^[sS]$ ]] || { msg_err "Cancelado"; pausa; return; }
for item in "${VENC[@]}"; do
u="${item%%|*}"
pkill -9 -u "$u" 2>/dev/null
userdel "$u" 2>/dev/null
zumo_db_del "$u"
done
msg_ok "${#VENC[@]} usuario(s) vencido(s) eliminado(s)"; pausa ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
}

editar_usuario() {
banner; echo -e " \e[1;38;5;141mEDITAR USUARIO${N}\n"
elegir_usuario || { pausa; return; }
while true; do
local info lim exp
info=$(awk -F: -v u="$SEL" '$1==u{print $2":"$3}' "$DB")
lim="${info%%:*}"
exp="${info#*:}"
banner; echo -e " \e[1;38;5;141mEDITAR USUARIO: $SEL${N}\n"
echo -e "   Límite actual:      \e[1;38;5;214m$lim${N}"
echo -e "   Vencimiento actual: \e[1;38;5;214m$exp ($(dias "$exp"))${N}\n"
local estado_bloq="desbloqueado"; esta_bloqueado "$SEL" && estado_bloq="bloqueado"
echo -e "   Estado:             \e[1;38;5;214m$estado_bloq${N}\n"
op 1 "✎" "Cambiar contraseña"
op 2 "⚙" "Cambiar límite de conexiones"
op 3 "⏱" "Cambiar días (vencimiento)"
if esta_bloqueado "$SEL"; then op 4 "🔓" "Desbloquear"; else op 4 "🔒" "Bloquear"; fi
es_hwid "$SEL" && op 5 "🔑" "Cambiar HWID"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " eo
case $eo in
1) leer_alnum "Contraseña nueva para $SEL: " 10; np="$REPLY_ALNUM"
clave_valida "$np" || { msg_err "Contraseña inválida (no puede estar vacía)"; sleep 1; continue; }
echo "$SEL:$np" | chpasswd
msg_ok "Contraseña de $SEL actualizada"; sleep 1 ;;
2) read -rp " Nuevo límite para $SEL [$lim]: " nl; nl=${nl:-$lim}
[[ "$nl" =~ ^[0-9]+$ ]] && [ "$nl" -ge 1 ] || { msg_err "Límite inválido"; sleep 1; continue; }
zumo_db_set "$SEL" 2 "$nl"
msg_ok "Límite de $SEL ahora es $nl"; sleep 1 ;;
3) read -rp " Días desde hoy: " nd
[[ "$nd" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; sleep 1; continue; }
nexp=$(date -d "+$nd days" +%F)
usermod -e "$nexp" "$SEL" 2>/dev/null
zumo_db_set "$SEL" 3 "$nexp"
msg_ok "Vencimiento de $SEL ahora: $nexp ($(dias "$nexp"))"; sleep 1 ;;
4) if esta_bloqueado "$SEL"; then
usermod -U "$SEL" 2>/dev/null; msg_ok "$SEL desbloqueado"
else
usermod -L "$SEL" 2>/dev/null; pkill -9 -u "$SEL" 2>/dev/null; msg_ok "$SEL bloqueado"
fi; sleep 1 ;;
5) es_hwid "$SEL" || { msg_err "Ese usuario no es de modo HWID"; sleep 1; continue; }
read -rp " HWID nuevo (8 a 32 alfanuméricos): " nhraw
nh=$(echo "$nhraw" | tr -cd 'A-Za-z0-9')
if [ ${#nh} -lt 8 ] || [ ${#nh} -gt 32 ]; then msg_err "HWID inválido"; sleep 1; continue; fi
id "$nh" &>/dev/null && { msg_err "Ya existe un usuario con ese HWID"; sleep 1; continue; }
pkill -9 -u "$SEL" 2>/dev/null
if usermod --badname -l "$nh" "$SEL" 2>/dev/null; then
echo "$nh:$nh" | chpasswd
zumo_db_rename "$SEL" "$nh"
SEL="$nh"
msg_ok "HWID cambiado a $nh"
else
msg_err "No se pudo cambiar el HWID"
fi; sleep 1 ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

listar_usuarios() {
if [ ! -s "$DB" ]; then banner; echo -e " \e[1;38;5;141mUSUARIOS REGISTRADOS${N}\n"; msg_err "No hay usuarios"; pausa; return; fi
while true; do
banner; echo -e " \e[1;38;5;141mUSUARIOS REGISTRADOS — EN VIVO${N}"
echo

local hay_comun=0 hay_hwid=0

# ---------- Sección COMÚN ----------
echo -e " \e[1;38;5;141m━━━ COMÚN ━━━${N}"
printf " \e[1;38;5;208m%-18s %-14s %-8s %s${N}\n" "USUARIO" "ESTADO" "LÍMITE" "VENCIMIENTO"
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
es_hwid "$u" && continue
hay_comun=1
on=$(en_linea "$u")
if [ "$on" -gt 0 ]; then est_txt="● online ($on)"; est_col="\e[1;32m"; else est_txt="○ offline"; est_col="\e[2m"; fi
if es_temporal "$u"; then venc="⏳ $(temp_restante "$u")"; else venc="$(dias "$exp")"; fi
printf " %-18s ${est_col}%-14s${N} %-8s %s\n" "$u" "$est_txt" "$lim" "$venc"
if [ "$on" -gt 0 ]; then tc=$(tiempo_conectado "$u"); [ -n "$tc" ] && echo -e "    \e[2mconectado hace\e[0m \e[1;38;5;214m$tc${N}"; fi
done < "$DB"
[ "$hay_comun" -eq 0 ] && echo -e " \e[2m(sin usuarios comunes)${N}"

# ---------- Sección HWID ----------
echo; echo -e " \e[1;38;5;141m━━━ HWID ━━━${N}"
printf " \e[1;38;5;208m%-18s %-14s %-8s %s${N}\n" "CLIENTE" "ESTADO" "LÍMITE" "VENCIMIENTO"
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
es_hwid "$u" || continue
hay_hwid=1
on=$(en_linea "$u")
if [ "$on" -gt 0 ]; then est_txt="● online ($on)"; est_col="\e[1;32m"; else est_txt="○ offline"; est_col="\e[2m"; fi
if es_temporal "$u"; then venc="⏳ $(temp_restante "$u")"; else venc="$(dias "$exp")"; fi
printf " %-18s ${est_col}%-14s${N} %-8s %s\n" "$(etiqueta_de "$u")" "$est_txt" "$lim" "$venc"
echo -e "    \e[2mHWID:\e[0m \e[1;38;5;214m$u${N}"
if [ "$on" -gt 0 ]; then tc=$(tiempo_conectado "$u"); [ -n "$tc" ] && echo -e "    \e[2mconectado hace\e[0m \e[1;38;5;214m$tc${N}"; fi
done < "$DB"
[ "$hay_hwid" -eq 0 ] && echo -e " \e[2m(sin usuarios HWID)${N}"

echo; echo -e " $L"
read -t 2 -rsn1 _ && break
done
}

menu_usuario() {
while true; do
banner; echo -e " \e[1;38;5;141mUSUARIO${N}\n"
op 1 "✚" "Crear usuario"
op 2 "✖" "Eliminar usuario"
op 3 "✎" "Editar usuario"
op 4 "▤" "Ver usuarios (en vivo)"
op 5 "⚠" "Usuarios vencidos"
op 6 "⏳" "Usuario temporal"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) crear_usuario ;;
2) eliminar_usuario ;;
3) editar_usuario ;;
4) listar_usuarios ;;
5) vencidos ;;
6) crear_temporal ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

bhttp_port() {
local p
p=$(cat /etc/zumo/bhttp.port 2>/dev/null)
[[ "$p" =~ ^[0-9]+$ ]] || p=8080
echo "$p"
}

diag_bhttp() {
banner; echo -e " \e[1;38;5;141mDIAGNÓSTICO DE BHTTP${N}\n"
local bp r n
bp=$(bhttp_port)
if systemctl is-active --quiet bhttp-server && systemctl is-active --quiet bhttp-shim; then msg_ok "Servicio: activo (servidor + adaptador)"; else msg_err "Servicio: inactivo o incompleto (activalo con la opción 5)"; fi
if ss -ltnH "sport = :$bp" 2>/dev/null | grep -q .; then msg_ok "Escuchando en el puerto $bp"; else msg_err "Nada escucha en el puerto $bp"; fi
if timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/$bp" 2>/dev/null; then msg_ok "Acepta conexiones locales"; else msg_err "No acepta conexiones locales en $bp"; fi
n=$(ss -tnH state established "( sport = :$bp )" 2>/dev/null | wc -l)
echo -e "   Conexiones establecidas ahora: \e[1;38;5;214m${n}${N}"
if [ -x /opt/bhttp-server/bhttp-server ]; then
r=$(timeout 15 /opt/bhttp-server/bhttp-server -self-test 2>&1 | head -n1)
case "$r" in BHTTP_SELF_TEST_PASS*) msg_ok "Prueba interna del binario: OK" ;; *) msg_err "Prueba interna: ${r:-sin respuesta}" ;; esac
fi
if systemctl is-active --quiet pdirect-80 && [ "$bp" != "80" ]; then
echo -e "   \e[1;38;5;214m⚠ El puerto 80 lo usa PDirect: si la app apunta al 80 se queda en 'Conectando'.${N}"
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
ufw status 2>/dev/null | grep -qw "$bp" || msg_err "ufw está activo y no muestra el puerto $bp abierto (ufw allow $bp/tcp)"
fi
echo; echo -e " \e[1;38;5;141mSSH (el tramo que sigue a 'BHTTP session connected'):${N}"
if ss -ltnH "sport = :22" 2>/dev/null | grep -q .; then msg_ok "sshd escucha en el puerto 22"; else msg_err "Nada escucha en el 22 (BHTTP reenvía a 127.0.0.1:22)"; fi
n22=$(ss -tnH state established "( dport = :22 )" 2>/dev/null | awk '$4=="127.0.0.1:22"' | wc -l)
echo -e "   Conexiones de BHTTP hacia sshd ahora: \e[1;38;5;214m${n22}${N}"
cfg=$(sshd -T 2>/dev/null | grep -Ei '^(passwordauthentication|allowtcpforwarding|maxsessions|maxstartups|usepam) ')
if [ -n "$cfg" ]; then
echo "$cfg" | sed 's/^/   /'
echo "$cfg" | grep -qi '^allowtcpforwarding no' && msg_err "AllowTcpForwarding está en 'no': el túnel no puede abrirse"
echo "$cfg" | grep -qi '^passwordauthentication no' && msg_err "PasswordAuthentication está en 'no': no deja entrar con contraseña"
fi
echo -e "   Últimos eventos de sshd (¿llegó el login de la app?):"
lg=$(journalctl -u ssh -u sshd -n 8 --no-pager -o cat 2>/dev/null)
[ -z "$lg" ] && lg=$(grep -a sshd /var/log/auth.log 2>/dev/null | tail -n 8)
if [ -n "$lg" ]; then echo "$lg" | cut -c1-110 | sed 's/^/     /'; else echo "     (sin eventos)"; fi
echo; echo -e " \e[1;38;5;141mÚltimos logs (solo muestra errores):${N}"
journalctl -u bhttp-server -u bhttp-shim -n 8 --no-pager -o cat 2>/dev/null | sed 's/^/   /'
echo; echo -e " $L"
echo -e "   En la app: servidor = IP pública de la VPS, puerto = \e[1;38;5;214m${bp}${N}"
echo -e "   Si sigue en 'Conectando', abrí el puerto $bp en el firewall del proveedor."
pausa
}

menu_pdirect() {
while true; do
banner; echo -e " \e[1;38;5;141mPDIRECT (WebSocket 80)${N}\n"
if systemctl is-active --quiet pdirect-80; then
echo -e " \e[1;32m● PDirect: activo (80 → SSH)${N}\n"
else
echo -e " \e[1;31m● PDirect: inactivo${N}\n"
fi
op 1 "⚡" "Activar"
op 2 "✖" "Desactivar"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) if systemctl is-active --quiet pdirect-80; then
msg_err "Ya está activo"; pausa
else
BAN=""; COL=""
read -rp " ¿Personalizar el banner? [s/N]: " pb
if [[ "$pb" =~ ^[sS]$ ]]; then
read -rp " Texto del banner [ZUMO]: " BAN; BAN=${BAN:-ZUMO}
COL="yellow"
fi
echo -e " \e[1;38;5;141mCompilando e instalando, aguardá...${N}"
RESULTADO=0; bash /etc/zumo/activar-pdirect.sh "$BAN" "$COL" "101" || RESULTADO=1
if [ "$RESULTADO" -eq 0 ]; then
msg_ok "PDirect activo (80 → SSH)"
[ -n "$BAN" ] && echo -e "   Banner: \e[1;38;5;214m$BAN${N}  Color: \e[1;38;5;214m${COL:-yellow}${N}"
else
msg_err "Falló; revisá 'journalctl -u pdirect-80'"
fi; pausa
fi ;;
2) echo -e " \e[1;38;5;141mLiberando puerto 80...${N}"
bash /etc/zumo/desactivar-pdirect.sh
msg_ok "PDirect desactivado; puerto 80 liberado"; pausa ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

menu_badvpn() {
while true; do
banner; echo -e " \e[1;38;5;141mBADVPN (UDPGW 7300)${N}\n"
if systemctl is-active --quiet udpgw-7300; then
echo -e " \e[1;32m● BadVPN: activo (7300)${N}\n"
else
echo -e " \e[1;31m● BadVPN: inactivo${N}\n"
fi
op 1 "⚡" "Activar"
op 2 "✖" "Desactivar"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) if systemctl is-active --quiet udpgw-7300; then
msg_err "Ya está activo"; pausa
else
echo -e " \e[1;38;5;141mCompilando e instalando, aguardá...${N}"
if bash /etc/zumo/activar-badvpn.sh; then
msg_ok "BadVPN activo (7300)"
else
msg_err "Falló; revisá 'journalctl -u udpgw-7300'"
fi; pausa
fi ;;
2) echo -e " \e[1;38;5;141mLiberando puerto 7300...${N}"
bash /etc/zumo/desactivar-badvpn.sh
msg_ok "BadVPN desactivado; puerto 7300 liberado"; pausa ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

hcr_port() {
local p
p=$(cat /etc/zumo/hcr.port 2>/dev/null)
[[ "$p" =~ ^[0-9]+$ ]] || p=8880
echo "$p"
}

menu_hcr() {
while true; do
banner; echo -e " \e[1;38;5;141mHCR SERVER${N}\n"
if systemctl is-active --quiet hcr-server; then
echo -e " \e[1;32m● HCR Server: activo ($(hcr_port) → SSH)${N}\n"
else
echo -e " \e[1;31m● HCR Server: inactivo${N}\n"
fi
op 1 "⚡" "Activar (elegís el puerto)"
op 2 "✖" "Desactivar"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) if systemctl is-active --quiet hcr-server; then
msg_err "Ya está activo"; pausa
else
read -rp " Puerto para HCR [8880]: " hp; hp=${hp:-8880}
if ! [[ "$hp" =~ ^[0-9]+$ ]] || [ "$hp" -lt 1 ] || [ "$hp" -gt 65535 ]; then
msg_err "Puerto inválido (1-65535)"
else
echo -e " \e[1;38;5;141mInstalando HCR Server en el puerto ${hp}, aguardá...${N}"
if bash /etc/zumo/activar-hcr.sh "$hp"; then
msg_ok "HCR Server activo en el puerto $hp (→ SSH 22)"
else
msg_err "Falló; revisá 'journalctl -u hcr-server'"
fi
fi; pausa
fi ;;
2) echo -e " \e[1;38;5;141mLiberando el puerto de HCR...${N}"
bash /etc/zumo/desactivar-hcr.sh
msg_ok "HCR Server desactivado; puerto liberado"; pausa ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

menu_bhttp() {
while true; do
banner; echo -e " \e[1;38;5;141mBHTTP${N}\n"
if systemctl is-active --quiet bhttp-server && systemctl is-active --quiet bhttp-shim; then
echo -e " \e[1;32m● BHTTP: activo ($(bhttp_port) → SSH)${N}\n"
else
echo -e " \e[1;31m● BHTTP: inactivo${N}\n"
fi
op 1 "⚡" "Activar (elegís el puerto)"
op 2 "✖" "Desactivar"
op 3 "⚙" "Diagnóstico"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) if systemctl is-active --quiet bhttp-server && systemctl is-active --quiet bhttp-shim; then
msg_err "Ya está activo"; pausa; continue
fi
read -rp " Puerto para BHTTP [8080]: " bp; bp=${bp:-8080}
if ! [[ "$bp" =~ ^[0-9]+$ ]] || [ "$bp" -lt 1 ] || [ "$bp" -gt 65535 ]; then
msg_err "Puerto inválido (1-65535)"
else
echo -e " \e[1;38;5;141mInstalando BHTTP en el puerto ${bp}, aguardá...${N}"
if bash /etc/zumo/activar-bhttp.sh "$bp"; then
msg_ok "BHTTP activo en el puerto $bp (→ SSH 22)"
else
msg_err "Falló; usá la opción 3 (diagnóstico)"
fi
fi; pausa ;;
2) echo -e " \e[1;38;5;141mLiberando el puerto de BHTTP...${N}"
bash /etc/zumo/desactivar-bhttp.sh
msg_ok "BHTTP desactivado; puerto liberado"; pausa ;;
3) diag_bhttp ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

menu_protocolos() {
while true; do
banner; echo -e " \e[1;38;5;141mPROTOCOLOS${N}\n"
op 1 "⚡" "PDirect (WebSocket)"
op 2 "⚡" "BadVPN"
op 3 "⚡" "BHTTP"
op 4 "⚡" "HCR Server"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) menu_pdirect ;;
2) menu_badvpn ;;
3) menu_bhttp ;;
4) menu_hcr ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

bbr_activo() {
[ "$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null)" = "bbr" ]
}

activar_bbr() {
# BBR + fq: mejor estabilidad y menos jitter bajo carga. Se persiste en
# /etc/sysctl.d/99-zumo-bbr.conf y se aplica en caliente.
if ! modprobe tcp_bbr 2>/dev/null; then
if ! grep -q bbr /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null; then
msg_err "Tu kernel no trae BBR (necesitás kernel 4.9+). No se cambió nada."
return 1
fi
fi
cat > /etc/sysctl.d/99-zumo-bbr.conf <<'EOF'
# ZUMO - optimización de red (BBR + fair queue)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_fastopen = 3
net.core.netdev_max_backlog = 16384
net.ipv4.tcp_notsent_lowat = 16384
EOF
grep -q '^tcp_bbr' /etc/modules-load.d/zumo-bbr.conf 2>/dev/null || echo "tcp_bbr" > /etc/modules-load.d/zumo-bbr.conf
sysctl --system >/dev/null 2>&1
bbr_activo
}

desactivar_bbr() {
rm -f /etc/sysctl.d/99-zumo-bbr.conf /etc/modules-load.d/zumo-bbr.conf
sysctl -w net.ipv4.tcp_congestion_control=cubic >/dev/null 2>&1
sysctl -w net.core.default_qdisc=fq_codel >/dev/null 2>&1
sysctl --system >/dev/null 2>&1
}

menu_bbr() {
while true; do
banner; echo -e " \e[1;38;5;141mBBR — OPTIMIZACIÓN DE RED${N}\n"
local cc qd
cc=$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null)
qd=$(sysctl -n net.core.default_qdisc 2>/dev/null)
if bbr_activo; then
echo -e " \e[1;32m● BBR: activo${N} \e[2m(control de congestión: ${cc}, qdisc: ${qd})${N}\n"
else
echo -e " \e[1;31m● BBR: inactivo${N} \e[2m(actual: ${cc:-?}, qdisc: ${qd:-?})${N}\n"
fi
echo -e " \e[2mBBR + fq mejora la estabilidad y baja el jitter bajo carga.${N}"
echo -e " \e[2mNo reduce el ping base (eso lo fija la distancia a la VPS).${N}\n"
op 1 "⚡" "Activar"
op 2 "✖" "Desactivar"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) echo -e " \e[1;38;5;141mAplicando BBR...${N}"
if activar_bbr; then msg_ok "BBR activado (se mantiene tras reiniciar)"; else msg_err "No se pudo activar BBR"; fi
pausa ;;
2) desactivar_bbr; msg_ok "BBR desactivado (se volvió a cubic)"; pausa ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

menu_herramientas() {
while true; do
banner; echo -e " \e[1;38;5;141mHERRAMIENTAS${N}\n"
if bbr_activo; then
echo -e " \e[1;32m● BBR: activo${N}\n"
else
echo -e " \e[1;31m● BBR: inactivo${N}\n"
fi
op 1 "⚡" "BBR"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) menu_bbr ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}


while true; do
banner
op 1 "●" "Usuario"
op 2 "⚡" "Protocolos"
op 3 "🛠" "Herramientas"
op 0 "✖" "Salir"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) menu_usuario ;;
2) menu_protocolos ;;
3) menu_herramientas ;;
0) clear; exit 0 ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
