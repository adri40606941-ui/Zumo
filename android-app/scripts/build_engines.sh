#!/usr/bin/env bash
#
# Compila los dos motores VPN y copia los .so a app/src/main/jniLibs/<abi>/.
#
# Requisitos:
#   - Android NDK instalado. Exportá ANDROID_NDK_HOME apuntando a la raíz:
#       export ANDROID_NDK_HOME=$HOME/Android/Sdk/ndk/26.1.10909125
#   - git, make, cmake
#
# Uso:
#   scripts/build_engines.sh
#
set -euo pipefail

if [ -z "${ANDROID_NDK_HOME:-}" ]; then
  echo "ERROR: exportá ANDROID_NDK_HOME (ruta del NDK)."; exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JNILIBS="$ROOT/app/src/main/jniLibs"
WORK="$ROOT/.engines"
ABIS=(arm64-v8a armeabi-v7a x86_64)

mkdir -p "$WORK"

# ---------------------------------------------------------------------------
# 1) hev-socks5-tunnel  ->  libhev-socks5-tunnel.so
# ---------------------------------------------------------------------------
if [ ! -d "$WORK/hev-socks5-tunnel" ]; then
  git clone --recursive https://github.com/heiher/hev-socks5-tunnel "$WORK/hev-socks5-tunnel"
fi
pushd "$WORK/hev-socks5-tunnel" >/dev/null
for ABI in "${ABIS[@]}"; do
  make -j"$(nproc)" \
    ANDROID_NDK_ROOT="$ANDROID_NDK_HOME" \
    PROJECT_NAME=hev-socks5-tunnel \
    ANDROID_ABI="$ABI" \
    -f Android.mk 2>/dev/null || \
  "$ANDROID_NDK_HOME/ndk-build" NDK_PROJECT_PATH=. APP_BUILD_SCRIPT=./Android.mk APP_ABI="$ABI" APP_PLATFORM=android-21
  mkdir -p "$JNILIBS/$ABI"
  find . -name 'libhev-socks5-tunnel.so' -path "*$ABI*" -exec cp {} "$JNILIBS/$ABI/" \;
done
popd >/dev/null

# ---------------------------------------------------------------------------
# 2) badvpn (tun2socks)  ->  libtun2socks.so  (ejecutable con --sock-path)
# ---------------------------------------------------------------------------
if [ ! -d "$WORK/badvpn" ]; then
  git clone https://github.com/shadowsocks/badvpn "$WORK/badvpn"
fi
pushd "$WORK/badvpn" >/dev/null
# El fork de shadowsocks trae jni/Android.mk con el target tun2socks.
"$ANDROID_NDK_HOME/ndk-build" \
  NDK_PROJECT_PATH=. \
  APP_BUILD_SCRIPT=jni/Android.mk \
  APP_ABI="$(IFS=' '; echo "${ABIS[*]}")" \
  APP_PLATFORM=android-21
for ABI in "${ABIS[@]}"; do
  mkdir -p "$JNILIBS/$ABI"
  find libs obj -name 'libtun2socks.so' -path "*$ABI*" -exec cp {} "$JNILIBS/$ABI/" \; 2>/dev/null || true
done
popd >/dev/null

echo
echo "Listo. Revisá:"
for ABI in "${ABIS[@]}"; do
  echo "  $ABI:"; ls -1 "$JNILIBS/$ABI" 2>/dev/null | sed 's/^/    /'
done
