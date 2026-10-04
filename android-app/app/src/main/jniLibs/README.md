# jniLibs — librerías nativas de los motores

Poné acá los `.so` de cada motor, por ABI. Quedan así:

```
jniLibs/
  arm64-v8a/     libhev-socks5-tunnel.so   libtun2socks.so
  armeabi-v7a/   libhev-socks5-tunnel.so   libtun2socks.so
  x86_64/        libhev-socks5-tunnel.so   libtun2socks.so
```

- `libhev-socks5-tunnel.so` → motor **hev** (https://github.com/heiher/hev-socks5-tunnel)
- `libtun2socks.so`         → motor **badvpn** (https://github.com/shadowsocks/badvpn)

Para generarlos automáticamente con el NDK, corré:

```
scripts/build_engines.sh
```

(mirá `scripts/build_engines.sh` para los requisitos). Si falta un `.so`, el
motor correspondiente avisa en el registro al conectar y el otro sigue
funcionando.
