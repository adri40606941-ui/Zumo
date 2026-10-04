#!/bin/bash
# Instala o actualiza el limitador de 1 sesión por usuario (limitador-1sesion.sh) con la última versión del repo.
[ "$(id -u)" -eq 0 ] || { echo "Ejecutá como root"; exit 1; }

URL="https://raw.githubusercontent.com/adri40606941-ui/Zumo/main/limitador-1sesion.sh?nocache=$(date +%s)"
TMP=$(mktemp)

echo "Descargando limitador-1sesion.sh..."
if ! curl -fsSL "$URL" -o "$TMP"; then
echo "✘ No se pudo descargar limitador-1sesion.sh"
rm -f "$TMP"
exit 1
fi

if ! bash -n "$TMP"; then
echo "✘ El limitador-1sesion.sh descargado tiene errores de sintaxis; no se instala"
rm -f "$TMP"
exit 1
fi

bash "$TMP"
r=$?
rm -f "$TMP"
exit $r
