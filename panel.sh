#!/bin/bash
# DB: usuario:limite:vencimiento
DB=/etc/zumo/usuarios.db
N='\e[0m'
L='\e[38;5;240m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\e[0m'

stats() {
read -r _ mt mu _ <<< "$(free -m | awk '/^Mem:/{print $1,$2,$3}')"
local mfreep=$(( (mt-mu)*100/mt ))
local cores=$(nproc 2>/dev/null || echo 1)
local la=$(awk '{print $1}' /proc/loadavg)
local cpu=$(awk -v l="$la" -v c="$cores" 'BEGIN{p=l/c*100; if(p>100)p=100; printf "%d", p}')
local cpufree=$(( 100-cpu ))
local creadas=$(grep -c ':' "$DB" 2>/dev/null)
local online=$(ps -eo user:32,comm 2>/dev/null | awk '$2=="sshd" && $1!="root" && $1!="sshd"{print $1}' | sort -u | wc -l)
echo -e " \e[1;38;5;117mRAM:\e[0m ${mu}/${mt}MB (\e[1;32m${mfreep}% libre\e[0m)  \e[1;38;5;117mCPU:\e[0m ${cpu}% (\e[1;32m${cpufree}% libre\e[0m)"
echo -e " \e[1;38;5;117mCuentas:\e[0m ${creadas}  \e[1;38;5;117mEn línea:\e[0m \e[1;33m${online}\e[0m"
}

banner() {
clear
echo
echo -e "        \e[1;38;5;87m═══  P A N E L   Z U M O  ═══${N}"
echo -e " $L"
stats
echo -e " $L"
}

pausa() { echo; read -rp " Enter para volver..." _; }
op() { echo -e " \e[1;38;5;201m[$1]\e[0m \e[1;38;5;87m$2${N}"; }
msg_ok() { echo -e " \e[1;32m✔ $1${N}"; }
msg_err() { echo -e " \e[1;31m✘ $1${N}"; }

dias() {
local d=$(( ( $(date -d "$1" +%s) - $(date -d "$(date +%F)" +%s) ) / 86400 ))
if [ "$d" -lt 0 ]; then echo "vencido"; elif [ "$d" -eq 1 ]; then echo "vence 1 día"; else echo "vence $d días"; fi
}

elegir_usuario() {
mapfile -t USERS < <(cut -d: -f1 "$DB" | sed '/^$/d')
if [ ${#USERS[@]} -eq 0 ]; then msg_err "No hay usuarios registrados"; return 1; fi
for i in "${!USERS[@]}"; do echo -e " \e[1;38;5;201m[$((i+1))]\e[0m ${USERS[$i]}"; done
echo; read -rp " Número de usuario: " n
if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt ${#USERS[@]} ]; then msg_err "Opción inválida"; return 1; fi
SEL="${USERS[$((n-1))]}"
}

crear_usuario() {
banner; echo -e " \e[1;38;5;87mCREAR USUARIO${N}\n"
read -rp " Usuario: " u
[[ "$u" =~ ^[a-z_][a-z0-9_-]*$ ]] || { msg_err "Nombre inválido"; pausa; return; }
id "$u" &>/dev/null && { msg_err "El usuario ya existe"; pausa; return; }
read -rp " Contraseña: " p
[ -z "$p" ] && { msg_err "Contraseña vacía"; pausa; return; }
read -rp " Días de duración: " d
[[ "$d" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; pausa; return; }
read -rp " Límite de conexiones [1]: " lim; lim=${lim:-1}
[[ "$lim" =~ ^[0-9]+$ ]] || { msg_err "Límite inválido"; pausa; return; }
exp=$(date -d "+$d days" +%F)
useradd -M -s /bin/false -e "$exp" "$u" && echo "$u:$p" | chpasswd
echo "$u:$lim:$exp" >> "$DB"
echo; echo -e " $L"
msg_ok "Usuario creado"
echo -e "   Usuario:    \e[1;38;5;87m$u${N}"
echo -e "   Contraseña: \e[1;38;5;87m$p${N}"
echo -e "   Duración:   \e[1;38;5;87m$(dias "$exp")${N}"
echo -e "   Límite:     \e[1;38;5;87m$lim conexión(es)${N}"
echo -e " $L"; pausa
}

eliminar_usuario() {
banner; echo -e " \e[1;38;5;87mELIMINAR USUARIO${N}\n"
elegir_usuario || { pausa; return; }
pkill -9 -u "$SEL" 2>/dev/null
userdel "$SEL" 2>/dev/null
sed -i "/^$SEL:/d" "$DB"
msg_ok "Usuario $SEL eliminado"; pausa
}

vencidos() {
banner; echo -e " \e[1;38;5;87mUSUARIOS VENCIDOS${N}\n"
if [ ! -s "$DB" ]; then msg_err "No hay usuarios"; pausa; return; fi
hoy=$(date -d "$(date +%F)" +%s)
VENC=()
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
e=$(date -d "$exp" +%s 2>/dev/null) || continue
[ "$e" -lt "$hoy" ] && VENC+=("$u|$exp")
done < "$DB"
if [ ${#VENC[@]} -eq 0 ]; then msg_ok "No hay usuarios vencidos"; pausa; return; fi
printf " \e[1;38;5;213m%-16s %s${N}\n" "USUARIO" "VENCIÓ"
for item in "${VENC[@]}"; do
printf " \e[1;31m%-16s %s${N}\n" "${item%%|*}" "${item#*|}"
done
echo; echo -e " $L"
op 1 "Borrar usuarios vencidos"
op 0 "Volver"
echo; read -rp " Opción: " o
case $o in
1) read -rp " ¿Borrar estos ${#VENC[@]} usuario(s)? [s/N]: " c
[[ "$c" =~ ^[sS]$ ]] || { msg_err "Cancelado"; pausa; return; }
for item in "${VENC[@]}"; do
u="${item%%|*}"
pkill -9 -u "$u" 2>/dev/null
userdel "$u" 2>/dev/null
sed -i "/^$u:/d" "$DB"
done
msg_ok "${#VENC[@]} usuario(s) vencido(s) eliminado(s)"; pausa ;;
*) return ;;
esac
}

cambiar_limite() {
banner; echo -e " \e[1;38;5;87mCAMBIAR LÍMITE DE CONEXIONES${N}\n"
elegir_usuario || { pausa; return; }
read -rp " Nuevo límite para $SEL: " lim
[[ "$lim" =~ ^[0-9]+$ ]] && [ "$lim" -ge 1 ] || { msg_err "Límite inválido"; pausa; return; }
awk -F: -v u="$SEL" -v l="$lim" 'BEGIN{OFS=":"} $1==u{$2=l} {print}' "$DB" > "$DB.tmp" && mv "$DB.tmp" "$DB"
msg_ok "Límite de $SEL ahora es $lim"; pausa
}

listar_usuarios() {
banner; echo -e " \e[1;38;5;87mUSUARIOS REGISTRADOS${N}\n"
if [ ! -s "$DB" ]; then msg_err "No hay usuarios"; pausa; return; fi
printf " \e[1;38;5;213m%-14s %-8s %-10s %s${N}\n" "USUARIO" "LÍMITE" "ONLINE" "VENCIMIENTO"
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
on=$(ps -u "$u" -o comm= 2>/dev/null | grep -c '^sshd$')
printf " %-14s %-8s %-10s %s\n" "$u" "$lim" "$on" "$(dias "$exp")"
done < "$DB"
pausa
}

menu_usuario() {
while true; do
banner; echo -e " \e[1;38;5;87mUSUARIO${N}\n"
op 1 "Crear usuario"
op 2 "Eliminar usuario"
op 3 "Cambiar límite de conexiones"
op 4 "Ver usuarios"
op 5 "Usuarios vencidos"
op 0 "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) crear_usuario ;;
2) eliminar_usuario ;;
3) cambiar_limite ;;
4) listar_usuarios ;;
5) vencidos ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

menu_protocolos() {
while true; do
banner
if systemctl is-active --quiet pdirect-80; then
echo -e " \e[1;32m● PDirect WebSocket: activo (80 → SSH)${N}"
else
echo -e " \e[1;31m● PDirect WebSocket: inactivo${N}"
fi
if systemctl is-active --quiet udpgw-7300; then
echo -e " \e[1;32m● BadVPN UDPGW: activo (7300)${N}"
else
echo -e " \e[1;31m● BadVPN UDPGW: inactivo${N}"
fi
if systemctl is-active --quiet hcr-server; then
echo -e " \e[1;32m● HCR Server: activo (8880 → SSH)${N}"
else
echo -e " \e[1;31m● HCR Server: inactivo${N}"
fi
if systemctl is-active --quiet bhttp-server; then
echo -e " \e[1;32m● BHTTP: activo (8080 → SSH)${N}\n"
else
echo -e " \e[1;31m● BHTTP: inactivo${N}\n"
fi
op 1 "Activar WebSocket (80 + 7300)"
op 2 "Desactivar WebSocket (libera 80 y 7300)"
op 3 "Activar HCR Server (8880)"
op 4 "Desactivar HCR Server (libera 8880)"
op 5 "Activar BHTTP (8080)"
op 6 "Desactivar BHTTP (libera 8080)"
op 0 "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) echo -e " \e[1;38;5;87mCompilando e instalando, aguardá...${N}"
if bash /etc/zumo/activar-protocolos.sh; then
msg_ok "WebSocket (80 → SSH) y BadVPN (7300) activos"
else
msg_err "Falló; revisá 'journalctl -u pdirect-80 -u udpgw-7300'"
fi; pausa ;;
2) echo -e " \e[1;38;5;87mLiberando puertos 80 y 7300...${N}"
bash /etc/zumo/desactivar-protocolos.sh
msg_ok "WebSocket y BadVPN desactivados; puertos 80 y 7300 liberados"; pausa ;;
3) echo -e " \e[1;38;5;87mInstalando HCR Server, aguardá...${N}"
if bash /etc/zumo/activar-hcr.sh; then
msg_ok "HCR Server activo en el puerto 8880 (→ SSH 22)"
else
msg_err "Falló; revisá 'journalctl -u hcr-server'"
fi; pausa ;;
4) echo -e " \e[1;38;5;87mLiberando puerto 8880...${N}"
bash /etc/zumo/desactivar-hcr.sh
msg_ok "HCR Server desactivado; puerto 8880 liberado"; pausa ;;
5) echo -e " \e[1;38;5;87mInstalando BHTTP, aguardá...${N}"
if bash /etc/zumo/activar-bhttp.sh; then
msg_ok "BHTTP activo en el puerto 8080 (→ SSH 22)"
else
msg_err "Falló; revisá 'journalctl -u bhttp-server'"
fi; pausa ;;
6) echo -e " \e[1;38;5;87mLiberando puerto 8080...${N}"
bash /etc/zumo/desactivar-bhttp.sh
msg_ok "BHTTP desactivado; puerto 8080 liberado"; pausa ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

while true; do
banner
op 1 "Usuario"
op 2 "Protocolos"
op 0 "Salir"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) menu_usuario ;;
2) menu_protocolos ;;
0) clear; exit 0 ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
