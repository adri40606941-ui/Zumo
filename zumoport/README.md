# Zumo Port

App Android para escanear **IP, rangos de IP, puertos y subdominios**. Va aparte de Zumo VPN (`android/`): otro paquete (`com.zumo.port`), otra firma y otro flujo de compilación.

## Qué hace

- **Escanear**: una IP, un rango (`192.168.1.1-254`, `10.0.0.1-10.0.1.50`), un bloque (`10.0.0.0/24`, de /16 a /32) o un dominio; varios a la vez separados por coma. Tocás «Mi red» y arma sola el /24 de tu WiFi.
- **Puertos**: `80,443`, rangos como `8000-8100`, o las listas «80 y 443», «Web», «Comunes» y «1–1024».
- **Qué te dice de cada puerto abierto**: en 80/8080/… hace un `HEAD` y muestra el código HTTP y el `Server`; en 443/8443/… hace el saludo TLS y después el `HEAD` (HTTPS 200, redirecciones, errores); en SSH/FTP/SMTP/… lee el banner. Tocando un equipo con puertos abiertos podés ver su DNS inverso (el dominio que esa IP tenga configurado, si tiene), copiar la IP o abrir el puerto web en el navegador. Los filtros dejan ver solo un puerto (por ejemplo «cuáles IP dan OK en el 443») o solo los que contestan bien.
- **Subdominios**: busca en certificados públicos (crt.sh), en HackerTarget y probando una lista de nombres comunes contra el DNS; ignora los DNS comodín y marca los que solo aparecen en certificados. Un toque sobre un subdominio: escanear sus puertos, copiarlo o abrirlo.
- **Por qué red salir**: «Datos móviles» (por defecto), «WiFi» o «Automática». Con datos móviles, el escaneo y las consultas DNS salen por la operadora aunque el WiFi esté prendido (la app pide la red celular al sistema y ata sus conexiones a ella; al terminar la suelta). Las IP de una red local (192.168…, 10…) no se alcanzan por datos móviles: la app lo avisa y hay que elegir WiFi o Automática.
- Copiar y compartir los resultados como texto.

Velocidades: Suave (40 hilos), Normal (150), Rápida (400). Tope: 65 536 equipos y 3 millones de sondeos por escaneo. Medido en la JVM contra 127.0.0.0/18: ~22 000 sondeos/s en velocidad Rápida.

## Estructura

| Archivo | Qué es |
|---|---|
| `Objetivos.kt` | Entiende IP, rangos, CIDR y dominios; lista de puertos y presets. |
| `Escaner.kt` | Sondeo TCP en paralelo + verificación web/TLS + banners. Cancelable. |
| `Subdominios.kt` | Fuentes (crt.sh, HackerTarget, lista), resolución y filtro de DNS comodín. |
| `Red.kt` | Elige por qué red salen los escaneos (datos móviles, WiFi o la del sistema). |
| `Ui.kt`, `PaginaEscanear.kt`, `PaginaSubdominios.kt`, `MainActivity.kt` | Pantalla en código (sin XML): cabecera, tres pestañas y sus páginas. |
| `app/src/test/…/NucleoTest.kt` | 33 pruebas del núcleo (sockets locales reales, sin internet). |

## Compilar el APK

Lo compila GitHub: **Actions → Compilar Zumo Port → Run workflow** (también corre solo al subir cambios de `zumoport/` a `main`). Corre las pruebas, compila y publica `zumo-port.apk` en la rama `apk-port` y como artefacto de la ejecución (`zumo-port-apk`). La firma se crea una vez y queda en el caché de GitHub, así cada APK nuevo se instala encima del anterior. El caché se borra tras 7 días sin compilar: para que la firma no cambie nunca, usa los mismos secretos fijos de Zumo VPN (`ZUMO_KEYSTORE_B64` y `ZUMO_KS_PASS`) si ya los cargaste.

Uso responsable: escaneá solo equipos, redes y dominios tuyos o con permiso de su dueño.
