# Zumo Cripto

App Android para comparar el precio de cada criptomoneda entre distintos exchanges y encontrar diferencias (margen) leyendo directamente las APIs públicas de los exchanges. Va aparte de Zumo VPN (`android/`) y Zumo Port (`zumoport/`): otro paquete (`com.zumo.cripto`), otra firma y otro flujo de compilación.

## Qué hace

- Lee los precios de **15 exchanges** directamente desde sus APIs públicas, sin clave: Binance, Bybit, OKX, KuCoin, Gate.io, Bitget, MEXC, HTX, Crypto.com, LBank, XT.com, Poloniex, Bitstamp, WhiteBIT y BingX. Cada uno devuelve todos sus pares en una sola llamada.
- Se queda con los pares contra **USDT, USDC o USD** (todos ≈ 1 dólar) y deja afuera los que tienen menos de 20 000 USD de volumen en 24 h, porque sus precios no son confiables.
- Arma cada activo que se opera en dos o más exchanges (si un mismo símbolo tiene precios muy distintos, más de 50 % de salto, lo toma por monedas diferentes y usa solo el grupo donde coinciden más exchanges), y muestra dónde está más barato y dónde más caro, con la diferencia en porcentaje.
- Para cada oportunidad, mira si podés **enviar** el activo del exchange barato al caro: busca una red en común donde el barato deje retirar y el caro reciba. Se confirma sin clave con KuCoin, Gate.io, Bitget, HTX, WhiteBIT, XT.com y Poloniex, que publican ese dato abierto; con Binance, Bybit, OKX, MEXC y BingX hace falta tu clave de solo lectura (pestaña Cuentas). Con Crypto.com, LBank y Bitstamp sale «no verificable».
- En la pestaña **Cuentas** podés cargar una clave API **de solo lectura** de Binance, Bybit, OKX, MEXC o BingX (nunca usuario ni contraseña). Con ella la app lee las redes de retiro y depósito reales de tu cuenta y la comisión de retiro, y esos exchanges dejan de salir «no verificable». La clave se guarda cifrada en el teléfono (Android Keystore) y solo se usa para consultar al propio exchange.
- Calcula el neto con una comisión fija de 0,1 % por operación (compra y venta). Tocando una fila, ves todos los exchanges y un enlace para operar.
- Muestra solo oportunidades de al menos 1 % de margen, y un único filtro, «Solo las que se pueden enviar», que se aplica al instante sin volver a pedir nada.
- Si un exchange no responde (por ejemplo, Binance y Bybit bloquean algunos países), la app lo informa y compara con los demás.

**No es consejo financiero.** El margen es antes de comisiones, retiros y de mover el activo entre exchanges, y los precios cambian en segundos. La pestaña Info lo aclara.

## Estructura

| Archivo | Qué es |
|---|---|
| `Modelos.kt` | `Moneda`, `Ticker` y `Oportunidad` (con `margenPct`, `barato`, `caro`). |
| `Exchanges.kt` | Las 15 fuentes: URL pública de cada exchange y cómo leer su respuesta. |
| `BuscadorExchanges.kt` | Lee cada exchange, agrupa los pares por activo (`Agregador`) y arma las oportunidades. |
| `Transferencias.kt` | Redes y estado de retiro/depósito de KuCoin, Gate.io, Bitget, HTX, WhiteBIT, XT.com y Poloniex; decide si un envío es posible. |
| `Cuentas.kt` | Firmas HMAC, `Credencial` y los lectores con clave de Binance, Bybit, OKX, MEXC y BingX. |
| `AlmacenCuentas.kt`, `PaginaCuentas.kt` | Guardado cifrado de las claves y la pestaña para cargarlas y probarlas. |
| `Red.kt` | El pedido HTTP (con encabezados firmados cuando hay cuenta). |
| `Calculadora.kt` | El neto después de comisiones. |
| `Comparador.kt` | Las reglas de negocio: descarta anómalos/desactualizados/baja confianza, se queda con el de mayor volumen por exchange repetido, exige un mínimo de exchanges. |
| `Ui.kt`, `PaginaOportunidades.kt`, `MainActivity.kt` | Pantalla en código (sin XML): cabecera, dos pestañas (Oportunidades, Info) y sus páginas. |
| `app/src/test/…/NucleoTest.kt` | Pruebas del núcleo: formato de cada exchange, separación de pares, agregado, comparador y búsqueda (sin red real). |

## Compilar el APK

Lo compila GitHub: **Actions → Compilar Zumo Cripto → Run workflow** (también corre solo al subir cambios de `zumocripto/` a `main`). Corre las pruebas, compila y publica `zumo-cripto.apk` en la rama `apk-cripto` y como artefacto de la ejecución (`zumo-cripto-apk`). La firma se crea una vez y queda en el caché de GitHub, así cada APK nuevo se instala encima del anterior. Para que la firma no cambie nunca, usa los mismos secretos fijos de Zumo VPN y Zumo Port (`ZUMO_KEYSTORE_B64` y `ZUMO_KS_PASS`).

## Fuentes

Precios leídos de las APIs públicas de Binance, Bybit, OKX, KuCoin, Gate.io, Bitget, MEXC, HTX, Crypto.com, LBank, XT.com, Poloniex, Bitstamp, WhiteBIT y BingX. Envíos (redes, retiro y depósito) de KuCoin, Gate.io, Bitget, HTX, WhiteBIT, XT.com y Poloniex sin clave, y de Binance, Bybit, OKX, MEXC y BingX con tu clave de solo lectura.
