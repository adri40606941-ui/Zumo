# Zumo

App de túnel para Android reducida a lo esencial: **solo Payload + SSH**. No
tiene bHTTP, HCR, pDirect, badvpn ni ningún otro transporte en la interfaz.

## Qué hace

1. Abre un socket TCP al host SSH.
2. Escribe tu **payload** (plantilla HTTP) en ese socket.
3. Hace el handshake **SSH** sobre esa misma conexión.
4. Expone un proxy **SOCKS5 local en `127.0.0.1:1080`**.
5. Levanta una interfaz **VPN (TUN)** para todo el dispositivo y enruta todo
   por el túnel con el **motor que elijas** (ver abajo).

## Motores VPN (se elige al conectar)

Al tocar **Conectar**, la app pregunta qué motor usar:

- **hev-socks5-tunnel** (recomendado) — un solo `.so`, liviano.
  Fuente: https://github.com/heiher/hev-socks5-tunnel
- **badvpn-tun2socks** — el clásico badvpn, el mismo que usás en el VPS.
  Fuente (listo para Android): https://github.com/shadowsocks/badvpn

Los dos hacen lo mismo: toman la interfaz TUN y relayan todo por el SOCKS5 del
túnel SSH. Las librerías nativas van en `app/src/main/jniLibs/<abi>/` (ver
`app/src/main/jniLibs/README.md`). Para generarlas con el NDK:

```bash
export ANDROID_NDK_HOME=$HOME/Android/Sdk/ndk/<version>
scripts/build_engines.sh
```

> Si falta el `.so` de un motor, ese motor avisa en el registro al conectar y
> el otro sigue andando.

### Límite real: UDP / DNS

El SOCKS del reenvío dinámico de SSH es **solo TCP**. Por eso el tráfico TCP
sale perfecto, pero **UDP (incluido DNS por UDP) no cruza** el túnel SSH tal
cual. Para que resuelva DNS tenés dos caminos: usar DNS sobre TCP, o agregar un
`dnsgw` (en badvpn, `--dnsgw ip:puerto` a un resolver DNS-TCP local). HTTP
Custom resuelve esto internamente; acá queda documentado para que lo sumes si
lo necesitás.

## Tokens del payload

| Token         | Se reemplaza por       |
|---------------|------------------------|
| `[crlf]`      | `\r\n`                 |
| `[cr]`        | `\r`                   |
| `[lf]`        | `\n`                   |
| `[host]`      | host SSH               |
| `[port]`      | puerto SSH             |
| `[host_port]` | `host:puerto`          |
| `[protocol]`  | `HTTP/1.1`             |
| `[ua]`        | User-Agent genérico    |

Ejemplo:

```
GET / HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf][crlf]
```

## Compilar

Necesitás **Android Studio** (o el SDK de Android por línea de comandos). El
proyecto ya trae el Gradle wrapper fijado a Gradle 8.7 y Android Gradle
Plugin 8.5.2.

1. Abrí la carpeta `zumo/` en Android Studio.
2. Dejá que sincronice (descarga el SDK y JSch de Maven Central).
3. `Build > Build APK(s)`  — o por consola:

```bash
./gradlew assembleDebug
# APK en app/build/outputs/apk/debug/app-debug.apk
```

> Nota: el `.apk` no viene compilado porque hace falta el Android SDK, que no
> estaba disponible donde se generó el proyecto. El código fuente está
> completo y listo para compilar.

## Estructura

```
app/src/main/java/com/zumo/tunnel/
  MainActivity.java       UI: payload + SSH, selector de motor, registro
  Payload.java            Renderiza la plantilla del payload
  SshTunnel.java          TCP + payload + SSH (JSch) + SOCKS5 local
  VpnEngine.java          Interface común de los motores
  HevEngine.java          Motor hev-socks5-tunnel (JNI)
  BadvpnEngine.java       Motor badvpn-tun2socks (ejecutable + fd por socket)
  TunnelVpnService.java   VpnService en foreground que arranca el motor elegido
app/src/main/jniLibs/     .so de cada motor por ABI (ver su README)
scripts/build_engines.sh  Clona y compila los dos motores con el NDK
```

> Nota: los `.so` no vienen incluidos (hace falta el Android NDK para
> compilarlos, que no estaba disponible donde se generó el proyecto). Corré
> `scripts/build_engines.sh` con el NDK, o dejá tus propios `.so` en `jniLibs/`.

Dependencia SSH: [`com.github.mwiede:jsch`](https://github.com/mwiede/jsch)
(fork mantenido de JSch, Maven Central).
