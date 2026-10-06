#!/bin/bash
# Prueba de la publicación del centro: extrae zumo-publicar de centro/instalar-centro.sh, lo corre
# contra un repo de juguete y comprueba que publica los instaladores (sin .git ni la app),
# que se puede repetir y que lleva el código secreto en la ruta.
# Uso: bash tests/prueba-centro.sh
set -u
AQUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; FALLOS=0
chequear() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FALLA $1: esperado '$2', real '$3'"; FALLOS=$((FALLOS+1)); fi; }
mkdir -p "$T/etc" "$T/src/android" "$T/src/bot"
git -C "$T/src" init -q
echo "instalador" > "$T/src/install.sh"; echo "app" > "$T/src/android/x"; echo "bot" > "$T/src/bot/zs.py"
git -C "$T/src" add -A; git -C "$T/src" -c user.name=t -c user.email=t@t commit -qm init
printf 'SECRETO=abc123\n' > "$T/etc/centro.env"
sed -n "/<<'PUB'/,/^PUB$/p" "$AQUI/centro/instalar-centro.sh" | sed '1d;$d' \
 | sed -e "s#/etc/zumo/centro.env#$T/etc/centro.env#; s#SRC=/opt/zumo-src; WEB=/var/www/zumo/#SRC=$T/src; WEB=$T/www/#; s#chmod -R a+rX /var/www/zumo#chmod -R a+rX $T/www#" > "$T/pub.sh"
bash "$T/pub.sh" >/dev/null 2>&1; chequear "primera publicación" "instalador" "$(cat "$T/www/abc123/install.sh" 2>/dev/null)"
chequear "bot publicado" "bot" "$(cat "$T/www/abc123/bot/zs.py" 2>/dev/null)"
chequear "sin la app" "no" "$([ -e "$T/www/abc123/android" ] && echo si || echo no)"
chequear "sin .git" "no" "$([ -e "$T/www/abc123/.git" ] && echo si || echo no)"
echo "nuevo" > "$T/src/install.sh"; git -C "$T/src" -c user.name=t -c user.email=t@t commit -qam dos
bash "$T/pub.sh" >/dev/null 2>&1; chequear "republicar" "nuevo" "$(cat "$T/www/abc123/install.sh")"
chequear "sin restos" "no" "$([ -e "$T/www/abc123.viejo" ] && echo si || echo no)"
# ZUMO_BASE se recuerda en base.url (misma lógica que usan install.sh, actualizar.sh e instalar-bot.sh)
SNIP=$(grep -m1 '^if \[ -n "${ZUMO_BASE:-}" \]' "$AQUI/actualizar.sh" | sed "s#/etc/zumo#$T/zb#g")
ZUMO_BASE="https://d.example/xyz/" bash -c "$SNIP"; chequear "base.url" "https://d.example/xyz" "$(cat "$T/zb/base.url")"
rm -rf "$T/zb"; ZUMO_BASE="http://inseguro" bash -c "$SNIP"; chequear "rechaza http" "no" "$([ -e "$T/zb/base.url" ] && echo si || echo no)"
# Reinstalar el centro no vuelve a preguntar lo que ya se cargó (token, ID, contraseña, email)
mkdir -p "$T/re"
printf 'BOT_TOKEN=tok:EN=1\nADMINS=\nRESPALDO_PASS=pa ss$x\n' > "$T/re/bot.env"
sed -n '/^guardado() {/,/^\[ -z "\${EMAIL+x}" \]/p' "$AQUI/centro/instalar-centro.sh" | sed "s#/etc/zumo/bot.env#$T/re/bot.env#; s#/etc/zumo/base.url#$T/re/base.url#" > "$T/re/snip.sh"
echo https://d.example/xyz > "$T/re/base.url"
r=$(bash -c ". $T/re/snip.sh; printf '%s|%s|%s|%s|%s' \"\$BOT_TOKEN\" \"\${ADMINS+set}\" \"\$RESPALDO_PASS\" \"\${EMAIL+set}\" \"\$EMAIL\"")
chequear "reusa token, contraseña y email" "tok:EN=1|set|pa ss\$x|set|" "$r"
r=$(BOT_TOKEN=otro bash -c ". $T/re/snip.sh; printf '%s' \"\$BOT_TOKEN\"")
chequear "lo que viene por entorno gana" "otro" "$r"
rm -f "$T/re/base.url"
r=$(bash -c ". $T/re/snip.sh; printf '%s' \"\${EMAIL+set}\"")
chequear "centro nuevo: el email sí se pregunta" "" "$r"
[ "$FALLOS" -eq 0 ] && echo "TODO OK" || { echo "$FALLOS fallos"; exit 1; }
