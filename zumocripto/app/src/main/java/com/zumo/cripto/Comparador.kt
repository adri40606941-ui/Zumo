package com.zumo.cripto

/** Arma una [Oportunidad] a partir de los tickers crudos de una moneda, o null si no hay suficiente para comparar. */
object Comparador {
    /** Confianza mínima para usar un precio. CoinGecko no siempre la informa: "" (vacío) se deja pasar. */
    private val CONFIANZA_DESCARTADA = setOf("red")

    fun construir(moneda: Moneda, tickersCrudos: List<Ticker>, minExchanges: Int = 2): Oportunidad? {
        // por exchange, se queda el ticker de mayor volumen (algunos exchanges aparecen con más de un par)
        val porExchange = LinkedHashMap<String, Ticker>()
        for (t in tickersCrudos) {
            if (t.anomalo || t.desactualizado) continue
            if (t.confianza in CONFIANZA_DESCARTADA) continue
            if (t.precioUsd <= 0) continue
            val actual = porExchange[t.exchange]
            if (actual == null || t.volumenUsd > actual.volumenUsd) porExchange[t.exchange] = t
        }
        val limpios = porExchange.values.sortedBy { it.precioUsd }
        if (limpios.size < minExchanges) return null
        return Oportunidad(moneda, limpios)
    }
}
