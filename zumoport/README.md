# Zumo Port

App Android para escanear **IP, rangos de IP, puertos y subdominios**. Va aparte de Zumo VPN (`android/`): otro paquete (`com.zumo.port`), otra firma y otro flujo de compilación.

## Qué hace

- **Escanear**: una IP, un rango (`192.168.1.1-254`, `10.0.0.1-10.0.1.50`), un bloque (`10.0.0.0/24`, de /16 a /32) o un dominio; varios a la vez separados por coma. Tocás «Mi red» y arma sola el /24 de tu WiFi.
- **Puertos**: `80,443`, rangos como `8000-8100`, o las listas «80 y 443», «Web», «Comunes» y «1–1024».
- **Qué te dice de cada puerto abierto**: en 80/8080/… hace un `HEAD` y muestra el código HTTP y el `Server`; en 443/8443/… hace el saludo TLS y después el `HEAD` (HTTPS 200, redirecciones, errores); en SSH/FTP/SMTP/… lee el banner. Tocando un equipo con puertos abiertos podés ver su DNS inverso (el dominio que esa IP tenga configurado, si tiene), copiar la IP o abrir el puerto web en el navegador. Los filtros dejan ver solo un puerto (por ejemplo «cuáles IP dan OK en el 443») o solo los que contestan bien.
- **Subdominios**: junta nombres de varias fuentes que se marcan con chips y se **consultan todas a la vez**: crt.sh y CertSpotter (certificados públicos), HackerTarget, AlienVault OTX (DNS pasivo), URLScan, Anubis, Subdomain Center y Wayback Machine (páginas archivadas), más una lista de nombres comunes que se prueba contra el DNS. Ninguna pide clave. Al terminar, una línea dice cuáles respondieron (`✔ crt.sh 120`) y cuáles no (`✘ URLScan`), porque a veces alguna está saturada o bloqueada en tu red; cada subdominio dice de qué fuentes salió. Ignora los DNS comodín y marca los que solo aparecen en las fuentes pero hoy no responden. Un toque sobre un subdominio: escanear sus puertos, copiarlo o abrirlo. De cada subdominio que responde también muestra (se pueden apagar con dos chips):
  - **ASN**: a qué sistema autónomo pertenecen sus IP, por ejemplo `AS13335 · CLOUDFLARENET · US`. Se consulta a Team Cymru (`origin.asn.cymru.com`) por DNS sobre HTTPS (Cloudflare, y Google de respaldo), sin claves ni cuenta y por la red que elegiste en la app. Las IP de redes locales no tienen ASN y no se consultan; si el servicio no contesta, la fila queda en «sin dato» y la búsqueda sigue.
  - **Puertos abiertos**: prueba una conexión TCP a cada puerto de la lista (por defecto `80, 443, 8080, 8443, 8880, 2052, 2053, 2082, 2083, 2086, 2087, 2095, 2096, 22, 21, 25`; se edita en el campo, hasta 40). Se prueba una vez por IP aunque varios subdominios la compartan.
  - La lista de subdominios aparece primero y cada fila se completa sola cuando llega su ASN y sus puertos. Copiar y Compartir llevan solo los nombres.
  - **Probar HTTP** (botón abajo del todo, cuando termina el escaneo): a cada subdominio con puertos abiertos le pide la página de inicio (`HEAD /` con su nombre en `Host`, y en SNI si es HTTPS) por cada IP y puerto abierto, y muestra el código (`80 · HTTP 301 · cloudflare → https://… · 120 ms`, `443 · HTTPS 200`). Los puertos de Cloudflare (2052/2082/2086/2095/8880 en HTTP y 2053/2083/2087/2096 en HTTPS) se reconocen solos; un puerto raro se prueba como HTTP y, si no contesta, como HTTPS; lo que no habla web muestra su saludo (SSH, FTP…). Resume cuántos responden bien (2xx/3xx), cuántos dan error (4xx/5xx) y cuántos no hablan web, y el chip «Solo HTTP OK» deja ver solo los que responden bien. Copiar y Compartir llevan solo los nombres de dominio, uno por línea.
- **Por qué red salir**: «Datos móviles» (por defecto), «WiFi» o «Automática». Con datos móviles, el escaneo y las consultas DNS salen por la operadora aunque el WiFi esté prendido (la app pide la red celular al sistema y ata sus conexiones a ella; al terminar la suelta). Las IP de una red local (192.168…, 10…) no se alcanzan por datos móviles: la app lo avisa y hay que elegir WiFi o Automática.
- Copiar y compartir los resultados como texto.

Velocidades: Suave (40 hilos), Normal (150), Rápida (400). Tope: 65 536 equipos y 3 millones de sondeos por escaneo. Medido en la JVM contra 127.0.0.0/18: ~22 000 sondeos/s en velocidad Rápida.

## Estructura

| Archivo | Qué es |
|---|---|
| `Objetivos.kt` | Entiende IP, rangos, CIDR y dominios; lista de puertos y presets. |
| `Escaner.kt` | Sondeo TCP en paralelo + verificación web/TLS + banners. Cancelable. |
| `Subdominios.kt` | Fuentes de nombres (`Fuente`: crt.sh, CertSpotter, HackerTarget, OTX, URLScan, Anubis, Subdomain Center, Wayback, lista), resolución, filtro de DNS comodín y, después, ASN y puertos abiertos de cada IP. |
| `Probador.kt` | El botón «Probar»: HEAD HTTP/HTTPS a cada puerto abierto de cada subdominio. |
| `Hilos.kt` | Hilos y esperas que se pueden cancelar al instante (Detener). |
| `Asn.kt` | IP → ASN con Team Cymru (TXT por DNS sobre HTTPS), con memoria y corte si el servicio no contesta. |
| `Red.kt` | Elige por qué red salen los escaneos (datos móviles, WiFi o la del sistema). |
| `Ui.kt`, `PaginaEscanear.kt`, `PaginaSubdominios.kt`, `MainActivity.kt` | Pantalla en código (sin XML): cabecera, tres pestañas y sus páginas. |
| `app/src/test/…/NucleoTest.kt` | 33 pruebas del núcleo (sockets locales reales, sin internet). |

## Compilar el APK

Lo compila GitHub: **Actions → Compilar Zumo Port → Run workflow** (también corre solo al subir cambios de `zumoport/` a `main`). Corre las pruebas, compila y publica `zumo-port.apk` en la rama `apk-port` y como artefacto de la ejecución (`zumo-port-apk`). La firma se crea una vez y queda en el caché de GitHub, así cada APK nuevo se instala encima del anterior. El caché se borra tras 7 días sin compilar: para que la firma no cambie nunca, usa los mismos secretos fijos de Zumo VPN (`ZUMO_KEYSTORE_B64` y `ZUMO_KS_PASS`) si ya los cargaste.

Uso responsable: escaneá solo equipos, redes y dominios tuyos o con permiso de su dueño.
