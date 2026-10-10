# Zumo Cripto

App Android para comparar el precio de cada criptomoneda entre distintos exchanges y encontrar diferencias (margen) a partir de los datos de **CoinGecko**. Va aparte de Zumo VPN (`android/`) y Zumo Port (`zumoport/`): otro paquete (`com.zumo.cripto`), otra firma y otro flujo de compilación.

## Qué hace

- Recorre el ranking de criptos de CoinGecko por capitalización (Top 50, 100, 200 o 500, a elección).
- Para cada una, pide el precio en cada exchange donde se opera (`/coins/{id}/tickers`) y descarta los precios anómalos, desactualizados o de baja confianza que CoinGecko marca.
- Calcula el margen entre el exchange más barato y el más caro, y muestra solo las que superan el margen mínimo elegido, ordenadas de mayor a menor diferencia.
- Por cada oportunidad: toque para ver el precio completo en cada exchange, con un enlace para operar cuando CoinGecko lo informa, y copiar el resumen.
- La API pública de CoinGecko comparte un límite de pedidos por minuto entre todos los que la usan sin clave, así que la búsqueda pide de a una cripto con una pausa entre cada una, y reintenta con espera creciente si llega un error 429 (demasiados pedidos).

**No es consejo financiero.** El margen mostrado es antes de comisiones, retiros y de mover la cripto entre exchanges, que pueden achicarlo o anularlo del todo; la app lo aclara en la pestaña Info.

## Estructura

| Archivo | Qué es |
|---|---|
| `Modelos.kt` | `Moneda`, `Ticker` y `Oportunidad` (con `margenPct`, `barato`, `caro`). |
| `CoinGeckoApi.kt` | URLs de la API pública, el pedido HTTP real y el parseo del JSON a los modelos. |
| `Comparador.kt` | Las reglas de negocio: descarta anómalos/desactualizados/baja confianza, se queda con el de mayor volumen por exchange repetido, exige un mínimo de exchanges. |
| `Escaneo.kt` | Recorre el ranking, pide de a una con pausa y reintentos ante 429, cancelable. |
| `Ui.kt`, `PaginaOportunidades.kt`, `MainActivity.kt` | Pantalla en código (sin XML): cabecera, dos pestañas (Oportunidades, Info) y sus páginas. |
| `app/src/test/…/NucleoTest.kt` | Pruebas del núcleo: parseo del JSON, reglas del comparador y el bucle de búsqueda (sin red real). |

## Compilar el APK

Lo compila GitHub: **Actions → Compilar Zumo Cripto → Run workflow** (también corre solo al subir cambios de `zumocripto/` a `main`). Corre las pruebas, compila y publica `zumo-cripto.apk` en la rama `apk-cripto` y como artefacto de la ejecución (`zumo-cripto-apk`). La firma se crea una vez y queda en el caché de GitHub, así cada APK nuevo se instala encima del anterior. El caché se borra tras 7 días sin compilar: para que la firma no cambie nunca, usa los mismos secretos fijos de Zumo VPN y Zumo Port (`ZUMO_KEYSTORE_B64` y `ZUMO_KS_PASS`) si ya los cargaste.

## Atribución

Los precios y datos de mercado son de [CoinGecko](https://www.coingecko.com), usados a través de su API pública gratuita.
