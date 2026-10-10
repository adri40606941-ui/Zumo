package com.zumo.cripto

import org.json.JSONArray
import org.json.JSONObject

/**
 * Un par (base/cotización) que un exchange opera, ya leído de su API pública.
 * El precio está en la cotización, que es USDT, USDC o USD: todas valen ~1 dólar.
 */
data class Cotizacion(
    val exchange: String,
    val base: String,
    val quote: String,
    val precio: Double,
    val volumen: Double,        // volumen de 24 h en la cotización (≈ dólares)
    val urlOperar: String,
)

/** Un exchange del que la app lee precios: su URL pública y cómo interpretar su respuesta. */
class Fuente(val nombre: String, val url: String, private val leer: (String) -> List<Cotizacion>) {
    /** Devuelve los pares de la respuesta; si el JSON no tiene el formato esperado, devuelve lista vacía en vez de romper. */
    fun parsear(json: String): List<Cotizacion> = try { leer(json) } catch (_: Exception) { emptyList() }
}

/** Los exchanges que la app consulta y el lector de cada uno. Funciones puras: fáciles de probar. */
object Exchanges {
    /** Solo se comparan pares contra estas cotizaciones (todas ≈ 1 dólar). Pares con BTC, ETH o EUR no se mezclan. */
    val COTIZACIONES = listOf("USDT", "USDC", "USD")

    /** Stablecoins: nunca se comparan como si fueran una cripto. */
    val STABLES = setOf("USDT", "USDC", "USD", "FDUSD", "DAI", "BUSD", "TUSD", "USDD", "USDE", "PYUSD")

    /**
     * Separa un par con o sin separador ("BTCUSDT", "BTC-USDT", "BTC_USDT").
     * Devuelve null si no es un par que se compara (cotización distinta, stablecoin como base, etc.).
     */
    fun separar(par: String, separador: String? = null): Pair<String, String>? {
        val p = par.trim().uppercase()
        val (base, quote) = if (separador != null) {
            val partes = p.split(separador)
            if (partes.size != 2) return null
            partes[0] to partes[1]
        } else {
            val q = COTIZACIONES.firstOrNull { p.endsWith(it) && p.length > it.length } ?: return null
            p.removeSuffix(q) to q
        }
        if (quote !in COTIZACIONES || base.isBlank() || base in STABLES) return null
        return base to quote
    }

    private fun numero(o: JSONObject, campo: String): Double = o.optString(campo).toDoubleOrNull() ?: Double.NaN

    private fun cotizar(
        filas: JSONArray, exchange: String, campoPar: String, campoPrecio: String, campoVolumen: String,
        separador: String?, urlOperar: (String, String) -> String,
    ): List<Cotizacion> {
        val out = ArrayList<Cotizacion>()
        for (i in 0 until filas.length()) {
            val o = filas.optJSONObject(i) ?: continue
            val (base, quote) = separar(o.optString(campoPar), separador) ?: continue
            val precio = numero(o, campoPrecio)
            val volumen = numero(o, campoVolumen)
            if (precio.isNaN() || precio <= 0 || volumen.isNaN()) continue
            out.add(Cotizacion(exchange, base, quote, precio, volumen, urlOperar(base, quote)))
        }
        return out
    }

    val BINANCE = Fuente("Binance", "https://api.binance.com/api/v3/ticker/24hr") { json ->
        cotizar(JSONArray(json), "Binance", "symbol", "lastPrice", "quoteVolume", null) { b, q -> "https://www.binance.com/en/trade/${b}_$q" }
    }

    val BYBIT = Fuente("Bybit", "https://api.bybit.com/v5/market/tickers?category=spot") { json ->
        val lista = JSONObject(json).getJSONObject("result").getJSONArray("list")
        cotizar(lista, "Bybit", "symbol", "lastPrice", "turnover24h", null) { b, q -> "https://www.bybit.com/trade/spot/$b/$q" }
    }

    val OKX = Fuente("OKX", "https://www.okx.com/api/v5/market/tickers?instType=SPOT") { json ->
        cotizar(JSONObject(json).getJSONArray("data"), "OKX", "instId", "last", "volCcy24h", "-") { b, q ->
            "https://www.okx.com/trade-spot/${b.lowercase()}-${q.lowercase()}"
        }
    }

    val KUCOIN = Fuente("KuCoin", "https://api.kucoin.com/api/v1/market/allTickers") { json ->
        val lista = JSONObject(json).getJSONObject("data").getJSONArray("ticker")
        cotizar(lista, "KuCoin", "symbol", "last", "volValue", "-") { b, q -> "https://www.kucoin.com/trade/$b-$q" }
    }

    val GATE = Fuente("Gate.io", "https://api.gateio.ws/api/v4/spot/tickers") { json ->
        cotizar(JSONArray(json), "Gate.io", "currency_pair", "last", "quote_volume", "_") { b, q -> "https://www.gate.io/trade/${b}_$q" }
    }

    val BITGET = Fuente("Bitget", "https://api.bitget.com/api/v2/spot/market/tickers") { json ->
        cotizar(JSONObject(json).getJSONArray("data"), "Bitget", "symbol", "lastPr", "quoteVolume", null) { b, q ->
            "https://www.bitget.com/spot/$b$q"
        }
    }

    val MEXC = Fuente("MEXC", "https://api.mexc.com/api/v3/ticker/24hr") { json ->
        cotizar(JSONArray(json), "MEXC", "symbol", "lastPrice", "quoteVolume", null) { b, q -> "https://www.mexc.com/exchange/${b}_$q" }
    }

    val TODAS: List<Fuente> = listOf(BINANCE, BYBIT, OKX, KUCOIN, GATE, BITGET, MEXC)
}
