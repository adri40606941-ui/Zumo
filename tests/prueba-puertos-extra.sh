#!/bin/bash
# Prueba de puertos extra (camuflaje) con un iptables falso que anota lo que le piden.
set -u
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d /tmp/zumo-extra.XXXXXX); trap 'rm -rf "$T"' EXIT
FALLOS=0
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }
mkdir -p "$T/bin"
cat > "$T/bin/iptables" <<'F'
#!/bin/bash
echo "$*" >> "$LOG"
case "$*" in *" -C "*) exit 1 ;; esac
exit 0
F
chmod +x "$T/bin/iptables"
export PATH="$T/bin:$PATH" LOG="$T/log" ZUMO_EXTRA_CONF="$T/p.conf"
extraer() { awk "index(\$0,\"<<'ZUMOEXTRAPORTS'\"){f=1;next} f&&\$0==\"ZUMOEXTRAPORTS\"{f=0;next} f" "$AQUI/install.sh"; }
extraer > "$T/pe.sh"; bash -n "$T/pe.sh" || { echo "no valida"; exit 1; }
E() { bash "$T/pe.sh" "$@" 2>&1; }

E agregar 8080 80 >/dev/null
chequear "guarda 8080:80" "8080:80" "$(cat "$T/p.conf")"
chequear "pide REDIRECT 8080 -> 80" "1" "$(grep -c -- '--dport 8080 -j REDIRECT --to-ports 80' "$T/log")"
E agregar 2052 2082 >/dev/null
chequear "destino elegido (2052 -> 2082)" "1" "$(grep -c -- '--dport 2052 -j REDIRECT --to-ports 2082' "$T/log")"
chequear "no repite un puerto" "1" "$(E agregar 8080 80 | grep -c 'ya es un puerto extra')"
chequear "rechaza el 22" "1" "$(E agregar 22 80 | grep -c 'no puede ser el 22')"
chequear "rechaza puerto inválido" "1" "$(E agregar abc 80 | grep -c 'inválido')"
chequear "rechaza igual al destino" "1" "$(E agregar 80 80 | grep -c 'no puede ser')"
chequear "lista los dos" "2" "$(E listar | wc -l)"
E quitar 8080 >/dev/null
chequear "quita 8080" "2052:2082" "$(cat "$T/p.conf")"
chequear "quitar uno que no existe falla" "1" "$(E quitar 9999 | grep -c 'no es un puerto extra')"
E quitar 2052 >/dev/null
chequear "sin puertos borra el archivo" "no" "$([ -f "$T/p.conf" ] && echo si || echo no)"
chequear "limpia la cadena" "1" "$(grep -c -- '-X ZUMO_EXTRA' "$T/log")"
[ "$FALLOS" -eq 0 ] && echo "TODO OK" || { echo "$FALLOS fallo(s)"; exit 1; }
