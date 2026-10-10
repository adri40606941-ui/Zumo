package com.zumo.cripto

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Lo que vuelve de un pedido HTTP: el código de estado y el cuerpo (null si no se pudo leer o el código no es 2xx). */
class ResultadoPedido(val codigo: Int, val cuerpo: String?)

/** Las URL de la API pública de CoinGecko que usa la app. */
object CoinGeckoApi {
    const val BASE = "https://api.coingecko.com/api/v3"

    fun urlMercados(cantidad: Int): String =
        "$BASE/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=$cantidad&page=1&sparkline=false&price_change_percentage=false"

    fun urlTickers(id: String): String =
        "$BASE/coins/$id/tickers?include_exchange_logo=false&depth=false&order=volume_desc"
}

/** El pedido HTTP de verdad. La clave es opcional: sin ella la API pública igual contesta, con un límite más bajo. */
object Red {
    fun pedir(url: String, apiKey: String): ResultadoPedido {
        var con: HttpURLConnection? = null
        return try {
            con = (URL(url).openConnection() as HttpURLConnection).also {
                it.connectTimeout = 10000
                it.readTimeout = 15000
                it.setRequestProperty("Accept", "application/json")
                it.setRequestProperty("User-Agent", "ZumoCripto/1.0")
                if (apiKey.isNotBlank()) it.setRequestProperty("x-cg-demo-api-key", apiKey)
            }
            val codigo = con.responseCode
            val flujo = if (codigo in 200..299) con.inputStream else con.errorStream
            val cuerpo = flujo?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
            ResultadoPedido(codigo, if (codigo in 200..299) cuerpo else null)
        } catch (_: Exception) {
            ResultadoPedido(0, null)
        } finally {
            con?.disconnect()
        }
    }
}

/** Convierte el JSON de CoinGecko en los modelos de la app. Funciones puras, sin red: fáciles de probar. */
object Parseo {
    /** La respuesta de /coins/markets: una lista de monedas. */
    fun monedas(json: String): List<Moneda> {
        val out = ArrayList<Moneda>()
        val arr = try { JSONArray(json) } catch (_: Exception) { return out }
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optString("id"); if (id.isBlank()) continue
            val precio = o.optDouble("current_price", Double.NaN)
            if (precio.isNaN() || precio <= 0) continue
            val puesto = o.optInt("market_cap_rank", i + 1)
            out.add(Moneda(id, o.optString("symbol").uppercase(), o.optString("name", id), precio, puesto))
        }
        return out
    }

    /** La respuesta de /coins/{id}/tickers: el precio en cada exchange. */
    fun tickers(json: String): List<Ticker> {
        val out = ArrayList<Ticker>()
        val raiz = try { JSONObject(json) } catch (_: Exception) { return out }
        val arr = raiz.optJSONArray("tickers") ?: return out
        for (i in 0 until arr.length()) {
            val t = arr.optJSONObject(i) ?: continue
            val mercado = t.optJSONObject("market")
            val exchange = mercado?.optString("name").orEmpty().ifBlank { mercado?.optString("identifier").orEmpty() }
            if (exchange.isBlank()) continue
            val convertido = t.optJSONObject("converted_last")
            val precio = convertido?.optDouble("usd", Double.NaN) ?: Double.NaN
            if (precio.isNaN() || precio <= 0) continue
            val volumen = t.optJSONObject("converted_volume")?.optDouble("usd", 0.0) ?: 0.0
            out.add(
                Ticker(
                    exchange = exchange,
                    par = "${t.optString("base")}/${t.optString("target")}",
                    precioUsd = precio,
                    volumenUsd = volumen,
                    confianza = t.optString("trust_score", ""),
                    urlOperar = t.optString("trade_url", ""),
                    anomalo = t.optBoolean("is_anomaly", false),
                    desactualizado = t.optBoolean("is_stale", false),
                )
            )
        }
        return out
    }
}
