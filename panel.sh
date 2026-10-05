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

hora_vps() {
local dn=(Dom Lun Mar Mié Jue Vie Sáb)
echo "${dn[$(date +%w)]} $(date '+%d/%m/%Y  %H:%M:%S %Z')"
}

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
echo -e " \e[1;38;5;208mHora VPS:\e[0m $(hora_vps)"
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

# Hora del día de vencimiento en que se corta al usuario (21 por defecto).
hora_corte() {
local h
h=$(grep -m1 '^EXPIRE_HOUR=' "${ZUMO_LIMCONF:-/etc/zumo/limit.conf}" 2>/dev/null | cut -d= -f2)
[[ "$h" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || h=21
echo "$h"
}
# vencido_ya AAAA-MM-DD: 0 si ya pasó el día de vencimiento a esa hora.
vencido_ya() {
local e
e=$(date -d "$1 $(hora_corte):00" +%s 2>/dev/null) || return 1
[ "$(date +%s)" -ge "$e" ]
}
# La cuenta de Linux se deja vencer un día después: el corte exacto lo hace el limitador.
fecha_cuenta() { date -d "$1 +1 day" +%F; }

dias() {
if vencido_ya "$1"; then echo "vencido"; return; fi
local d=$(( ( $(date -d "$1" +%s) - $(date -d "$(date +%F)" +%s) ) / 86400 ))
if [ "$d" -eq 0 ]; then echo "vence hoy"; elif [ "$d" -eq 1 ]; then echo "vence 1 día"; else echo "vence $d días"; fi
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

# Contraseñas guardadas para mostrarlas en "Ver usuarios" (archivo solo root, modo 600).
# El sistema solo guarda el hash, por eso el panel anota la clave al crear/cambiar.
CLAVES="${ZUMO_CLAVES:-/etc/zumo/claves.db}"
clave_get() {
if es_hwid "$1"; then echo "$1"; return; fi
[ -f "$CLAVES" ] && awk -F: -v u="$1" '$1==u{print substr($0,length(u)+2); exit}' "$CLAVES"
}
clave_del() { [ -f "$CLAVES" ] && { awk -F: -v u="$1" '$1!=u' "$CLAVES" > "$CLAVES.tmp" && cat "$CLAVES.tmp" > "$CLAVES"; rm -f "$CLAVES.tmp"; }; return 0; }
clave_set() { # usuario clave
( umask 077; touch "$CLAVES"; chmod 600 "$CLAVES" 2>/dev/null )
clave_del "$1"
( umask 077; printf '%s:%s\n' "$1" "$2" >> "$CLAVES" )
}
clave_rename() { [ -f "$CLAVES" ] && awk -F: -v a="$1" -v b="$2" -v OFS=: '$1==a{$1=b}1' "$CLAVES" > "$CLAVES.tmp" && cat "$CLAVES.tmp" > "$CLAVES"; rm -f "$CLAVES.tmp"; return 0; }

en_linea() { ps -u "$1" -o comm= 2>/dev/null | grep -c '^sshd$'; }

# Validación de usuario/contraseña: solo letras y números, máximo 10, sin
# espacios ni símbolos. El usuario además empieza con letra (mayúscula o minúscula).
nombre_valido() { [[ "$1" =~ ^[A-Za-z][A-Za-z0-9]{0,9}$ ]]; }
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

# Usuarios.db ordenado alfabéticamente (HWID por el nombre del cliente), sin distinguir mayúsculas.
db_orden() {
awk -F: 'NR==FNR{ if($5 ~ /^hwid,/) l[$1]=substr($5,6); next } NF{ k=($1 in l)?l[$1]:$1; print tolower(k) "\t" $0 }' "${ZUMO_PASSWD:-/etc/passwd}" "$DB" | LC_ALL=C sort -s -t "$(printf '\t')" -k1,1 | cut -f2-
}

esta_bloqueado() {
local est
est=$(passwd -S "$1" 2>/dev/null | awk '{print $2}')
[ "$est" = "L" ]
}

elegir_usuario() {
mapfile -t USERS < <(db_orden | cut -d: -f1 | sed '/^$/d')
if [ ${#USERS[@]} -eq 0 ]; then msg_err "No hay usuarios registrados"; return 1; fi
for i in "${!USERS[@]}"; do
local etiq="$(etiqueta_de "${USERS[$i]}")"
if es_hwid "${USERS[$i]}"; then etiq="$etiq (HWID)"; fi
echo -e " \e[1;38;5;208m[$((i+1))]\e[0m \e[1;32m${etiq}\e[0m"
done
echo; read -rp " Número o nombre de usuario: " n
n="${n#"${n%%[![:space:]]*}"}"; n="${n%"${n##*[![:space:]]}"}"
[ -z "$n" ] && { msg_err "Opción inválida"; return 1; }
local rc h
buscar_usuario "$n"; rc=$?
case $rc in
0) return 0 ;;
2) msg_err "Hay varios clientes con el nombre \"$n\". Escribí el HWID:"
for h in "${AMBIGUOS[@]}"; do echo -e "     \e[1;38;5;214m$h${N}"; done
return 1 ;;
*) msg_err "No existe: $n"; return 1 ;;
esac
}

ip_publica() {
local ip
ip=$(curl -4 -fsS --max-time 3 https://api.ipify.org 2>/dev/null)
[[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || ip=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "${ip:-?}"
}

puertos_activos() {
local ssh out
ssh=$(awk 'tolower($1)=="port"{print $2; exit}' /etc/ssh/sshd_config 2>/dev/null)
out="SSH ${ssh:-22}"
systemctl is-active --quiet pdirect-80 2>/dev/null && out+=" · WebSocket 80"
systemctl is-active --quiet udpgw-7300 2>/dev/null && out+=" · BadVPN 7300"
if systemctl is-active --quiet bhttp-server 2>/dev/null && systemctl is-active --quiet bhttp-shim 2>/dev/null; then out+=" · BHTTP $(bhttp_port)"; fi
systemctl is-active --quiet hcr-server 2>/dev/null && out+=" · HCR $(hcr_port)"
echo "$out"
}

# mensaje_cliente USUARIO CLAVE VENCE_TEXTO — bloque listo para copiar y mandar.
mensaje_cliente() {
local u="$1" clave="$2" vence="$3" lim
lim=$(zumo_db_campo "$u" 2)
echo -e " $L"
echo -e " \e[1;38;5;141mDATOS PARA EL CLIENTE${N}"
echo -e " \e[1;38;5;208mServidor:\e[0m     \e[1;38;5;214m$(ip_publica)${N}"
if es_hwid "$u"; then
echo -e " \e[1;38;5;208mCliente:\e[0m      \e[1;38;5;214m$(etiqueta_de "$u")${N}"
echo -e " \e[1;38;5;208mUsuario y clave:\e[0m \e[1;38;5;214m$u${N}"
else
echo -e " \e[1;38;5;208mUsuario:\e[0m      \e[1;38;5;214m$u${N}"
echo -e " \e[1;38;5;208mContraseña:\e[0m   \e[1;38;5;214m${clave:-(la que le diste)}${N}"
fi
echo -e " \e[1;38;5;208mVence:\e[0m        \e[1;38;5;214m$vence${N}"
echo -e " \e[1;38;5;208mConexiones:\e[0m   \e[1;38;5;214m${lim:-1}${N}"
echo -e " \e[1;38;5;208mPuertos:\e[0m      \e[1;38;5;214m$(puertos_activos)${N}"
echo -e " $L"
}

# Mensaje corto para usuarios comunes: usuario, contraseña, fecha (dd/mm),
# dispositivos y el banner del 101 (el de PDirect).
mensaje_comun() {
local u="$1" clave="$2" fecha="$3" lim ban
lim=$(zumo_db_campo "$u" 2); lim=${lim:-1}
ban=$(grep -m1 '^PDIRECT_BANNER=' "${ZUMO_PDIRECT_ENV:-/etc/zumo/pdirect.env}" 2>/dev/null | cut -d= -f2-)
ban=${ban:-ZUMO}
echo
echo -e " 👤 \e[1;38;5;214m$u${N}"
echo -e " 🔒 \e[1;38;5;214m$clave${N}"
echo -e " 📅 \e[1;38;5;214m$fecha${N}"
if [ "$lim" -eq 1 ]; then echo -e " 🔌 \e[1;38;5;214m1 dispositivo${N}"; else echo -e " 🔌 \e[1;38;5;214m$lim dispositivos${N}"; fi
echo -e " 📄 \e[1;38;5;214m$ban${N}"
echo
}

# Mensaje para usuarios HWID: el "Usuario" es el nombre del cliente y la
# "Máquina" es el banner del 101 (el de PDirect).
mensaje_hwid() {
local u="$1" vence="$2" ban nombre
ban=$(grep -m1 '^PDIRECT_BANNER=' "${ZUMO_PDIRECT_ENV:-/etc/zumo/pdirect.env}" 2>/dev/null | cut -d= -f2-)
ban=${ban:-ZUMO}
nombre=$(etiqueta_de "$u")
echo
echo -e " 🔐 \e[1;38;5;214mDATOS DE ACCESO${N}"
echo -e " ├ ☁️ Plan: \e[1;38;5;214mPrivado${N}"
echo -e " ├ ⚙️ Máquina: \e[1;38;5;214m$ban${N}"
echo -e " ├ 👤 Usuario: \e[1;38;5;214m$nombre${N}"
echo -e " ├ ⏳ Vence: \e[1;38;5;214m$vence${N}"
echo
}

# Mismo mensaje para el cliente que al crear el usuario, pero con el nuevo vencimiento.
mensaje_renovacion() { # usuario vencimiento(AAAA-MM-DD)
local u="$1" exp="$2" clave
if es_hwid "$u"; then mensaje_hwid "$u" "$(date -d "$exp" +%d/%m/%Y)"
else
clave="$(clave_get "$u")"; [ -n "$clave" ] || clave="(su clave)"
mensaje_comun "$u" "$clave" "$(date -d "$exp" +%d/%m)"
fi
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
read -rp " Límite de conexiones [2]: " lim; lim=${lim:-2}
[[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido (mínimo 1)"; pausa; return; }
exp=$(date -d "+$d days" +%F)
if ! useradd --badname -M -s /bin/false -e "$(fecha_cuenta "$exp")" -c "hwid,$etiqueta" "$hwid" 2>/dev/null; then
msg_err "No se pudo crear el usuario (probá con otro HWID)"; pausa; return
fi
echo "$hwid:$hwid" | chpasswd
zumo_db_add "$hwid" "$lim" "$exp"
echo; msg_ok "Usuario HWID creado"
mensaje_hwid "$hwid" "$(date -d "$exp" +%d/%m/%Y)"
pausa
return
fi

leer_alnum "Usuario: " 10; u="$REPLY_ALNUM"
nombre_valido "$u" || { msg_err "Usuario inválido (debe empezar con letra)"; pausa; return; }
id "$u" &>/dev/null && { msg_err "El usuario ya existe"; pausa; return; }
leer_alnum "Contraseña: " 10; p="$REPLY_ALNUM"
clave_valida "$p" || { msg_err "Contraseña inválida (no puede estar vacía)"; pausa; return; }
read -rp " Días de duración: " d
[[ "$d" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; pausa; return; }
read -rp " Límite de conexiones [1]: " lim; lim=${lim:-1}
[[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido (mínimo 1)"; pausa; return; }
exp=$(date -d "+$d days" +%F)
if ! useradd $([[ "$u" =~ ^[a-z][a-z0-9]*$ ]] || echo --badname) -M -s /bin/false -e "$(fecha_cuenta "$exp")" "$u" 2>/dev/null; then
msg_err "No se pudo crear el usuario"; pausa; return
fi
echo "$u:$p" | chpasswd
clave_set "$u" "$p"
zumo_db_add "$u" "$lim" "$exp"
echo; msg_ok "Usuario creado"
mensaje_comun "$u" "$p" "$(date -d "$exp" +%d/%m)"
pausa
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
if ! useradd --badname -M -s /bin/false -e "$(fecha_cuenta "$exp")" -c "hwid,$etiqueta" "$hwid" 2>/dev/null; then
msg_err "No se pudo crear el usuario temporal"; pausa; return
fi
echo "$hwid:$hwid" | chpasswd
zumo_db_add "$hwid" 1 "$exp"
programar_borrado_temp "$hwid" "$min"
echo; msg_ok "Usuario HWID temporal creado"
mensaje_hwid "$hwid" "$( [ "$min" -eq 1 ] && echo "1 minuto" || echo "$min minutos" )"
pausa
return
fi

leer_alnum "Usuario: " 10; u="$REPLY_ALNUM"
nombre_valido "$u" || { msg_err "Usuario inválido (debe empezar con letra)"; pausa; return; }
id "$u" &>/dev/null && { msg_err "El usuario ya existe"; pausa; return; }
leer_alnum "Contraseña: " 10; p="$REPLY_ALNUM"
clave_valida "$p" || { msg_err "Contraseña inválida (no puede estar vacía)"; pausa; return; }
read -rp " Minutos de duración: " min
[[ "$min" =~ ^[0-9]+$ ]] && [ "$min" -ge 1 ] || { msg_err "Minutos inválidos"; pausa; return; }
read -rp " Conexiones permitidas [1]: " lim; lim=${lim:-1}
[[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido"; pausa; return; }
exp=$(date -d "+2 days" +%F)
if ! useradd $([[ "$u" =~ ^[a-z][a-z0-9]*$ ]] || echo --badname) -M -s /bin/false -e "$(fecha_cuenta "$exp")" "$u" 2>/dev/null; then
msg_err "No se pudo crear el usuario temporal"; pausa; return
fi
echo "$u:$p" | chpasswd
clave_set "$u" "$p"
zumo_db_add "$u" "$lim" "$exp"
programar_borrado_temp "$u" "$min"
echo; msg_ok "Usuario temporal creado"
mensaje_comun "$u" "$p" "$( [ "$min" -eq 1 ] && echo "1 minuto" || echo "$min minutos" )"
pausa
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
done < <(db_orden)
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
done < <(db_orden)
# 2) número de la lista (mismo orden que lista_para_borrar)
if [[ "$q" =~ ^[0-9]+$ ]]; then
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
i=$((i+1))
if [ "$i" -eq "$((10#$q))" ]; then SEL="$u"; return 0; fi
done < <(db_orden)
return 1
fi
# 3) nombre del cliente (HWID)
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
es_hwid "$u" || continue
lab=$(etiqueta_de "$u")
[ "${lab,,}" = "${q,,}" ] && hits+=("$u")
done < <(db_orden)
if [ ${#hits[@]} -eq 1 ]; then SEL="${hits[0]}"; return 0; fi
if [ ${#hits[@]} -gt 1 ]; then AMBIGUOS=("${hits[@]}"); return 2; fi
# 4) usuario sin distinguir mayúsculas
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
es_hwid "$u" && continue
if [ "${u,,}" = "${q,,}" ]; then SEL="$u"; return 0; fi
done < <(db_orden)
return 1
}

# Saca al usuario del sistema, de la base y de los temporales.
borrar_usuario_completo() {
local u="$1"
pkill -9 -u "$u" 2>/dev/null
userdel "$u" 2>/dev/null
zumo_db_del "$u"
clave_del "$u"
( flock -w 5 9; [ -f /etc/zumo/datos.db ] && awk -F: -v u="$u" '$1!=u' /etc/zumo/datos.db > /etc/zumo/datos.db.tmp && mv -f /etc/zumo/datos.db.tmp /etc/zumo/datos.db ) 9>/etc/zumo/datos.lock 2>/dev/null
( flock -w 5 9; [ -f /etc/zumo/datos-hist.db ] && awk -F: -v u="$u" '$2!=u' /etc/zumo/datos-hist.db > /etc/zumo/datos-hist.db.tmp && mv -f /etc/zumo/datos-hist.db.tmp /etc/zumo/datos-hist.db ) 9>/etc/zumo/datos.lock 2>/dev/null
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
VENC=()
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
vencido_ya "$exp" && VENC+=("$u|$exp")
done < <(db_orden)
if [ ${#VENC[@]} -eq 0 ]; then msg_ok "No hay usuarios vencidos"; pausa; return; fi
printf " \e[1;38;5;208m%-4s %-16s %s${N}\n" "" "USUARIO/CLIENTE" "VENCIÓ"
local n=0
for item in "${VENC[@]}"; do
n=$((n+1))
printf " \e[1;38;5;208m[%s]${N} \e[1;31m%-16s %s${N}\n" "$n" "$(etiqueta_de "${item%%|*}")" "${item#*|}"
done
echo; echo -e " $L"
op 1 "✖" "Borrar usuarios vencidos"
op 2 "↻" "Renovar un usuario"
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
clave_del "$u"
done
msg_ok "${#VENC[@]} usuario(s) vencido(s) eliminado(s)"; pausa ;;
2) local nr nd nexp ur
read -rp " Número a renovar: " nr
if ! [[ "$nr" =~ ^[0-9]+$ ]] || [ "$nr" -lt 1 ] || [ "$nr" -gt ${#VENC[@]} ]; then msg_err "Número inválido"; pausa; return; fi
ur="${VENC[$((nr-1))]%%|*}"
read -rp " Días desde hoy: " nd
[[ "$nd" =~ ^[0-9]+$ ]] && [ "$nd" -ge 1 ] || { msg_err "Días inválidos"; pausa; return; }
nexp=$(date -d "+$nd days" +%F)
usermod -e "$(fecha_cuenta "$nexp")" "$ur" 2>/dev/null
zumo_db_set "$ur" 3 "$nexp"
datos_reset "$ur"
msg_ok "$(etiqueta_de "$ur") renovado hasta $nexp ($(dias "$nexp"))"
mensaje_renovacion "$ur" "$nexp"; pausa ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
}

datos_de() { awk -F: -v u="$1" '$1==u{print $2}' "${ZUMO_DATOS:-/etc/zumo/datos.db}" 2>/dev/null; }
# datos_reset USUARIO = poner el contador en 0 (al renovar); datos_rename VIEJO NUEVO
datos_reset() {
local f="${ZUMO_DATOS:-/etc/zumo/datos.db}"
( flock -w 5 9; [ -f "$f" ] && awk -F: -v u="$1" '$1!=u' "$f" > "$f.tmp" && mv -f "$f.tmp" "$f" ) 9>"${ZUMO_DATOS_LOCK:-/etc/zumo/datos.lock}" 2>/dev/null
}
datos_rename() {
local f="${ZUMO_DATOS:-/etc/zumo/datos.db}"
( flock -w 5 9; [ -f "$f" ] && awk -F: -v a="$1" -v b="$2" -v OFS=: '$1==a{$1=b}1' "$f" > "$f.tmp" && mv -f "$f.tmp" "$f" ) 9>"${ZUMO_DATOS_LOCK:-/etc/zumo/datos.lock}" 2>/dev/null
local h="${ZUMO_HIST:-/etc/zumo/datos-hist.db}"
( flock -w 5 9; [ -f "$h" ] && awk -F: -v a="$1" -v b="$2" -v OFS=: '$2==a{$2=b}1' "$h" > "$h.tmp" && mv -f "$h.tmp" "$h" ) 9>"${ZUMO_DATOS_LOCK:-/etc/zumo/datos.lock}" 2>/dev/null
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
clave_set "$SEL" "$np"
msg_ok "Contraseña de $SEL actualizada"; sleep 1 ;;
2) read -rp " Nuevo límite para $SEL [$lim]: " nl; nl=${nl:-$lim}
[[ "$nl" =~ ^[0-9]+$ ]] && [ "$nl" -ge 1 ] || { msg_err "Límite inválido"; sleep 1; continue; }
zumo_db_set "$SEL" 2 "$nl"
msg_ok "Límite de $SEL ahora es $nl"; sleep 1 ;;
3) read -rp " Días desde hoy: " nd
[[ "$nd" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; sleep 1; continue; }
nexp=$(date -d "+$nd days" +%F)
usermod -e "$(fecha_cuenta "$nexp")" "$SEL" 2>/dev/null
zumo_db_set "$SEL" 3 "$nexp"
datos_reset "$SEL"
msg_ok "Vencimiento de $SEL ahora: $nexp ($(dias "$nexp")). Contador de datos en 0"
mensaje_renovacion "$SEL" "$nexp"; pausa ;;
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
clave_rename "$SEL" "$nh"
datos_rename "$SEL" "$nh"
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

# Dos líneas por usuario: [n] ● nombre + clave; debajo vencimiento, límite y tiempo conectado.
_fmt_secs() { # segundos -> 5m / 1h 5m / 2d 3h
local s="$1" d h m
d=$(( s/86400 )); h=$(( (s%86400)/3600 )); m=$(( (s%3600)/60 ))
if [ "$d" -gt 0 ]; then echo "${d}d ${h}h"; elif [ "$h" -gt 0 ]; then echo "${h}h ${m}m"; else echo "${m}m"; fi
}

_ficha_usuario() { # n usuario límite vencimiento
local n="$1" u="$2" lim="$3" exp="$4" on dot venc venc_col tc nombre clave con="" esw=0 esT=0
[ -n "${_SEP:-}" ] || _SEP=$(printf '─%.0s' $(seq 1 40))
echo -e " \e[38;5;60m${_SEP}${N}"
if [ -n "${_PRE:-}" ]; then
on=${_ON[$u]:-0}; tc=""; [ "$on" -gt 0 ] && tc=$(_fmt_secs "${_TS[$u]:-0}")
[[ -v _GE[$u] ]] && esw=1; [[ -v _TM[$u] ]] && esT=1
else
on=$(en_linea "$u"); tc=""; [ "$on" -gt 0 ] && tc=$(tiempo_conectado "$u")
es_hwid "$u" && esw=1; es_temporal "$u" && esT=1
fi
if [ "$on" -gt 0 ]; then
dot="\e[1;32m●${N}"
[ -n "$tc" ] && con="  \e[1;32m${tc}${N}"
else dot="\e[1;31m●${N}"; fi
if [ "$esT" -eq 1 ]; then
if [ -n "${_PRE:-}" ]; then local m=$(( (_TM[$u] - $(printf '%(%s)T' -1) + 59) / 60 )); if [ "$m" -le 0 ]; then venc="venció"; else venc="${m}m restantes"; fi
else venc="$(temp_restante "$u")"; fi
venc_col="\e[1;38;5;214m"
else
if [ -n "${_PRE:-}" ]; then
[[ -v _DC[$exp] ]] || _DC[$exp]=$(dias "$exp")
venc="${_DC[$exp]}"
else venc="$(dias "$exp")"; fi
case "$venc" in
vencido) venc_col="\e[1;31m" ;;
"vence hoy"|"vence 1 día"|"vence 2 días"|"vence 3 días") venc_col="\e[1;38;5;214m" ;;
*) venc_col="\e[1;32m" ;;
esac
venc="${exp:8:2}/${exp:5:2}/${exp:0:4}"
fi
if [ -n "${_PRE:-}" ]; then
if [ "$esw" -eq 1 ]; then nombre="${_GE[$u]}"; clave="$u"; else nombre="$u"; clave="${_CL[$u]:-}"; fi
else
nombre="$(etiqueta_de "$u")"; clave="$(clave_get "$u")"
fi
[ -n "$clave" ] || clave="-"
if [ "$esw" -eq 1 ]; then
echo -e " \e[1;38;5;208m[$n]${N} $dot \e[1;97m$nombre${N}"
echo -e "      \e[2mHWID:${N} \e[1;38;5;214m$u${N}"
else
echo -e " \e[1;38;5;208m[$n]${N} $dot \e[1;97m$nombre${N}  \e[2m·${N} \e[1;96m$clave${N}"
fi
if [ "$esw" -eq 1 ]; then
echo -e "      \e[2mVence:${N} ${venc_col}${venc}${N}$con"   # HWID: sin límite a la vista
else
echo -e "      \e[2mVence:${N} ${venc_col}${venc}${N}  \e[2mLímite:${N} \e[1;97m${on}/${lim}${N}$con"
fi
}

listar_usuarios() {
if [ ! -s "$DB" ]; then banner; echo -e " \e[1;38;5;141mUSUARIOS REGISTRADOS${N}\n"; msg_err "No hay usuarios"; pausa; return; fi
# Se prepara todo de una vez (sesiones, HWID, claves, temporales) y se dibuja la lista entera junta.
declare -gA _ON=() _TS=() _GE=() _CL=() _TM=() _DC=()
local k a b l
while read -r k a b; do _ON[$k]=$a; _TS[$k]=$b; done < <(ps -eo user:32=,comm=,etimes= 2>/dev/null | awk '$2=="sshd"{c[$1]++; if($3>m[$1])m[$1]=$3} END{for(u in c) print u, c[u], m[u]}')
while IFS=$'\t' read -r k a; do _GE[$k]=$a; done < <(awk -F: '$5 ~ /^hwid,/{print $1 "\t" substr($5,6)}' "${ZUMO_PASSWD:-/etc/passwd}")
[ -f "$CLAVES" ] && while IFS= read -r l; do [ -n "$l" ] && _CL[${l%%:*}]=${l#*:}; done < "$CLAVES"
[ -f "$TEMPDB" ] && while IFS=: read -r k a; do [ -n "$k" ] && _TM[$k]=$a; done < "$TEMPDB"
_PRE=1
local out
out=$(
echo -e " \e[1;38;5;141mUSUARIOS REGISTRADOS${N}\n"
local u lim exp hay_comun=0 hay_hwid=0 n=0
echo -e " \e[1;38;5;141m━━━ COMÚN ━━━${N}\n"
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
[[ -v _GE[$u] ]] && continue
hay_comun=1; n=$((n+1))
_ficha_usuario "$n" "$u" "$lim" "$exp"
done < <(db_orden)
[ "$hay_comun" -eq 0 ] && echo -e " \e[2m(sin usuarios comunes)${N}\n"
echo -e " \e[1;38;5;141m━━━ HWID ━━━${N}\n"
n=0
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
[[ -v _GE[$u] ]] || continue
hay_hwid=1; n=$((n+1))
_ficha_usuario "$n" "$u" "$lim" "$exp"
done < <(db_orden)
[ "$hay_hwid" -eq 0 ] && echo -e " \e[2m(sin usuarios HWID)${N}\n"
echo -e " $L"
)
_PRE=""
banner
printf '%s\n' "$out"
echo -e "\n Enter para volver..."
read -rsn1 _
}

menu_usuario() {
while true; do
banner; echo -e " \e[1;38;5;141mUSUARIO${N}\n"
op 1 "✚" "Crear usuario"
op 2 "✖" "Eliminar usuario"
op 3 "✎" "Editar usuario"
op 4 "▤" "Ver usuarios"
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

# Test de velocidad con curl contra el servidor de Cloudflare (no instala nada).
# Mide con 4 conexiones en paralelo de pedidos chicos (25 MB de bajada y 10 MB
# de subida cada una): evita el tope de tamaño por pedido del servidor y mide
# mejor en conexiones rápidas. ZUMO_SPEED_URL solo se usa en pruebas.
_medir_velocidad() {
local modo="$1" base="$2" tmp i t0 t1 total
local -a pids=()
tmp=$(mktemp -d)
t0=$(date +%s.%N)
for i in 1 2 3 4; do
if [ "$modo" = "bajada" ]; then
curl -s -o /dev/null -w '%{http_code} %{size_download}\n' --max-time 20 \
"$base/__down?bytes=25000000" > "$tmp/$i" 2>/dev/null &
else
head -c 10000000 /dev/zero | curl -s -o /dev/null -w '%{http_code} %{size_upload}\n' --max-time 20 \
-X POST -H 'Content-Type: application/octet-stream' --data-binary @- "$base/__up" > "$tmp/$i" 2>/dev/null &
fi
pids+=($!)
done
wait "${pids[@]}" 2>/dev/null
t1=$(date +%s.%N)
total=$(cat "$tmp"/* 2>/dev/null | awk '$1==200{s+=$2} END{printf "%d", s}')
rm -rf "$tmp"
LC_ALL=C awk -v b="${total:-0}" -v a="$t0" -v z="$t1" \
'BEGIN{d=z-a; if(d<=0||b<=0){print "0.0"} else printf "%.1f", b*8/d/1000000}'
}

test_velocidad() {
local base="${ZUMO_SPEED_URL:-https://speed.cloudflare.com}" lat lat_ms dl_m ul_m
banner; echo -e " \e[1;38;5;141mTEST DE VELOCIDAD${N}\n"
command -v curl >/dev/null 2>&1 || { msg_err "Falta curl"; pausa; return; }
echo -e " \e[2mUsa hasta 100 MB de bajada y 40 MB de subida.${N}\n"
echo " Midiendo latencia..."
lat=$(curl -s -o /dev/null -w '%{time_connect}' --max-time 10 "$base/__down?bytes=0" 2>/dev/null)
echo " Midiendo bajada..."
dl_m=$(_medir_velocidad bajada "$base")
echo " Midiendo subida..."
ul_m=$(_medir_velocidad subida "$base")
echo
lat_ms=$(LC_ALL=C awk -v t="${lat:-0}" 'BEGIN{printf "%.0f", t*1000}')
if [ "$dl_m" = "0.0" ] && [ "$ul_m" = "0.0" ]; then
msg_err "No se pudo medir (¿sin internet o bloqueado?)"
else
echo -e " \e[1;38;5;208mLatencia:\e[0m \e[1;32m${lat_ms} ms\e[0m"
if [ "$dl_m" = "0.0" ]; then echo -e " \e[1;38;5;208mBajada:\e[0m   \e[1;31mno se pudo medir\e[0m"
else echo -e " \e[1;38;5;208mBajada:\e[0m   \e[1;32m${dl_m} Mbps\e[0m"; fi
if [ "$ul_m" = "0.0" ]; then echo -e " \e[1;38;5;208mSubida:\e[0m   \e[1;31mno se pudo medir\e[0m"
else echo -e " \e[1;38;5;208mSubida:\e[0m   \e[1;32m${ul_m} Mbps\e[0m"; fi
fi
pausa
}

# Vacía la caché de memoria y limpia logs y paquetes viejos. La RAM que usan los
# programas no se toca: eso depende de qué esté corriendo (ver "Procesos").
liberar_ram() {
banner; echo -e " \e[1;38;5;141mLIBERAR RAM Y LIMPIAR${N}\n"
local libre0 libre1 cache0 cache1 disco0 disco1 lib
libre0=$(free -m | awk '/^Mem:/{print $4}')
cache0=$(free -m | awk '/^Mem:/{print $6}')
disco0=$(df -Pm / | awk 'NR==2{print $4}')
sync
if ! { echo 3 > /proc/sys/vm/drop_caches; } 2>/dev/null; then
msg_err "Este servidor no permite vaciar la caché"
fi
journalctl --vacuum-size=50M >/dev/null 2>&1
apt-get clean >/dev/null 2>&1
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' \) -delete 2>/dev/null
libre1=$(free -m | awk '/^Mem:/{print $4}')
cache1=$(free -m | awk '/^Mem:/{print $6}')
disco1=$(df -Pm / | awk 'NR==2{print $4}')
lib=$(( cache0 - cache1 )); [ "$lib" -lt 0 ] && lib=0
echo -e " \e[1;38;5;208mRAM libre:\e[0m      ${libre0} → \e[1;32m${libre1} MB\e[0m"
echo -e " \e[1;38;5;208mCaché liberada:\e[0m \e[1;32m${lib} MB\e[0m"
echo -e " \e[1;38;5;208mDisco libre:\e[0m    ${disco0} → \e[1;32m${disco1} MB\e[0m"
echo -e "\n \e[2mLimpia caché y paquetes obsoletos.${N}"
pausa
}

# Descripción corta de qué es un proceso, según su nombre.
desc_proceso() {
case "$1" in
sshd|sshd-session) echo "SSH (conexiones)" ;;
badvpn-udpgw) echo "BadVPN (UDP)" ;;
pdirect-c) echo "PDirect (WebSocket)" ;;
bhttp-server|bhttp-shim) echo "BHTTP" ;;
hcr-server) echo "HCR Server" ;;
zumo-limit) echo "Limitador" ;;
zumo) echo "Este panel" ;;
systemd) echo "Sistema (arranque)" ;;
systemd-journal*) echo "Registros del sistema" ;;
systemd-logind) echo "Sesiones de login" ;;
systemd-udevd) echo "Dispositivos" ;;
systemd-resolve*) echo "DNS del sistema" ;;
systemd-timesyn*|chronyd|ntpd) echo "Hora del sistema" ;;
systemd-network*) echo "Red del sistema" ;;
dbus-daemon) echo "Mensajes del sistema" ;;
cron|crond) echo "Tareas programadas" ;;
rsyslogd) echo "Registros (syslog)" ;;
agetty|login) echo "Consola" ;;
python3|python) echo "Python" ;;
fail2ban*) echo "Anti fuerza bruta" ;;
unattended-upgr*) echo "Actualizaciones" ;;
apt|apt-get|dpkg) echo "Paquetes (apt)" ;;
kworker*|ksoftirqd*|kswapd*|rcu_*|migration*) echo "Kernel" ;;
*) echo "-" ;;
esac
}

# Foto de la CPU usada por cada proceso: "pid nombre ticks" (usuario + sistema).
_snap_cpu() {
awk '{
line=$0; sub(/^[0-9]+ \(/, "", line)
i=match(line, /\)[^)]*$/); name=substr(line, 1, i-1); rest=substr(line, i+2)
gsub(/ /, "_", name); split(rest, f, " "); print $1, name, f[12]+f[13]
}' /proc/[0-9]*/stat 2>/dev/null
}

# Color según el porcentaje: verde < 70, amarillo < 90, rojo desde 90.
_col_pct() {
LC_ALL=C awk -v p="$1" 'BEGIN{ if (p >= 90) print "\033[1;31m"; else if (p >= 70) print "\033[1;33m"; else print "\033[1;32m" }'
}

# Los 5 procesos que más RAM y más CPU usan, con qué es cada uno, y al final el
# uso real de RAM y CPU. La CPU se mide en vivo durante 1 segundo.
procesos_top() {
local tmp t0 t1 dt cores hz cpu_real mt mu ram_pct pid comm val d rc primera=1 buf k
while true; do
[ "$primera" = 1 ] && { banner; echo -e " \e[1;38;5;141mUSO DE CPU Y RAM${N}\n"; echo -e " \e[2mMidiendo...${N}"; }
primera=0
tmp=$(mktemp -d)
cores=$(nproc 2>/dev/null || echo 1)
hz=$(getconf CLK_TCK 2>/dev/null || echo 100)
head -n1 /proc/stat > "$tmp/s0"; _snap_cpu > "$tmp/p0"; t0=$(date +%s.%N)
sleep 1
head -n1 /proc/stat > "$tmp/s1"; _snap_cpu > "$tmp/p1"; t1=$(date +%s.%N)
dt=$(LC_ALL=C awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
cpu_real=$(LC_ALL=C awk 'NR==FNR{for(i=2;i<=9;i++)a+=$i; i0=$5+$6; next}
{for(i=2;i<=9;i++)b+=$i; i1=$5+$6}
END{dt=b-a; di=i1-i0; if(dt<=0) print "0.0"; else printf "%.1f", (dt-di)*100/dt}' "$tmp/s0" "$tmp/s1")
buf=$(

echo -e " \e[1;38;5;208mMás RAM:${N}"
printf ' \e[2m%6s %-15s %5s  %s\e[0m\n' "PID" "PROCESO" "%RAM" "DE QUÉ ES"
while read -r pid comm val; do
[ -z "$pid" ] && continue
printf ' %6s %-15s %5s  \e[2m%s\e[0m\n' "$pid" "${comm:0:15}" "$val" "$(desc_proceso "$comm")"
done < <(ps -eo pid=,comm=,%mem= --sort=-%mem 2>/dev/null | head -n 5)

echo; echo -e " \e[1;38;5;208mMás CPU:${N}"
printf ' \e[2m%6s %-15s %5s  %s\e[0m\n' "PID" "PROCESO" "%CPU" "DE QUÉ ES"
while read -r pid comm val; do
[ -z "$pid" ] && continue
printf ' %6s %-15s %5s  \e[2m%s\e[0m\n' "$pid" "${comm:0:15}" "$val" "$(desc_proceso "$comm")"
done < <(LC_ALL=C awk -v dt="$dt" -v hz="$hz" -v c="$cores" 'NR==FNR{a[$1]=$3; next}
($1 in a){ printf "%d %s %.1f\n", $1, $2, ($3-a[$1])/hz/dt*100/c }' "$tmp/p0" "$tmp/p1" | sort -k3 -nr | head -n 5)

read -r mt mu <<< "$(free -m | awk '/^Mem:/{print $2, $3}')"
ram_pct=$(LC_ALL=C awk -v u="${mu:-0}" -v t="${mt:-1}" 'BEGIN{printf "%.1f", u*100/t}')
echo; echo -e " $L"
echo -e " \e[1;38;5;208mUso real ahora:${N}"
echo -e "   RAM: $(_col_pct "$ram_pct")${ram_pct}%\e[0m  (${mu} de ${mt} MB)"
echo -e "   CPU: $(_col_pct "$cpu_real")${cpu_real}%\e[0m  (medido en 1 s, de todos los núcleos)"

)
rm -rf "$tmp"
banner; echo -e " \e[1;38;5;141mUSO DE CPU Y RAM${N}\n"
printf '%s\n' "$buf"
echo -e "\n \e[1;38;5;208m[L]${N} Liberar RAM y limpiar     \e[2mEnter para volver...${N}"
k=""; read -rsn1 -t 1 k; rc=$?
if [ "$rc" -eq 0 ] && [[ "$k" =~ ^[lL]$ ]]; then liberar_ram; primera=1; continue; fi
[ "$rc" -gt 128 ] || break
done
}

_fmt_bytes() {
awk -v b="$1" 'BEGIN{ if (b>=1073741824) printf "%.2f GB", b/1073741824; else printf "%.1f MB", b/1048576 }'
}

uso_datos() {
local DATOS="${ZUMO_DATOS:-/etc/zumo/datos.db}" HIST="${ZUMO_HIST:-/etc/zumo/datos-hist.db}"
local u b d total totd n rc k hoy hh
if [ ! -s "$DB" ]; then msg_err "No hay usuarios registrados"; pausa; return; fi
while true; do
hoy=$(date +%F)
banner; echo -e " \e[1;38;5;141mUSO DE DATOS${N}\n"
if ! systemctl is-active --quiet zumo-datos 2>/dev/null; then
echo -e " \e[1;31m● El contador no está activo (actualizá con actualizar.sh)${N}\n"
fi
hh=$(awk -F: -v h="$hoy" '$1==h{d[$2]+=$3} END{for(u in d) printf "%s %.0f\n", u, d[u]}' "$HIST" 2>/dev/null)
total=0; totd=0; n=0
printf " \e[1;38;5;208m%-10s %9s %9s${N}\n" "USUARIO" "HOY" "TOTAL"
while IFS=: read -r u _; do
[ -z "$u" ] && continue
b=$(awk -F: -v u="$u" '$1==u{print $2}' "$DATOS" 2>/dev/null); b=${b:-0}
d=$(awk -v u="$u" '$1==u{print $2}' <<<"$hh"); d=${d:-0}
total=$(awk -v a="$total" -v b="$b" 'BEGIN{printf "%.0f", a+b}')
totd=$(awk -v a="$totd" -v b="$d" 'BEGIN{printf "%.0f", a+b}')
n=$((n+1))
printf " \e[1;32m%-10s\e[0m \e[1;38;5;51m%9s %9s${N}\n" "$(etiqueta_de "$u" | cut -c1-10)" "$(_fmt_bytes "$d")" "$(_fmt_bytes "$b")"
done < "$DB"
echo; echo -e " $L"
printf " \e[1;38;5;214m%-10s\e[0m \e[1;38;5;51m%9s %9s${N}\n" "TOTAL($n)" "$(_fmt_bytes "$totd")" "$(_fmt_bytes "$total")"
echo -e "\n Enter para volver..."
read -rsn1 -t 2 k; rc=$?
[ "$rc" -gt 128 ] && continue
break
done
}

# ---------------------------------------------------------------- Respaldo
RESP_DIR="${ZUMO_RESP_DIR:-/etc/zumo/respaldos}"

# Cambia las filas de los usuarios de $2 (backup) dentro de $1 (actual).
# $3 = columna donde está el usuario (1 = datos.db, 2 = datos-hist.db).
_merge_por_usuario() {
local actual="$1" backup="$2" col="$3" us="$4"
[ -s "$backup" ] || return 0
touch "$actual"
{ awk -F: -v col="$col" -v us="$us" 'BEGIN{n=split(us,a," "); for(i=1;i<=n;i++) m[a[i]]=1} !($col in m)' "$actual"
awk -F: -v col="$col" -v us="$us" 'BEGIN{n=split(us,a," "); for(i=1;i<=n;i++) m[a[i]]=1} ($col in m)' "$backup"; } > "$actual.tmp" && mv -f "$actual.tmp" "$actual"
}

# _respaldo_crear ARCHIVO CLAVE — guarda usuarios (con su contraseña cifrada),
# vencimientos, temporales, datos y configuración, todo cifrado con la clave.
_respaldo_crear() {
local out="$1" clave="$2" w u hash gecos n=0 f
w=$(mktemp -d)
: > "$w/cuentas.txt"
while IFS=: read -r u _; do
[ -z "$u" ] && continue
getent passwd "$u" >/dev/null 2>&1 || continue
hash=$(getent shadow "$u" | cut -d: -f2)
gecos=$(getent passwd "$u" | cut -d: -f5)
printf '%s:%s:%s\n' "$u" "$hash" "$gecos" >> "$w/cuentas.txt"
n=$((n+1))
done < "$DB"
cp "$DB" "$w/usuarios.db"
[ -f "$TEMPDB" ] && cp "$TEMPDB" "$w/temporales.db"
[ -f "$CLAVES" ] && cp "$CLAVES" "$w/claves.db"
[ -f "${ZUMO_DATOS:-/etc/zumo/datos.db}" ] && cp "${ZUMO_DATOS:-/etc/zumo/datos.db}" "$w/datos.db"
[ -f "${ZUMO_HIST:-/etc/zumo/datos-hist.db}" ] && cp "${ZUMO_HIST:-/etc/zumo/datos-hist.db}" "$w/datos-hist.db"
[ -f "${ZUMO_LIMCONF:-/etc/zumo/limit.conf}" ] && cp "${ZUMO_LIMCONF:-/etc/zumo/limit.conf}" "$w/limit.conf"
tar czf "$w/datos.tgz" -C "$w" --exclude=datos.tgz . 2>/dev/null
mkdir -p "$(dirname "$out")"
ZBK="$clave" openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:ZBK -in "$w/datos.tgz" -out "$out" 2>/dev/null
local rc=$?
rm -rf "$w"
[ $rc -eq 0 ] && chmod 600 "$out"
BK_CUENTAS=$n
return $rc
}

# _respaldo_restaurar ARCHIVO CLAVE — recrea usuarios, contraseñas, vencimientos,
# temporales y datos. Los usuarios que ya existen no se tocan (se actualiza su
# límite y vencimiento en la base).
_respaldo_restaurar() {
local arch="$1" clave="$2" w u hash gecos lim exp nuevos=0 existentes=0 vencidos=0 us="" ep now min
local -a args
local -A TEMP=()
w=$(mktemp -d)
if ! ZBK="$clave" openssl enc -d -aes-256-cbc -pbkdf2 -pass env:ZBK -in "$arch" 2>/dev/null | tar xz -C "$w" 2>/dev/null || [ ! -s "$w/cuentas.txt" ]; then
rm -rf "$w"; return 1
fi
now=$(date +%s)
if [ -f "$w/temporales.db" ]; then
while IFS=: read -r u ep; do [ -n "$u" ] && TEMP[$u]=$ep; done < "$w/temporales.db"
fi
while IFS=: read -r u hash gecos; do
[ -z "$u" ] && continue
lim=$(awk -F: -v u="$u" '$1==u{print $2; exit}' "$w/usuarios.db"); lim=${lim:-1}
exp=$(awk -F: -v u="$u" '$1==u{print $3; exit}' "$w/usuarios.db")
[ -n "$exp" ] || continue
if [ -n "${TEMP[$u]:-}" ] && [ "${TEMP[$u]}" -le "$now" ]; then vencidos=$((vencidos+1)); continue; fi
if id "$u" >/dev/null 2>&1; then
existentes=$((existentes+1))
else
args=(-M -s /bin/false -e "$(fecha_cuenta "$exp")")
[ -n "$gecos" ] && args+=(-c "$gecos")
case "$gecos" in hwid,*) args+=(--badname) ;; esac
if useradd "${args[@]}" "$u" 2>/dev/null; then
echo "$u:$hash" | chpasswd -e 2>/dev/null
nuevos=$((nuevos+1))
else
continue
fi
fi
zumo_db_del "$u"; zumo_db_add "$u" "$lim" "$exp"
us+="$u "
if [ -n "${TEMP[$u]:-}" ]; then
min=$(( (TEMP[$u] - now + 59) / 60 ))
[ "$min" -ge 1 ] && programar_borrado_temp "$u" "$min"
fi
done < "$w/cuentas.txt"
_merge_por_usuario "${ZUMO_DATOS:-/etc/zumo/datos.db}" "$w/datos.db" 1 "$us"
[ -s "$w/claves.db" ] && { ( umask 077; _merge_por_usuario "$CLAVES" "$w/claves.db" 1 "$us" ); chmod 600 "$CLAVES" 2>/dev/null; }
_merge_por_usuario "${ZUMO_HIST:-/etc/zumo/datos-hist.db}" "$w/datos-hist.db" 2 "$us"
[ -f "$w/limit.conf" ] && [ -w "$(dirname "${ZUMO_LIMCONF:-/etc/zumo/limit.conf}")" ] && cp "$w/limit.conf" "${ZUMO_LIMCONF:-/etc/zumo/limit.conf}"
rm -rf "$w"
RS_NUEVOS=$nuevos; RS_EXISTENTES=$existentes; RS_VENCIDOS=$vencidos
return 0
}

# Servidor de un solo uso: entrega el respaldo una vez en /respaldo y se apaga
# (o a los 10 minutos). El archivo va cifrado, así que no se ve nada en el cable.
_respaldo_servidor_py() {
cat <<'PY'
import http.server, sys, threading
arch, puerto = sys.argv[1], int(sys.argv[2])
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.split("?")[0] != "/respaldo":
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        d = open(arch, "rb").read()
        self.send_response(200); self.send_header("Content-Length", str(len(d))); self.end_headers()
        self.wfile.write(d)
        print("DESCARGADO", flush=True)
        threading.Thread(target=srv.shutdown).start()
srv = http.server.HTTPServer(("0.0.0.0", puerto), H)
t = threading.Timer(600, srv.shutdown); t.daemon = True; t.start()
srv.serve_forever()
PY
}

_respaldos_lista() {
RESPALDOS=()
local f
for f in $(ls -1t "$RESP_DIR"/*.zbk 2>/dev/null); do RESPALDOS+=("$f"); done
}

_respaldo_elegir() { # sale con 1 si no hay o se cancela; deja el archivo en BK_ELEGIDO
local i n sz
_respaldos_lista
if [ ${#RESPALDOS[@]} -eq 0 ]; then msg_err "No hay respaldos guardados. Creá uno primero."; return 1; fi
for i in "${!RESPALDOS[@]}"; do
sz=$(du -h "${RESPALDOS[$i]}" | cut -f1)
echo -e " \e[1;38;5;208m[$((i+1))]\e[0m \e[1;32m$(basename "${RESPALDOS[$i]}")\e[0m \e[2m($sz)${N}"
done
echo -e " \e[1;38;5;208m[0]\e[0m \e[1;32mVolver${N}\n"
read -rp " Número [1 = el último]: " n; n=${n:-1}
[ "$n" = "0" ] && return 1
if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt ${#RESPALDOS[@]} ]; then msg_err "Opción inválida"; return 1; fi
BK_ELEGIDO="${RESPALDOS[$((n-1))]}"
}

crear_respaldo() {
banner; echo -e " \e[1;38;5;141mCREAR RESPALDO${N}\n"
local clave arch
if ! command -v openssl >/dev/null 2>&1; then msg_err "Falta openssl (apt install openssl)"; pausa; return; fi
read -rp " Clave del respaldo, 4 a 20 letras/números (Enter = generar): " clave
[ -z "$clave" ] && clave=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 8)
[[ "$clave" =~ ^[A-Za-z0-9]{4,20}$ ]] || { msg_err "Clave inválida"; pausa; return; }
mkdir -p "$RESP_DIR"; chmod 700 "$RESP_DIR"
arch="$RESP_DIR/zumo-$(date +%Y%m%d-%H%M%S).zbk"
if _respaldo_crear "$arch" "$clave"; then
ls -1t "$RESP_DIR"/*.zbk 2>/dev/null | tail -n +11 | xargs -r rm -f
echo; msg_ok "Respaldo creado ($BK_CUENTAS usuarios)"
echo -e "   Archivo: \e[1;38;5;214m$arch${N}"
echo -e "   Clave:   \e[1;38;5;214m$clave${N}"
echo -e "\n \e[1;31mAnotá la clave: sin ella no se puede restaurar.${N}"
else
msg_err "No se pudo crear el respaldo"
fi
pausa
}

compartir_respaldo() {
banner; echo -e " \e[1;38;5;141mCOMPARTIR RESPALDO (IP y puerto)${N}\n"
local puerto pid ip ufw_regla=0 salida rc
command -v python3 >/dev/null 2>&1 || { msg_err "Falta python3 (apt install python3)"; pausa; return; }
_respaldo_elegir || { pausa; return; }
read -rp " Puerto [8088]: " puerto; puerto=${puerto:-8088}
[[ "$puerto" =~ ^[0-9]+$ ]] && [ "$puerto" -ge 1 ] && [ "$puerto" -le 65535 ] || { msg_err "Puerto inválido"; pausa; return; }
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
ufw allow "$puerto/tcp" >/dev/null 2>&1 && ufw_regla=1
fi
salida=$(mktemp)
_respaldo_servidor_py > "$salida.py"
python3 "$salida.py" "$BK_ELEGIDO" "$puerto" > "$salida" 2>&1 &
pid=$!
sleep 1
if ! kill -0 "$pid" 2>/dev/null; then msg_err "No se pudo abrir el puerto $puerto (¿está en uso?)"; rm -f "$salida" "$salida.py"; [ $ufw_regla = 1 ] && ufw delete allow "$puerto/tcp" >/dev/null 2>&1; pausa; return; fi
ip=$(ip_publica)
echo
msg_ok "Listo para descargar (una sola vez, 10 minutos)"
echo -e "\n   IP:     \e[1;38;5;214m$ip${N}"
echo -e "   Puerto: \e[1;38;5;214m$puerto${N}"
echo -e "\n \e[2mEn la VPS nueva: Herramientas → Respaldo → Restaurar desde otra VPS.${N}"
echo -e " \e[2mNecesitás también la clave del respaldo. Si no conecta, abrí ese puerto en el firewall del proveedor.${N}"
echo -e "\n Esperando la descarga... (Enter para cancelar)"
while kill -0 "$pid" 2>/dev/null; do
read -rsn1 -t 1 && break
done
if grep -q DESCARGADO "$salida" 2>/dev/null; then echo; msg_ok "Respaldo descargado. Servidor cerrado."
else echo; echo -e " \e[2mServidor cerrado.${N}"; fi
kill "$pid" 2>/dev/null
rm -f "$salida" "$salida.py"
[ $ufw_regla = 1 ] && ufw delete allow "$puerto/tcp" >/dev/null 2>&1
pausa
}

_respaldo_restaurar_pantalla() { # $1 = archivo
local clave
read -rsp " Clave del respaldo: " clave; echo
if _respaldo_restaurar "$1" "$clave"; then
echo; msg_ok "Restauración lista"
echo -e "   Usuarios creados:         \e[1;38;5;214m$RS_NUEVOS${N}"
echo -e "   Ya existían (se dejaron): \e[1;38;5;214m$RS_EXISTENTES${N}"
[ "$RS_VENCIDOS" -gt 0 ] && echo -e "   Temporales vencidos:      \e[1;38;5;214m$RS_VENCIDOS (no se crearon)${N}"
echo -e "\n \e[2mLos protocolos (PDirect, BadVPN, etc.) se activan desde el menú Protocolos.${N}"
else
msg_err "Clave incorrecta o archivo dañado"
fi
}

restaurar_desde_ip() {
banner; echo -e " \e[1;38;5;141mRESTAURAR DESDE OTRA VPS${N}\n"
local ip puerto arch
read -rp " IP de la VPS vieja (Enter = volver): " ip
[ -z "$ip" ] && return
[[ "$ip" =~ ^[A-Za-z0-9.:-]+$ ]] || { msg_err "IP inválida"; pausa; return; }
read -rp " Puerto [8088]: " puerto; puerto=${puerto:-8088}
[[ "$puerto" =~ ^[0-9]+$ ]] || { msg_err "Puerto inválido"; pausa; return; }
mkdir -p "$RESP_DIR"; chmod 700 "$RESP_DIR"
arch="$RESP_DIR/recibido-$(date +%Y%m%d-%H%M%S).zbk"
echo -e " \e[2mDescargando...${N}"
if ! curl -fsS --max-time 120 -o "$arch" "http://$ip:$puerto/respaldo" 2>/dev/null || [ ! -s "$arch" ]; then
rm -f "$arch"; msg_err "No se pudo descargar. Revisá IP, puerto y que la VPS vieja siga esperando."; pausa; return
fi
msg_ok "Descargado"
_respaldo_restaurar_pantalla "$arch"
pausa
}

restaurar_guardado() {
banner; echo -e " \e[1;38;5;141mRESTAURAR UN RESPALDO GUARDADO${N}\n"
_respaldo_elegir || { pausa; return; }
echo
_respaldo_restaurar_pantalla "$BK_ELEGIDO"
pausa
}

menu_respaldo() {
while true; do
banner; echo -e " \e[1;38;5;141mRESPALDO Y RESTAURACIÓN${N}\n"
op 1 "💾" "Crear respaldo"
op 2 "📤" "Compartir respaldo (IP y puerto)"
op 3 "📥" "Restaurar desde otra VPS (IP y puerto)"
op 4 "♻" "Restaurar un respaldo guardado"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) crear_respaldo ;;
2) compartir_respaldo ;;
3) restaurar_desde_ip ;;
4) restaurar_guardado ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

# Usuario compartido: quiénes intentaron conectar más sesiones de las permitidas
# (el limitador anota cada intento en excesos.log).
usuario_compartido() {
local f="${ZUMO_EXCESOS:-/etc/zumo/excesos.log}" k ep nom n=0 cnt last ips u lim sep resp
sep=$(printf '─%.0s' $(seq 1 40))
while true; do
banner; echo -e " \e[1;38;5;141mUSUARIO COMPARTIDO${N}"
echo -e " \e[2mQuienes intentaron conectar más de su límite${N}\n"
n=0
local datos
datos=$(awk -F'|' 'NR==FNR{split($0,d,":"); lim[d[1]]=d[2]; next}
($2 in lim){c[$2]++; if($1>l[$2])l[$2]=$1; if(!(($2,$3) in seen)){seen[$2,$3]=1; ip[$2]++}}
END{for(u in c) print c[u] "\t" l[u] "\t" ip[u] "\t" u "\t" lim[u]}' "$DB" <(cat "$f.1" "$f" 2>/dev/null) | sort -t$'\t' -k1,1nr -k2,2nr)
if [ -z "$datos" ]; then
echo -e " \e[1;32m✔ Todavía no hubo intentos por encima del límite.${N}"
else
while IFS=$'\t' read -r cnt last ips u lim; do
[ -z "$u" ] && continue
n=$((n+1))
nom="$(etiqueta_de "$u")"
echo -e " \e[38;5;60m${sep}${N}"
echo -e " \e[1;38;5;208m[$n]${N} \e[1;97m$nom${N}  \e[2m·${N} \e[1;31m${cnt} intento$([ "$cnt" -ne 1 ] && echo s)${N}"
echo -e "      \e[2mÚltima:${N} \e[1;97m$(date -d "@$last" +'%d/%m %H:%M')${N}  \e[2mIPs:${N} \e[1;97m${ips}${N}  \e[2mLímite:${N} \e[1;97m${lim}${N}"
done <<< "$datos"
fi
echo -e " $L"
echo -e "\n \e[1;38;5;208m[V]${N} Vaciar historial     \e[2mEnter para volver...${N}"
read -rsn1 k
if [[ "$k" =~ ^[vV]$ ]]; then
read -rp " ¿Vaciar el historial? (s/N): " resp
if [[ "$resp" =~ ^[sS]$ ]]; then rm -f "$f" "$f.1"; msg_ok "Historial vaciado"; sleep 1; fi
continue
fi
return
done
}

# ── Control de red ─────────────────────────────────────────────────────────
# Interfaz de salida a internet (la de la ruta por defecto).
_red_iface() {
local i
i=$(ip -o route get 1.1.1.1 2>/dev/null | awk '{for(k=1;k<=NF;k++) if($k=="dev"){print $(k+1); exit}}')
[ -z "$i" ] && i=$(awk -F'[: ]+' 'NR>2 && $2!="lo"{print $2; exit}' "${ZUMO_NETDEV:-/proc/net/dev}")
echo "$i"
}
# Bytes recibidos y enviados desde el arranque: "rx tx".
_red_bytes() {
awk -v i="$1" '{sub(/^ +/,"")} index($0,i":")==1{sub(/^[^:]*: */,""); print $1, $9; exit}' "${ZUMO_NETDEV:-/proc/net/dev}"
}
# Velocidad en Mbps entre dos lecturas: "bajada subida".
_red_calc() {
LC_ALL=C awk -v r0="$1" -v t0="$2" -v r1="$3" -v t1="$4" -v dt="$5" \
'BEGIN{if(dt<=0){print "0.0 0.0"; exit} d=(r1-r0)*8/dt/1000000; u=(t1-t0)*8/dt/1000000; if(d<0)d=0; if(u<0)u=0; printf "%.1f %.1f", d, u}'
}
_red_fmt_bytes() {
LC_ALL=C awk -v b="${1:-0}" 'BEGIN{ if(b>=1073741824) printf "%.2f GB", b/1073741824;
else if(b>=1048576) printf "%.1f MB", b/1048576; else printf "%.0f KB", b/1024 }'
}
# Capacidad del enlace en Mbps (vacío si la VPS no la informa).
_red_capacidad() {
local c="${ZUMO_LINK_MBPS:-}"
[ -z "$c" ] && c=$(cat "/sys/class/net/$1/speed" 2>/dev/null)
[[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -gt 0 ] && echo "$c"
}

control_red() {
local ifc rx0 tx0 rx1 tx1 t0 t1 dt dl ul cap pico_d=0 pico_u=0 pd pu conex online k rc buf
local rx_ini tx_ini
ifc=$(_red_iface)
[ -z "$ifc" ] && { banner; msg_err "No se encontró la interfaz de red"; pausa; return; }
read -r rx_ini tx_ini <<< "$(_red_bytes "$ifc")"
cap=$(_red_capacidad "$ifc")
read -r rx0 tx0 <<< "$(_red_bytes "$ifc")"; t0=$(date +%s.%N)
while true; do
read -rsn1 -t 1 k; rc=$?
if [ "$rc" -eq 0 ]; then
[[ "$k" =~ ^[rR]$ ]] && { pico_d=0; pico_u=0; continue; }
return
fi
[ "$rc" -gt 128 ] || return   # sin teclado (EOF): salir
read -r rx1 tx1 <<< "$(_red_bytes "$ifc")"; t1=$(date +%s.%N)
dt=$(LC_ALL=C awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
read -r dl ul <<< "$(_red_calc "$rx0" "$tx0" "$rx1" "$tx1" "$dt")"
rx0=$rx1; tx0=$tx1; t0=$t1
pico_d=$(LC_ALL=C awk -v a="$pico_d" -v b="$dl" 'BEGIN{print (b>a)?b:a}')
pico_u=$(LC_ALL=C awk -v a="$pico_u" -v b="$ul" 'BEGIN{print (b>a)?b:a}')
conex=$(ss -Htn state established '( sport = :22 )' 2>/dev/null | wc -l)
online=$(ps -eo user:32,comm 2>/dev/null | awk '$2=="sshd" && $1!="root" && $1!="sshd"{print $1}' | sort -u | wc -l)
buf=$(
echo -e " \e[1;38;5;141mCONTROL DE RED${N}  \e[2m(${ifc})${N}\n"
echo -e " \e[1;38;5;208m↓ Bajada:${N} \e[1;32m${dl} Mbps${N}   \e[2mpico ${pico_d}${N}"
echo -e " \e[1;38;5;208m↑ Subida:${N} \e[1;32m${ul} Mbps${N}   \e[2mpico ${pico_u}${N}"
if [ -n "$cap" ]; then
pd=$(LC_ALL=C awk -v v="$dl" -v c="$cap" 'BEGIN{printf "%.1f", v*100/c}')
pu=$(LC_ALL=C awk -v v="$ul" -v c="$cap" 'BEGIN{printf "%.1f", v*100/c}')
echo -e "\n \e[1;38;5;208mUso del enlace (${cap} Mbps):${N}"
echo -e "   Bajada: $(_col_pct "$pd")${pd}%\e[0m   Subida: $(_col_pct "$pu")${pu}%\e[0m"
else
echo -e "\n \e[2mLa VPS no informa la capacidad del enlace.\n Para ver el % de uso: ZUMO_LINK_MBPS=1000 zumo${N}"
fi
echo -e "\n \e[1;38;5;208mDesde que abriste esto:${N}"
echo -e "   ↓ $(_red_fmt_bytes $((rx1-rx_ini)))   ↑ $(_red_fmt_bytes $((tx1-tx_ini)))"
echo -e " \e[1;38;5;208mDesde que arrancó la VPS:${N}"
echo -e "   ↓ $(_red_fmt_bytes "$rx1")   ↑ $(_red_fmt_bytes "$tx1")"
echo -e "\n \e[1;38;5;208mConexiones SSH:${N} \e[1;97m${conex}${N}   \e[1;38;5;208mUsuarios en línea:${N} \e[1;32m${online}${N}"
)
banner
printf '%s\n' "$buf"
echo -e "\n $L\n \e[1;38;5;208m[R]${N} Reiniciar picos     \e[2mCualquier otra tecla para volver...${N}"
done
}

# ───────────────────────────── fail2ban (anti-bots SSH) ─────────────────────
# Banea las IP que fallan la contraseña varias veces. Los clientes que entran por
# el payload llegan al sshd como 127.0.0.1 (ignoreip), así que nunca se bloquean.
F2B_JAIL="${ZUMO_F2B_JAIL:-/etc/fail2ban/jail.local}"

fail2ban_instalado() { command -v fail2ban-client >/dev/null 2>&1; }
fail2ban_activo() { systemctl is-active --quiet fail2ban 2>/dev/null; }

# Escribe el jail con el tiempo de baneo que se le pase (por defecto 1h).
f2b_escribir_jail() {
cat > "$F2B_JAIL" <<EOF
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = ${1:-1h}
ignoreip = 127.0.0.1/8 ::1
EOF
}

# Lee el tiempo de baneo del jail (o 1h si no está).
f2b_bantime() {
local v
v=$(grep -m1 '^bantime' "$F2B_JAIL" 2>/dev/null | cut -d= -f2 | tr -d ' ')
echo "${v:-1h}"
}

# Lista las IP baneadas ahora (separadas por espacios).
f2b_baneadas() {
fail2ban-client status sshd 2>/dev/null | sed -n 's/.*Banned IP list:[[:space:]]*//p'
}

f2b_activar() {
if ! fail2ban_instalado; then
echo " Instalando fail2ban..."
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null 2>&1
apt-get install -y --no-install-recommends fail2ban >/dev/null 2>&1 || { msg_err "No se pudo instalar fail2ban"; return 1; }
fi
[ -f "$F2B_JAIL" ] || f2b_escribir_jail "1h"
systemctl enable fail2ban >/dev/null 2>&1
systemctl restart fail2ban 2>/dev/null
sleep 1
if fail2ban_activo; then msg_ok "fail2ban activado (baneo: $(f2b_bantime))"; else msg_err "No quedó activo (journalctl -u fail2ban)"; fi
}

f2b_desactivar() {
systemctl stop fail2ban 2>/dev/null
systemctl disable fail2ban >/dev/null 2>&1
msg_ok "fail2ban desactivado (ya no se banea a nadie)"
}

f2b_cambiar_tiempo() {
echo -e " Tiempo de baneo actual: \e[1;97m$(f2b_bantime)${N}\n"
echo -e " \e[2mEjemplos: 30m (minutos) · 1h · 12h · 1d (día) · 1w (semana) · -1 (para siempre)${N}"
read -rp " Nuevo tiempo de baneo: " bt
[[ "$bt" =~ ^(-1|[0-9]+[smhdw]?)$ ]] || { msg_err "Formato inválido"; sleep 1; return; }
f2b_escribir_jail "$bt"
if fail2ban_activo; then systemctl restart fail2ban 2>/dev/null; fi
msg_ok "Tiempo de baneo: $bt"; sleep 1
}

# De qué país/empresa es una IP (ip-api.com, gratis, sin clave). "" si no se pudo.
f2b_empresa() {
curl -s --max-time 4 "http://ip-api.com/line/$1?fields=country,isp" 2>/dev/null
}

f2b_lista() {
banner; echo -e " \e[1;38;5;141mIP BANEADAS${N}\n"
if ! fail2ban_instalado || ! fail2ban_activo; then msg_err "fail2ban no está activo"; pausa; return; fi
local ips; ips=$(f2b_baneadas)
local total; total=$(echo $ips | wc -w)
if [ "$total" -eq 0 ]; then msg_ok "No hay ninguna IP baneada en este momento"; pausa; return; fi
echo -e " \e[1;38;5;208mTotal baneadas:${N} \e[1;97m$total${N}   \e[2m(el dato de empresa sale de internet)${N}\n"
local i=0 ip info country isp
for ip in $ips; do
i=$((i+1))
info=$(f2b_empresa "$ip")
country=$(echo "$info" | sed -n 1p)
isp=$(echo "$info" | sed -n 2p)
if [ -n "$isp" ]; then
printf " \e[1;38;5;208m[%2d]\e[0m \e[1;97m%-16s\e[0m \e[1;32m%s\e[0m \e[2m(%s)${N}\n" "$i" "$ip" "$isp" "$country"
else
printf " \e[1;38;5;208m[%2d]\e[0m \e[1;97m%-16s\e[0m \e[2m(sin datos de empresa)${N}\n" "$i" "$ip"
fi
done
pausa
}

f2b_desbanear() {
if ! fail2ban_instalado || ! fail2ban_activo; then msg_err "fail2ban no está activo"; sleep 1; return; fi
read -rp " IP a desbanear: " ip
[[ "$ip" =~ ^[0-9a-fA-F.:]+$ ]] || { msg_err "IP inválida"; sleep 1; return; }
if fail2ban-client set sshd unbanip "$ip" >/dev/null 2>&1; then msg_ok "$ip desbaneada"; else msg_err "No se pudo (¿estaba baneada?)"; fi
sleep 1
}

menu_fail2ban() {
while true; do
banner; echo -e " \e[1;38;5;141mFAIL2BAN · anti-bots SSH${N}\n"
echo -e " \e[2mBloquea la IP que falla la contraseña 5 veces en 10 min.\n Tus clientes por payload (127.0.0.1) nunca se bloquean.${N}\n"
if fail2ban_instalado && fail2ban_activo; then
local nban; nban=$(echo "$(f2b_baneadas)" | wc -w)
echo -e " \e[1;32m● Estado: activo${N}   \e[1;38;5;208mBaneo:${N} \e[1;97m$(f2b_bantime)${N}   \e[1;38;5;208mBaneadas ahora:${N} \e[1;97m${nban}${N}\n"
elif fail2ban_instalado; then
echo -e " \e[1;31m● Estado: instalado pero apagado${N}\n"
else
echo -e " \e[1;31m● Estado: no instalado${N}\n"
fi
op 1 "⚡" "Activar"
op 2 "✖" "Desactivar"
op 3 "⏱" "Tiempo de baneo"
op 4 "▤" "Ver IP baneadas (con empresa)"
op 5 "🔓" "Desbanear una IP"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) f2b_activar; pausa ;;
2) f2b_desactivar; sleep 1 ;;
3) f2b_cambiar_tiempo ;;
4) f2b_lista ;;
5) f2b_desbanear ;;
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
op 2 "🚀" "Test de velocidad"
op 3 "📊" "Uso de CPU y RAM"
op 4 "👥" "Usuario compartido"
op 5 "📡" "Control de red"
op 6 "🛡" "Fail2ban (anti-bots SSH)"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) menu_bbr ;;
2) test_velocidad ;;
3) procesos_top ;;
4) usuario_compartido ;;
5) control_red ;;
6) menu_fail2ban ;;
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
