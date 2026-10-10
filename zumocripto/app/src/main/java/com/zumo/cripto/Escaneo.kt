package com.zumo.cripto

import java.util.concurrent.atomic.AtomicBoolean

/**
 * Recorre el ranking de criptos de CoinGecko entre dos puestos y, para cada una, compara su precio entre exchanges.
 * Pide de a una (la API pública comparte un límite de pedidos por minuto entre todos: pedir en paralelo
 * solo lograría más códigos 429), con una pausa que se adapta y reintentos con espera creciente si llega un 429.
 */
class Escaneo(
    private val pedir: (String) -> ResultadoPedido,
    private val dormir: (Long) -> Unit = { Thread.sleep(it) },
) {
    private val cancelado = AtomicBoolean(false)
    fun cancelar() = cancelado.set(true)

    /** Compara las monedas de los puestos [desde] a [hasta] (inclusive). El rango se recorta a [MAX_RANGO] monedas. */
    fun buscar(
        desde: Int,
        hasta: Int,
        margenMinimoPct: Double,
        pausaMs: Long,
        alEstado: (String) -> Unit,
        alHallar: (Oportunidad) -> Unit,
        alAvanzar: (Int, Int) -> Unit,
    ) {
        cancelado.set(false)
        val d = desde.coerceAtLeast(1)
        val h = hasta.coerceAtLeast(d).coerceAtMost(d + MAX_RANGO - 1)
        alEstado("Consultando el ranking (puestos $d a $h)…")
        val monedas = ArrayList<Moneda>()
        for (pagina in CoinGeckoApi.paginaDe(d)..CoinGeckoApi.paginaDe(h)) {
            if (cancelado.get()) return
            val rm = pedirConReintentos(CoinGeckoApi.urlMercados(pagina))
            if (rm.cuerpo == null) {
                alEstado(mensajeDeError(rm.codigo))
                return
            }
            val base = (pagina - 1) * CoinGeckoApi.POR_PAGINA
            monedas.addAll(Parseo.monedas(rm.cuerpo, base).filter { it.puestoRanking in d..h })
        }
        if (monedas.isEmpty()) { alEstado("CoinGecko no devolvió monedas en los puestos $d a $h."); return }

        // La pausa arranca en [pausaMs] y se adapta: se acorta mientras CoinGecko responde bien, y se alarga si llega un 429.
        var pausa = pausaMs.coerceIn(PAUSA_MIN_MS, PAUSA_MAX_MS)
        for ((i, m) in monedas.withIndex()) {
            if (cancelado.get()) break
            if (i > 0) dormir(pausa)
            if (cancelado.get()) break
            alEstado("Comparando ${m.nombre} (${i + 1}/${monedas.size})…")
            val rt = pedirConReintentos(CoinGeckoApi.urlTickers(m.id))
            pausa = ajustarPausa(pausa, rt.codigo)
            if (rt.cuerpo != null) {
                val op = Comparador.construir(m, Parseo.tickers(rt.cuerpo))
                if (op != null && op.margenPct >= margenMinimoPct) alHallar(op)
            }
            alAvanzar(i + 1, monedas.size)
        }
    }

    /**
     * Busca una sola cripto por símbolo, id o nombre, entre las 250 primeras del ranking, y compara sus precios.
     * Devuelve null (y avisa por [alEstado]) si no se encontró, si CoinGecko no respondió o si no hay exchanges suficientes.
     */
    fun buscarUna(consulta: String, alEstado: (String) -> Unit): Oportunidad? {
        cancelado.set(false)
        val q = consulta.trim().lowercase()
        if (q.isEmpty()) { alEstado("Escribí una cripto, por ejemplo BTC."); return null }
        alEstado("Buscando \"${consulta.trim()}\" en el ranking…")
        val rm = pedirConReintentos(CoinGeckoApi.urlMercados(1))
        if (rm.cuerpo == null) { alEstado(mensajeDeError(rm.codigo)); return null }
        val m = Parseo.monedas(rm.cuerpo).firstOrNull {
            it.simbolo.lowercase() == q || it.id.lowercase() == q || it.nombre.lowercase() == q
        }
        if (m == null) {
            alEstado("No encontré \"${consulta.trim()}\" entre las ${CoinGeckoApi.POR_PAGINA} primeras del ranking.")
            return null
        }
        if (cancelado.get()) return null
        alEstado("Comparando ${m.nombre}…")
        val rt = pedirConReintentos(CoinGeckoApi.urlTickers(m.id))
        if (rt.cuerpo == null) { alEstado(mensajeDeError(rt.codigo)); return null }
        val op = Comparador.construir(m, Parseo.tickers(rt.cuerpo))
        if (op == null) alEstado("${m.nombre} no tiene suficientes exchanges con precio confiable para comparar.")
        return op
    }

    /** Un 429 duplica la pausa (con un empujón); cada respuesta buena la achica de a poco hasta el mínimo. */
    internal fun ajustarPausa(actual: Long, codigo: Int): Long = when {
        codigo == 429 -> (actual * 2 + 500).coerceAtMost(PAUSA_MAX_MS)
        codigo in 200..299 -> (actual * 9 / 10).coerceAtLeast(PAUSA_MIN_MS)
        else -> actual
    }

    private fun mensajeDeError(codigo: Int): String =
        if (codigo == 429) "CoinGecko está limitando los pedidos; probá de nuevo en un minuto."
        else "No se pudo consultar CoinGecko (código $codigo)."

    private fun pedirConReintentos(url: String): ResultadoPedido {
        var intento = 0
        while (true) {
            val r = pedir(url)
            if (r.codigo != 429 || cancelado.get() || intento >= MAX_REINTENTOS) return r
            intento++
            dormir(PAUSA_429_MS * intento)
        }
    }

    companion object {
        const val MAX_REINTENTOS = 3
        const val PAUSA_429_MS = 4000L
        const val PAUSA_MIN_MS = 300L
        const val PAUSA_MAX_MS = 10000L
        /** Tope de monedas por búsqueda: cada una son dos pedidos, así que más de esto tarda demasiado. */
        const val MAX_RANGO = 1000
    }
}
