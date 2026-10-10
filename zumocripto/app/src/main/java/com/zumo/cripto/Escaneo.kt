package com.zumo.cripto

import java.util.concurrent.atomic.AtomicBoolean

/**
 * Recorre el ranking de criptos de CoinGecko y, para cada una, compara su precio entre exchanges.
 * Pide de a una (la API pública comparte un límite de pedidos por minuto entre todos: pedir en paralelo
 * solo lograría más códigos 429), con una pausa entre pedidos y reintentos con espera creciente si llega un 429.
 */
class Escaneo(
    private val pedir: (String) -> ResultadoPedido,
    private val dormir: (Long) -> Unit = { Thread.sleep(it) },
) {
    private val cancelado = AtomicBoolean(false)
    fun cancelar() = cancelado.set(true)

    fun buscar(
        cantidadMonedas: Int,
        margenMinimoPct: Double,
        pausaMs: Long,
        alEstado: (String) -> Unit,
        alHallar: (Oportunidad) -> Unit,
        alAvanzar: (Int, Int) -> Unit,
    ) {
        cancelado.set(false)
        alEstado("Consultando el ranking de criptos…")
        val rm = pedirConReintentos(CoinGeckoApi.urlMercados(cantidadMonedas))
        if (rm.cuerpo == null) {
            alEstado(mensajeDeError(rm.codigo))
            return
        }
        val monedas = Parseo.monedas(rm.cuerpo)
        if (monedas.isEmpty()) { alEstado("CoinGecko no devolvió ninguna moneda."); return }
        for ((i, m) in monedas.withIndex()) {
            if (cancelado.get()) break
            if (i > 0) dormir(pausaMs)
            if (cancelado.get()) break
            alEstado("Comparando ${m.nombre} (${i + 1}/${monedas.size})…")
            val rt = pedirConReintentos(CoinGeckoApi.urlTickers(m.id))
            if (rt.cuerpo != null) {
                val op = Comparador.construir(m, Parseo.tickers(rt.cuerpo))
                if (op != null && op.margenPct >= margenMinimoPct) alHallar(op)
            }
            alAvanzar(i + 1, monedas.size)
        }
    }

    /**
     * Busca una sola cripto por símbolo, id o nombre, entre las [entreTop] primeras del ranking, y compara sus precios.
     * Devuelve null (y avisa por [alEstado]) si no se encontró, si CoinGecko no respondió o si no hay exchanges suficientes.
     */
    fun buscarUna(consulta: String, alEstado: (String) -> Unit, entreTop: Int = 250): Oportunidad? {
        cancelado.set(false)
        val q = consulta.trim().lowercase()
        if (q.isEmpty()) { alEstado("Escribí una cripto, por ejemplo BTC."); return null }
        alEstado("Buscando \"${consulta.trim()}\" en el ranking…")
        val rm = pedirConReintentos(CoinGeckoApi.urlMercados(entreTop))
        if (rm.cuerpo == null) { alEstado(mensajeDeError(rm.codigo)); return null }
        val m = Parseo.monedas(rm.cuerpo).firstOrNull {
            it.simbolo.lowercase() == q || it.id.lowercase() == q || it.nombre.lowercase() == q
        }
        if (m == null) {
            alEstado("No encontré \"${consulta.trim()}\" entre las $entreTop primeras del ranking.")
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
    }
}
