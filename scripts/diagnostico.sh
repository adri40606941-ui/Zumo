#!/bin/bash
# Diagnóstico de desconexiones - Zumo
# Uso: bash scripts/diagnostico.sh   (como root)
echo "========================================="
echo "   DIAGNÓSTICO ZUMO - $(date)"
echo "========================================="

echo -e "\n--- 1) Memoria y swap ---"
free -h
echo
echo "¿El kernel mató procesos por falta de RAM? (OOM killer):"
dmesg 2>/dev/null | grep -i "out of memory\|oom-killer\|killed process" | tail -20
grep -i "out of memory\|oom-killer\|killed process" /var/log/syslog 2>/dev/null | tail -20
grep -i "out of memory\|oom-killer\|killed process" /var/log/kern.log 2>/dev/null | tail -20

echo -e "\n--- 2) CPU y carga ---"
uptime
nproc

echo -e "\n--- 3) Servicios: estado y cuántas veces se reiniciaron ---"
for svc in ssh sshd zumo-limit zumo-datos pdirect-80 udpgw-7300 bhttp-server bhttp-shim bhttp-v2 hcr-server fail2ban; do
  if systemctl list-unit-files 2>/dev/null | grep -q "^$svc.service"; then
    echo "· $svc:"
    systemctl show "$svc" -p NRestarts,ActiveState,SubState 2>/dev/null
    systemctl status "$svc" --no-pager -l 2>/dev/null | head -5
    echo
  fi
done

echo -e "\n--- 4) Conexiones SSH activas ahora mismo ---"
ss -tn state established '( dport = :22 or sport = :22 )' 2>/dev/null | wc -l
who | wc -l

echo -e "\n--- 5) Tabla de conexiones (conntrack) ---"
if command -v conntrack >/dev/null; then
  conntrack -C 2>/dev/null
  cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null
else
  echo "conntrack no instalado"
  cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo "sin soporte conntrack"
fi

echo -e "\n--- 6) Límites de archivos abiertos (ulimit) ---"
ulimit -n
grep -i "nofile\|maxstartups\|maxsessions" /etc/ssh/sshd_config

echo -e "\n--- 7) Configuración actual de sshd (keepalive) ---"
grep -iE "clientalive|tcpkeepalive|maxstartups|maxsessions|maxauthtries" /etc/ssh/sshd_config

echo -e "\n--- 8) Últimas desconexiones en el log de auth ---"
grep -i "disconnect\|Connection closed\|Connection reset\|error" /var/log/auth.log 2>/dev/null | tail -40

echo -e "\n--- 9) fail2ban (si está, por si está baneando de más) ---"
if command -v fail2ban-client >/dev/null; then
  fail2ban-client status 2>/dev/null
  for jail in $(fail2ban-client status 2>/dev/null | grep "Jail list" | sed 's/.*://;s/,//g'); do
    echo "Jail: $jail"
    fail2ban-client status "$jail" 2>/dev/null
  done
else
  echo "fail2ban no instalado"
fi

echo -e "\n--- 10) Espacio en disco ---"
df -h /

echo -e "\n========================================="
echo "   FIN DEL DIAGNÓSTICO"
echo "========================================="
