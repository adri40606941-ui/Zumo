package com.zumo.cripto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger

class ParseoTest {
    private val MERCADOS = """
        [
          {"id":"bitcoin","symbol":"btc","name":"Bitcoin","current_price":67000.5,"market_cap_rank":1},
          {"id":"ethereum","symbol":"eth","name":"Ethereum","current_price":3200.0,"market_cap_rank":2},
          {"id":"sin-precio","symbol":"xx","name":"Sin precio","current_price":null,"market_cap_rank":3}
        ]
    """.trimIndent()

    @Test fun lee_la_lista_de_monedas() {
        val m = Parseo.monedas(MERCADOS)
        assertEquals(2, m.size)
        assertEquals(Moneda("bitcoin", "BTC", "Bitcoin", 67000.5, 1), m[0])
        assertEquals("ETH", m[1].simbolo)
    }

    @Test fun json_roto_da_lista_vacia() {
        assertTrue(Parseo.monedas("no es json").isEmpty())
        assertTrue(Parseo.tickers("{incompleto").isEmpty())
    }

    private val TICKERS = """
        {"name":"Bitcoin","tickers":[
          {"base":"BTC","target":"USDT","market":{"name":"Binance","identifier":"binance"},
           "converted_last":{"usd":67000.0},"converted_volume":{"usd":5000000},"trust_score":"green",
           "trade_url":"https://binance.com/btc","is_anomaly":false,"is_stale":false},
          {"base":"BTC","target":"USD","market":{"name":"Coinbase","identifier":"gdax"},
           "converted_last":{"usd":67450.0},"converted_volume":{"usd":3000000},"trust_score":"green",
           "trade_url":"https://coinbase.com/btc","is_anomaly":false,"is_stale":false},
          {"base":"BTC","target":"ARS","market":{"name":"Rarísimo","identifier":"rarisimo"},
           "converted_last":{"usd":90000.0},"converted_volume":{"usd":10},"trust_score":"red",
           "trade_url":"","is_anomaly":false,"is_stale":false},
          {"base":"BTC","target":"USDT","market":{"name":"Fantasma","identifier":"fantasma"},
           "converted_last":{"usd":1.0},"converted_volume":{"usd":100},"trust_score":"green",
           "trade_url":"","is_anomaly":true,"is_stale":false},
          {"base":"BTC","target":"USDT","market":{"name":"Viejo","identifier":"viejo"},
           "converted_last":{"usd":99999.0},"converted_volume":{"usd":100},"trust_score":"green",
           "trade_url":"","is_anomaly":false,"is_stale":true},
          {"base":"BTC","target":"EUR","market":{"name":"SinConvertir"},
           "converted_last":{},"converted_volume":{},"trust_score":"green",
           "trade_url":"","is_anomaly":false,"is_stale":false}
        ]}
    """.trimIndent()

    @Test fun lee_los_tickers_crudos() {
        // Parseo solo entiende el JSON tal cual viene: filtrar anomalías, desactualizados o baja confianza es trabajo de Comparador.
        // Acá solo se descarta lo que no se puede ni leer: sin exchange, o sin precio convertido a usd (SinConvertir).
        val t = Parseo.tickers(TICKERS)
        assertEquals(setOf("Binance", "Coinbase", "Rarísimo", "Fantasma", "Viejo"), t.map { it.exchange }.toSet())
        val binance = t.first { it.exchange == "Binance" }
        assertEquals(67000.0, binance.precioUsd, 0.0001)
        assertEquals("green", binance.confianza)
        assertEquals("BTC/USDT", binance.par)
        assertEquals("https://binance.com/btc", binance.urlOperar)
        assertFalse(binance.anomalo); assertFalse(binance.desactualizado)
        assertTrue(t.first { it.exchange == "Fantasma" }.anomalo)
        assertTrue(t.first { it.exchange == "Viejo" }.desactualizado)
        assertEquals("red", t.first { it.exchange == "Rarísimo" }.confianza)
    }
}

class ComparadorTest {
    private fun t(exch: String, precio: Double, vol: Double = 100.0, confianza: String = "green", anomalo: Boolean = false, stale: Boolean = false) =
        Ticker(exch, "BTC/USDT", precio, vol, confianza, "https://$exch", anomalo, stale)

    private val BTC = Moneda("bitcoin", "BTC", "Bitcoin", 67000.0, 1)

    @Test fun arma_la_oportunidad_ordenada_por_precio() {
        val o = Comparador.construir(BTC, listOf(t("B", 67500.0), t("A", 67000.0), t("C", 68000.0)))!!
        assertEquals(listOf("A", "B", "C"), o.tickers.map { it.exchange })
        assertEquals("A", o.barato.exchange); assertEquals("C", o.caro.exchange)
    }

    @Test fun calcula_el_margen_porcentual() {
        val o = Comparador.construir(BTC, listOf(t("A", 100.0), t("B", 110.0)))!!
        assertEquals(10.0, o.margenPct, 0.0001)
    }

    @Test fun un_solo_exchange_no_alcanza_para_comparar() {
        assertNull(Comparador.construir(BTC, listOf(t("A", 100.0))))
        assertNull(Comparador.construir(BTC, emptyList()))
    }

    @Test fun descarta_anomalos_desactualizados_y_de_baja_confianza() {
        val o = Comparador.construir(BTC, listOf(
            t("A", 100.0), t("B", 110.0),
            t("Falso", 1.0, anomalo = true), t("Viejo", 500.0, stale = true), t("Dudoso", 9.0, confianza = "red"),
        ))!!
        assertEquals(setOf("A", "B"), o.tickers.map { it.exchange }.toSet())
    }

    @Test fun mismo_exchange_repetido_se_queda_con_el_de_mas_volumen() {
        val o = Comparador.construir(BTC, listOf(t("A", 100.0, vol = 10.0), t("A", 101.0, vol = 999.0), t("B", 105.0)))!!
        assertEquals(2, o.exchanges)
        assertEquals(101.0, o.tickers.first { it.exchange == "A" }.precioUsd, 0.0001)
    }

    @Test fun precio_cero_o_negativo_se_descarta() {
        assertNull(Comparador.construir(BTC, listOf(t("A", 0.0), t("B", -5.0))))
    }
}

class EscaneoTest {
    private fun mercados(vararg ids: String) =
        "[" + ids.joinToString(",") { """{"id":"$it","symbol":"$it","name":"$it","current_price":100,"market_cap_rank":1}""" } + "]"

    private fun tickers(vararg precios: Double) =
        """{"tickers":[""" + precios.mapIndexed { i, p ->
            """{"base":"X","target":"USDT","market":{"name":"ex$i"},"converted_last":{"usd":$p},"converted_volume":{"usd":100},"trust_score":"green","trade_url":"","is_anomaly":false,"is_stale":false}"""
        }.joinToString(",") + "]}"

    @Test fun recorre_las_monedas_y_avisa_las_que_superan_el_minimo() {
        val respuestas = mapOf(
            CoinGeckoApi.urlMercados(2) to ResultadoPedido(200, mercados("a", "b")),
            CoinGeckoApi.urlTickers("a") to ResultadoPedido(200, tickers(100.0, 110.0)),   // 10 %
            CoinGeckoApi.urlTickers("b") to ResultadoPedido(200, tickers(100.0, 101.0)),   // 1 %
        )
        val hallados = CopyOnWriteArrayList<Oportunidad>()
        val estados = CopyOnWriteArrayList<String>()
        Escaneo({ url -> respuestas[url] ?: ResultadoPedido(404, null) }, dormir = {})
            .buscar(2, 5.0, 0L, { estados.add(it) }, { hallados.add(it) }, { _, _ -> })
        assertEquals(listOf("a"), hallados.map { it.moneda.id })
        assertTrue(estados.any { it.contains("Consultando") })
    }

    @Test fun reintenta_con_espera_si_llega_un_429_y_despues_sigue() {
        var pedidosTickersA = 0
        val respuestas = mutableMapOf(CoinGeckoApi.urlMercados(1) to ResultadoPedido(200, mercados("a")))
        val dormidas = CopyOnWriteArrayList<Long>()
        val hallados = CopyOnWriteArrayList<Oportunidad>()
        Escaneo(
            pedir = { url ->
                if (url == CoinGeckoApi.urlTickers("a")) {
                    pedidosTickersA++
                    if (pedidosTickersA < 3) ResultadoPedido(429, null) else ResultadoPedido(200, tickers(100.0, 120.0))
                } else respuestas[url] ?: ResultadoPedido(404, null)
            },
            dormir = { dormidas.add(it) },
        ).buscar(1, 1.0, 0L, {}, { hallados.add(it) }, { _, _ -> })
        assertEquals(3, pedidosTickersA)
        assertEquals(1, hallados.size)
        assertTrue("se esperó cada vez más", dormidas.size >= 2 && dormidas[0] < dormidas[1])
    }

    @Test fun se_rinde_tras_los_reintentos_y_sigue_con_la_siguiente_moneda() {
        val respuestas = mapOf(
            CoinGeckoApi.urlMercados(2) to ResultadoPedido(200, mercados("a", "b")),
            CoinGeckoApi.urlTickers("a") to ResultadoPedido(429, null),
            CoinGeckoApi.urlTickers("b") to ResultadoPedido(200, tickers(100.0, 120.0)),
        )
        val hallados = CopyOnWriteArrayList<Oportunidad>()
        val avances = AtomicInteger(0)
        Escaneo({ respuestas[it] ?: ResultadoPedido(404, null) }, dormir = {})
            .buscar(2, 1.0, 0L, {}, { hallados.add(it) }, { _, _ -> avances.incrementAndGet() })
        assertEquals(listOf("b"), hallados.map { it.moneda.id })
        assertEquals(2, avances.get())
    }

    @Test fun sin_conexion_al_pedir_el_ranking_avisa_y_no_sigue() {
        val estados = CopyOnWriteArrayList<String>()
        var llamadasTickers = 0
        Escaneo(
            pedir = { url -> if (url.contains("/tickers")) { llamadasTickers++; ResultadoPedido(200, tickers(1.0, 2.0)) } else ResultadoPedido(0, null) },
            dormir = {},
        ).buscar(5, 1.0, 0L, { estados.add(it) }, {}, { _, _ -> })
        assertEquals(0, llamadasTickers)
        assertTrue(estados.any { it.contains("No se pudo consultar") })
    }

    @Test fun busca_una_sola_cripto_por_simbolo_sin_importar_mayusculas() {
        val respuestas = mapOf(
            CoinGeckoApi.urlMercados(250) to ResultadoPedido(200, mercados("a", "b")),
            CoinGeckoApi.urlTickers("b") to ResultadoPedido(200, tickers(100.0, 103.0)),
        )
        val estados = CopyOnWriteArrayList<String>()
        val op = Escaneo({ respuestas[it] ?: ResultadoPedido(404, null) }, dormir = {})
            .buscarUna("B", { estados.add(it) })
        assertEquals("b", op!!.moneda.id)
        assertEquals(3.0, op.margenPct, 0.0001)
    }

    @Test fun busqueda_individual_sin_coincidencia_avisa_y_devuelve_null() {
        val respuestas = mapOf(CoinGeckoApi.urlMercados(250) to ResultadoPedido(200, mercados("a")))
        val estados = CopyOnWriteArrayList<String>()
        val op = Escaneo({ respuestas[it] ?: ResultadoPedido(404, null) }, dormir = {})
            .buscarUna("zzz", { estados.add(it) })
        assertNull(op)
        assertTrue(estados.any { it.contains("No encontré") })
    }

    @Test fun busqueda_individual_vacia_no_pide_nada() {
        var pedidos = 0
        val op = Escaneo({ pedidos++; ResultadoPedido(200, "[]") }, dormir = {}).buscarUna("   ", {})
        assertNull(op)
        assertEquals(0, pedidos)
    }

    @Test fun cancelar_corta_entre_una_moneda_y_otra() {
        val ids = (1..50).map { "m$it" }.toTypedArray()
        val respuestas = mutableMapOf(CoinGeckoApi.urlMercados(50) to ResultadoPedido(200, mercados(*ids)))
        for (id in ids) respuestas[CoinGeckoApi.urlTickers(id)] = ResultadoPedido(200, tickers(100.0, 120.0))
        val e = Escaneo({ respuestas[it] ?: ResultadoPedido(404, null) }, dormir = {})
        val hallados = CopyOnWriteArrayList<Oportunidad>()
        val t = Thread { e.buscar(50, 1.0, 50L, {}, { hallados.add(it); if (hallados.size == 3) e.cancelar() }, { _, _ -> }) }
        t.start(); t.join(10000)
        assertFalse(t.isAlive)
        assertTrue("se cortó antes de terminar las 50", hallados.size < 50)
    }
}

class CalculadoraTest {
    @Test fun sin_comisiones_el_neto_es_el_margen_bruto() {
        assertEquals(10.0, Calculadora.netoPct(100.0, 110.0, 0.0, 0.0), 0.0001)
    }

    @Test fun con_comision_de_0_1_por_operacion_baja_el_neto() {
        // compra: 100 * 1.001 = 100.1 ; venta: 110 * 0.999 = 109.89 ; neto = (109.89 - 100.1) / 100.1
        assertEquals(9.7803, Calculadora.netoPct(100.0, 110.0, 0.1, 0.1), 0.001)
    }

    @Test fun una_comision_grande_puede_anular_el_margen() {
        assertTrue(Calculadora.netoPct(100.0, 101.0, 0.5, 0.5) < 0)
    }

    @Test fun precio_barato_cero_da_cero() {
        assertEquals(0.0, Calculadora.netoPct(0.0, 110.0, 0.1, 0.1), 0.0001)
    }
}

class PausaAdaptativaTest {
    private val e = Escaneo({ ResultadoPedido(0, null) }, dormir = {})

    @Test fun una_respuesta_buena_acorta_la_pausa_hasta_el_minimo() {
        assertEquals(900L, e.ajustarPausa(1000L, 200))
        assertEquals(Escaneo.PAUSA_MIN_MS, e.ajustarPausa(Escaneo.PAUSA_MIN_MS, 200))
    }

    @Test fun un_429_alarga_la_pausa_y_no_pasa_del_maximo() {
        assertEquals(2500L, e.ajustarPausa(1000L, 429))
        assertEquals(Escaneo.PAUSA_MAX_MS, e.ajustarPausa(Escaneo.PAUSA_MAX_MS, 429))
    }

    @Test fun un_error_que_no_es_429_deja_la_pausa_como_estaba() {
        assertEquals(1000L, e.ajustarPausa(1000L, 500))
    }

    @Test fun el_escaneo_acorta_la_pausa_entre_monedas_si_todo_responde_bien() {
        val ids = (1..6).map { "m$it" }
        val respuestas = mutableMapOf(CoinGeckoApi.urlMercados(6) to ResultadoPedido(200,
            "[" + ids.joinToString(",") { """{"id":"$it","symbol":"$it","name":"$it","current_price":100,"market_cap_rank":1}""" } + "]"))
        for (id in ids) respuestas[CoinGeckoApi.urlTickers(id)] = ResultadoPedido(200,
            """{"tickers":[{"base":"X","target":"USDT","market":{"name":"a"},"converted_last":{"usd":100},"converted_volume":{"usd":1},"trust_score":"green","is_anomaly":false,"is_stale":false},{"base":"X","target":"USDT","market":{"name":"b"},"converted_last":{"usd":101},"converted_volume":{"usd":1},"trust_score":"green","is_anomaly":false,"is_stale":false}]}""")
        val pausas = CopyOnWriteArrayList<Long>()
        Escaneo({ respuestas[it] ?: ResultadoPedido(404, null) }, dormir = { pausas.add(it) })
            .buscar(6, 0.0, 2000L, {}, {}, { _, _ -> })
        // 5 esperas entre 6 monedas: cada una más corta que la anterior, sin bajar del mínimo
        assertEquals(5, pausas.size)
        assertTrue(pausas.zipWithNext().all { (a, b) -> b <= a })
        assertTrue(pausas.last() >= Escaneo.PAUSA_MIN_MS)
        assertTrue(pausas.last() < 2000L)
    }
}
