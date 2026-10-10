package com.zumo.cripto

/** Una cripto en el ranking de CoinGecko (sin datos de exchanges todavía). */
data class Moneda(val id: String, val simbolo: String, val nombre: String, val precioUsd: Double, val puestoRanking: Int)

/** El precio de una cripto en un exchange puntual, ya convertido a dólares. */
data class Ticker(
    val exchange: String,
    val par: String,          // "BTC/USDT", por ejemplo
    val precioUsd: Double,
    val volumenUsd: Double,
    val confianza: String,    // "green", "yellow", "red" o "" si CoinGecko no lo informa
    val urlOperar: String,
    val anomalo: Boolean,
    val desactualizado: Boolean,
)

/** El resultado de comparar una cripto entre varios exchanges: dónde está más barata y más cara. */
data class Oportunidad(
    val moneda: Moneda,
    val tickers: List<Ticker>,     // ya filtrados y ordenados de menor a mayor precio
) {
    val barato: Ticker get() = tickers.first()
    val caro: Ticker get() = tickers.last()
    val margenPct: Double get() = if (barato.precioUsd <= 0) 0.0 else (caro.precioUsd - barato.precioUsd) / barato.precioUsd * 100.0
    val exchanges: Int get() = tickers.size
}
