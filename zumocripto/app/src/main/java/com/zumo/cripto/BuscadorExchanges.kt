package com.zumo.cripto

import java.util.concurrent.atomic.AtomicBoolean

/** Junta los pares de todos los exchanges por activo y arma las oportunidades (un activo en al menos dos exchanges). Función pura. */
object Agregador {
    /** Un par con poco volumen tiene un libro vacío y da diferencias de precio falsas: se ignora. */
    const val VOLUMEN_MINIMO_USD = 20000.0

    fun construir(cotizaciones: List<Cotizacion>): List<Oportunidad> {
        val porActivo = LinkedHashMap<String, MutableList<Cotizacion>>()
        for (c in cotizaciones) {
            if (c.volumen < VOLUMEN_MINIMO_USD) continue
            porActivo.getOrPut(c.base) { ArrayList() }.add(c)
        }
        return porActivo.mapNotNull { (base, lista) ->
            val tickers = lista.map {
                Ticker(it.exchange, "$base/${it.quote}", it.precio, it.volumen, "", it.urlOperar, anomalo = false, desactualizado = false)
            }
            val precios = tickers.map { it.precioUsd }.sorted()
            val moneda = Moneda(base.lowercase(), base, base, precios[precios.size / 2], 0)
            Comparador.construir(moneda, tickers)
        }
    }
}

/** Lee los precios de cada exchange (uno por uno, con una sola llamada cada uno) y arma las oportunidades. */
class BuscadorExchanges(
    private val pedir: (String) -> ResultadoPedido,
    private val fuentes: List<Fuente> = Exchanges.TODAS,
) {
    private val cancelado = AtomicBoolean(false)
    fun cancelar() = cancelado.set(true)

    /** Lo que sale de una lectura: las oportunidades, qué exchanges fallaron y si se canceló. */
    class Resultado(val oportunidades: List<Oportunidad>, val leidos: Int, val fallaron: List<String>, val cancelado: Boolean)

    fun buscar(alEstado: (String) -> Unit): Resultado {
        cancelado.set(false)
        val todas = ArrayList<Cotizacion>()
        val fallaron = ArrayList<String>()
        var leidos = 0
        for ((i, f) in fuentes.withIndex()) {
            if (cancelado.get()) return Resultado(emptyList(), leidos, fallaron, true)
            alEstado("Leyendo ${f.nombre} (${i + 1}/${fuentes.size})…")
            val r = pedir(f.url)
            val cuerpo = r.cuerpo
            if (cuerpo == null) {
                fallaron.add("${f.nombre} (código ${r.codigo})")
                continue
            }
            val pares = f.parsear(cuerpo)
            if (pares.isEmpty()) fallaron.add("${f.nombre} (formato no reconocido)") else leidos++
            todas.addAll(pares)
        }
        if (leidos == 0) {
            alEstado("No se pudo leer ningún exchange. ¿Estás sin internet?")
            return Resultado(emptyList(), 0, fallaron, false)
        }
        alEstado("Armando comparaciones…")
        return Resultado(Agregador.construir(todas), leidos, fallaron, false)
    }
}
