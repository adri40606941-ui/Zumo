#!/bin/bash
# Actualiza el panel "zumo" (/usr/local/bin/zumo) con la última versión de panel.sh del repo.
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }

URL="${ZUMO_BASE:-$(cat /etc/zumo/base.url 2>/dev/null || echo https://raw.githubusercontent.com/adri40606941-ui/Zumo/main)}/panel.sh?nocache=$(date +%s)"
TMP=$(mktemp)

echo "Descargando panel.sh..."
if ! curl -fsSL "$URL" -o "$TMP"; then
echo "✘ No se pudo descargar panel.sh"
rm -f "$TMP"
exit 1
fi

if ! bash -n "$TMP"; then
echo "✘ El panel.sh descargado tiene errores de sintaxis; no se instala"
rm -f "$TMP"
exit 1
fi

if [ -f /usr/local/bin/zumo ]; then
cp /usr/local/bin/zumo /usr/local/bin/zumo.bak
fi

install -m 0755 "$TMP" /usr/local/bin/zumo
rm -f "$TMP"
echo "✔ Panel actualizado. Escribí 'zumo' para abrirlo."
echo "  (si algo sale mal: cp /usr/local/bin/zumo.bak /usr/local/bin/zumo)"
