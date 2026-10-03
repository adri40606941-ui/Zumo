#!/bin/bash
# DB: usuario:limite:vencimiento
DB=/etc/zumo/usuarios.db
N='\e[0m'
L='\e[38;5;97m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\e[0m'

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

elegir_usuario() {
mapfile -t USERS < <(cut -d: -f1 "$DB" | sed '/^$/d')
if [ ${#USERS[@]} -eq 0 ]; then msg_err "No hay usuarios registrados"; return 1; fi
for i in "${!USERS[@]}"; do echo -e " \e[1;38;5;208m[$((i+1))]\e[0m \e[1;32m${USERS[$i]}\e[0m"; done
echo; read -rp " Número de usuario: " n
if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt ${#USERS[@]} ]; then msg_err "Opción inválida"; return 1; fi
SEL="${USERS[$((n-1))]}"
}

crear_usuario() {
banner; echo -e " \e[1;38;5;141mCREAR USUARIO${N}\n"
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
echo -e "   Usuario:    \e[1;38;5;214m$u${N}"
echo -e "   Contraseña: \e[1;38;5;214m$p${N}"
echo -e "   Duración:   \e[1;38;5;214m$(dias "$exp")${N}"
echo -e "   Límite:     \e[1;38;5;214m$lim conexión(es)${N}"
echo -e " $L"; pausa
}

eliminar_usuario() {
banner; echo -e " \e[1;38;5;141mELIMINAR USUARIO${N}\n"
elegir_usuario || { pausa; return; }
pkill -9 -u "$SEL" 2>/dev/null
userdel "$SEL" 2>/dev/null
sed -i "/^$SEL:/d" "$DB"
msg_ok "Usuario $SEL eliminado"; pausa
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
printf " \e[1;38;5;208m%-16s %s${N}\n" "USUARIO" "VENCIÓ"
for item in "${VENC[@]}"; do
printf " \e[1;31m%-16s %s${N}\n" "${item%%|*}" "${item#*|}"
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
sed -i "/^$u:/d" "$DB"
done
msg_ok "${#VENC[@]} usuario(s) vencido(s) eliminado(s)"; pausa ;;
*) return ;;
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
op 1 "✎" "Cambiar contraseña"
op 2 "⚙" "Cambiar límite de conexiones"
op 3 "⏱" "Cambiar días (vencimiento)"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " eo
case $eo in
1) read -rp " Contraseña nueva para $SEL: " np
[ -z "$np" ] && { msg_err "Contraseña vacía"; sleep 1; continue; }
echo "$SEL:$np" | chpasswd
msg_ok "Contraseña de $SEL actualizada"; sleep 1 ;;
2) read -rp " Nuevo límite para $SEL [$lim]: " nl; nl=${nl:-$lim}
[[ "$nl" =~ ^[0-9]+$ ]] && [ "$nl" -ge 1 ] || { msg_err "Límite inválido"; sleep 1; continue; }
awk -F: -v u="$SEL" -v l="$nl" 'BEGIN{OFS=":"} $1==u{$2=l} {print}' "$DB" > "$DB.tmp" && mv "$DB.tmp" "$DB"
msg_ok "Límite de $SEL ahora es $nl"; sleep 1 ;;
3) read -rp " Días desde hoy: " nd
[[ "$nd" =~ ^[0-9]+$ ]] || { msg_err "Días inválidos"; sleep 1; continue; }
nexp=$(date -d "+$nd days" +%F)
usermod -e "$nexp" "$SEL" 2>/dev/null
awk -F: -v u="$SEL" -v e="$nexp" 'BEGIN{OFS=":"} $1==u{$3=e} {print}' "$DB" > "$DB.tmp" && mv "$DB.tmp" "$DB"
msg_ok "Vencimiento de $SEL ahora: $nexp ($(dias "$nexp"))"; sleep 1 ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

listar_usuarios() {
banner; echo -e " \e[1;38;5;141mUSUARIOS REGISTRADOS${N}\n"
if [ ! -s "$DB" ]; then msg_err "No hay usuarios"; pausa; return; fi
printf " \e[1;38;5;208m%-14s %-8s %-10s %s${N}\n" "USUARIO" "LÍMITE" "ONLINE" "VENCIMIENTO"
while IFS=: read -r u lim exp; do
[ -z "$u" ] && continue
on=$(ps -u "$u" -o comm= 2>/dev/null | grep -c '^sshd$')
printf " %-14s %-8s %-10s %s\n" "$u" "$lim" "$on" "$(dias "$exp")"
done < "$DB"
pausa
}

menu_usuario() {
while true; do
banner; echo -e " \e[1;38;5;141mUSUARIO${N}\n"
op 1 "✚" "Crear usuario"
op 2 "✖" "Eliminar usuario"
op 3 "✎" "Editar usuario"
op 4 "▤" "Ver usuarios"
op 5 "⚠" "Usuarios vencidos"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) crear_usuario ;;
2) eliminar_usuario ;;
3) editar_usuario ;;
4) listar_usuarios ;;
5) vencidos ;;
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
if systemctl is-active --quiet bhttp-server && systemctl is-active --quiet bhttp-shim; then
echo -e " \e[1;32m● BHTTP: activo ($(bhttp_port) → SSH)${N}\n"
else
echo -e " \e[1;31m● BHTTP: inactivo${N}\n"
fi
op 1 "⚡" "Activar WebSocket (80 + 7300)"
op 2 "✖" "Desactivar WebSocket (libera 80 y 7300)"
op 3 "⚡" "Activar HCR Server (8880)"
op 4 "✖" "Desactivar HCR Server (libera 8880)"
op 5 "⚡" "Activar BHTTP (elegís el puerto)"
op 6 "✖" "Desactivar BHTTP (libera su puerto)"
op 7 "⚙" "Diagnóstico de BHTTP"
op 0 "◂" "Volver"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) echo -e " \e[1;38;5;141mCompilando e instalando, aguardá...${N}"
if bash /etc/zumo/activar-protocolos.sh; then
msg_ok "WebSocket (80 → SSH) y BadVPN (7300) activos"
else
msg_err "Falló; revisá 'journalctl -u pdirect-80 -u udpgw-7300'"
fi; pausa ;;
2) echo -e " \e[1;38;5;141mLiberando puertos 80 y 7300...${N}"
bash /etc/zumo/desactivar-protocolos.sh
msg_ok "WebSocket y BadVPN desactivados; puertos 80 y 7300 liberados"; pausa ;;
3) echo -e " \e[1;38;5;141mInstalando HCR Server, aguardá...${N}"
if bash /etc/zumo/activar-hcr.sh; then
msg_ok "HCR Server activo en el puerto 8880 (→ SSH 22)"
else
msg_err "Falló; revisá 'journalctl -u hcr-server'"
fi; pausa ;;
4) echo -e " \e[1;38;5;141mLiberando puerto 8880...${N}"
bash /etc/zumo/desactivar-hcr.sh
msg_ok "HCR Server desactivado; puerto 8880 liberado"; pausa ;;
5) read -rp " Puerto para BHTTP [8080]: " bp; bp=${bp:-8080}
if ! [[ "$bp" =~ ^[0-9]+$ ]] || [ "$bp" -lt 1 ] || [ "$bp" -gt 65535 ]; then
msg_err "Puerto inválido (1-65535)"
else
echo -e " \e[1;38;5;141mInstalando BHTTP en el puerto ${bp}, aguardá...${N}"
if bash /etc/zumo/activar-bhttp.sh "$bp"; then
msg_ok "BHTTP activo en el puerto $bp (→ SSH 22)"
else
msg_err "Falló; usá la opción 7 (diagnóstico)"
fi
fi; pausa ;;
6) echo -e " \e[1;38;5;141mLiberando el puerto de BHTTP...${N}"
bash /etc/zumo/desactivar-bhttp.sh
msg_ok "BHTTP desactivado; puerto liberado"; pausa ;;
7) diag_bhttp ;;
0) return ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
}

while true; do
banner
op 1 "●" "Usuario"
op 2 "⚡" "Protocolos"
op 0 "✖" "Salir"
echo -e "\n $L"; read -rp " Opción: " o
case $o in
1) menu_usuario ;;
2) menu_protocolos ;;
0) clear; exit 0 ;;
*) msg_err "Opción inválida"; sleep 1 ;;
esac
done
