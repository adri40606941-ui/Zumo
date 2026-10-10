package com.zumo.cripto

import java.util.concurrent.atomic.AtomicBoolean

/** Junta los pares de todos los exchanges por activo y arma las oportunidades (un activo en al menos dos exchanges). Función pura. */
object Agregador {
    /** Un par con poco volumen tiene un libro vacío y da diferencias de precio falsas: se ignora. */
    const val VOLUMEN_MINIMO_USD = 20000.0

    /**
     * Dos precios del mismo símbolo que se alejan más que esto (50 %) no se consideran la misma cripto: es casi siempre
     * otra moneda con el mismo símbolo (por ejemplo dos "RAIN"), no una oportunidad.
     */
    const val SALTO_MAXIMO = 1.5

    /**
     * Separa las cotizaciones de un símbolo en grupos de precios parecidos y se queda con el grupo que tiene más exchanges
     * (si empatan, el de más volumen). Los que quedan afuera son otra moneda con el mismo símbolo.
     */
    internal fun mismoActivo(lista: List<Cotizacion>): List<Cotizacion> {
        val ordenadas = lista.sortedBy { it.precio }
        val grupos = ArrayList<MutableList<Cotizacion>>()
        for (c in ordenadas) {
            val g = grupos.lastOrNull()
            if (g != null && c.precio / g.last().precio <= SALTO_MAXIMO) g.add(c) else grupos.add(mutableListOf(c))
        }
        return grupos.maxWithOrNull(
            compareBy<List<Cotizacion>>({ g -> g.map { it.exchange }.toSet().size }, { g -> g.sumOf { it.volumen } })
        ) ?: emptyList()
    }

    fun construir(cotizaciones: List<Cotizacion>): List<Oportunidad> {
        val porActivo = LinkedHashMap<String, MutableList<Cotizacion>>()
        for (c in cotizaciones) {
            if (c.volumen < VOLUMEN_MINIMO_USD) continue
            porActivo.getOrPut(c.base) { ArrayList() }.add(c)
        }
        return porActivo.mapNotNull { (base, todas) ->
            val lista = mismoActivo(todas)
            val tickers = lista.map {
                Ticker(it.exchange, "$base/${it.quote}", it.precio, it.volumen, "", it.urlOperar, anomalo = false, desactualizado = false)
            }
            if (tickers.isEmpty()) return@mapNotNull null
            val precios = tickers.map { it.precioUsd }.sorted()
            val moneda = Moneda(base.lowercase(), base, base, precios[precios.size / 2], 0)
            Comparador.construir(moneda, tickers)
        }
    }
}

/** Lee los precios de cada exchange, después qué redes permite cada uno para enviar (público y, si hay clave, con la cuenta), y arma las oportunidades. */
class BuscadorExchanges(
    private val pedir: (String) -> ResultadoPedido,
    private val fuentes: List<Fuente> = Exchanges.TODAS,
    private val envios: List<FuenteTransferencias> = Transferencias.FUENTES,
    private val privadas: List<FuentePrivada> = emptyList(),
    private val credenciales: () -> Map<String, Credencial> = { emptyMap() },
    private val pedirFirmado: (PedidoFirmado) -> ResultadoPedido = { ResultadoPedido(0, null) },
    private val ahora: () -> Long = { System.currentTimeMillis() },
) {
    private val cancelado = AtomicBoolean(false)
    fun cancelar() = cancelado.set(true)

    /** Lo que sale de una lectura: oportunidades, exchanges que fallaron, si se canceló y lo que se sabe de envíos. */
    class Resultado(
        val oportunidades: List<Oportunidad>,
        val leidos: Int,
        val fallaron: List<String>,
        val cancelado: Boolean,
        val catalogo: Catalogo,
        /** Exchanges cuyos envíos se leyeron con la cuenta del usuario (clave API). */
        val conCuenta: List<String> = emptyList(),
    )

    fun buscar(alEstado: (String) -> Unit): Resultado {
        cancelado.set(false)
        val todas = ArrayList<Cotizacion>()
        val fallaron = ArrayList<String>()
        var leidos = 0
        for ((i, f) in fuentes.withIndex()) {
            if (cancelado.get()) return Resultado(emptyList(), leidos, fallaron, true, Catalogo(emptyMap()))
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

        val datosEnvio = HashMap<String, Map<String, List<RedDe>>>()
        for ((i, f) in envios.withIndex()) {
            if (cancelado.get()) return Resultado(emptyList(), leidos, fallaron, true, Catalogo(datosEnvio))
            alEstado("Leyendo envíos de ${f.nombre} (${i + 1}/${envios.size})…")
            val r = pedir(f.url)
            val cuerpo = r.cuerpo
            if (cuerpo == null) { fallaron.add("envíos de ${f.nombre} (código ${r.codigo})"); continue }
            val d = f.parsear(cuerpo)
            if (d.isEmpty()) fallaron.add("envíos de ${f.nombre} (formato no reconocido)") else datosEnvio[f.nombre] = d
        }

        // Con la clave del usuario se leen los envíos reales; reemplazan a lo público del mismo exchange.
        val conCuenta = ArrayList<String>()
        val claves = if (privadas.isEmpty()) emptyMap() else credenciales()
        for (f in privadas) {
            val c = claves[f.nombre] ?: continue
            if (cancelado.get()) return Resultado(emptyList(), leidos, fallaron, true, Catalogo(datosEnvio), conCuenta)
            alEstado("Leyendo envíos de ${f.nombre} con tu cuenta…")
            val r = pedirFirmado(f.pedido(c, ahora()))
            val cuerpo = r.cuerpo
            if (cuerpo == null) { fallaron.add("tu cuenta de ${f.nombre} (código ${r.codigo})"); continue }
            val d = f.parsear(cuerpo)
            if (d.isEmpty()) fallaron.add("tu cuenta de ${f.nombre} (formato no reconocido)") else { datosEnvio[f.nombre] = d; conCuenta.add(f.nombre) }
        }
        val catalogo = Catalogo(datosEnvio)

        if (leidos == 0) {
            alEstado("No se pudo leer ningún exchange. ¿Estás sin internet?")
            return Resultado(emptyList(), 0, fallaron, false, catalogo, conCuenta)
        }
        alEstado("Armando comparaciones…")
        return Resultado(Agregador.construir(todas), leidos, fallaron, false, catalogo, conCuenta)
    }
}
