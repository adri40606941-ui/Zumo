package com.zumo.cripto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

class ComparadorTest {
    private fun t(exch: String, precio: Double, vol: Double = 100.0, confianza: String = "green", anomalo: Boolean = false, stale: Boolean = false) =
        Ticker(exch, "BTC/USDT", precio, vol, confianza, "https://$exch", anomalo, stale)

    private val BTC = Moneda("bitcoin", "BTC", "Bitcoin", 67000.0, 0)

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

class SeparadorDeParesTest {
    @Test fun separa_pares_pegados() {
        assertEquals("BTC" to "USDT", Exchanges.separar("BTCUSDT"))
        assertEquals("ETH" to "USDC", Exchanges.separar("ethusdc"))
    }

    @Test fun separa_pares_con_guion_y_guion_bajo() {
        assertEquals("BTC" to "USDT", Exchanges.separar("BTC-USDT", "-"))
        assertEquals("SOL" to "USD", Exchanges.separar("SOL_USD", "_"))
    }

    @Test fun ignora_pares_que_no_son_contra_dolar() {
        assertNull(Exchanges.separar("BTCEUR"))
        assertNull(Exchanges.separar("ETHBTC"))
        assertNull(Exchanges.separar("BTC-EUR", "-"))
    }

    @Test fun ignora_stablecoins_como_base() {
        assertNull(Exchanges.separar("USDCUSDT"))
        assertNull(Exchanges.separar("USDT-USD", "-"))
    }
}

class ParseoExchangesTest {
    private fun unico(f: Fuente, json: String): Cotizacion {
        val r = f.parsear(json)
        assertEquals(1, r.size)
        return r[0]
    }

    @Test fun binance_lee_symbol_lastPrice_y_quoteVolume() {
        val c = unico(Exchanges.BINANCE, """[
          {"symbol":"BTCUSDT","lastPrice":"67000.5","quoteVolume":"5000000"},
          {"symbol":"ETHBTC","lastPrice":"0.05","quoteVolume":"900000"},
          {"symbol":"USDCUSDT","lastPrice":"1.0","quoteVolume":"9000000"}]""")
        assertEquals("BTC", c.base); assertEquals("USDT", c.quote)
        assertEquals(67000.5, c.precio, 0.0001); assertEquals(5000000.0, c.volumen, 0.0001)
    }

    @Test fun bybit_lee_la_lista_dentro_de_result() {
        val c = unico(Exchanges.BYBIT, """{"retCode":0,"result":{"category":"spot","list":[
          {"symbol":"BTCUSDT","lastPrice":"67600","turnover24h":"600000"}]}}""")
        assertEquals("BTC", c.base); assertEquals(67600.0, c.precio, 0.0001)
    }

    @Test fun okx_separa_con_guion_y_usa_volCcy24h() {
        val c = unico(Exchanges.OKX, """{"code":"0","data":[
          {"instId":"BTC-USDT","last":"67100","volCcy24h":"3000000"},
          {"instId":"BTC-EUR","last":"60000","volCcy24h":"10"}]}""")
        assertEquals("USDT", c.quote); assertEquals(67100.0, c.precio, 0.0001)
    }

    @Test fun kucoin_lee_data_ticker_con_volValue() {
        val c = unico(Exchanges.KUCOIN, """{"code":"200000","data":{"time":1,"ticker":[
          {"symbol":"BTC-USDT","last":"67200","volValue":"2000000"}]}}""")
        assertEquals(2000000.0, c.volumen, 0.0001)
    }

    @Test fun gate_lee_currency_pair_con_guion_bajo() {
        val c = unico(Exchanges.GATE, """[{"currency_pair":"BTC_USDT","last":"67300","quote_volume":"1000000"}]""")
        assertEquals("BTC", c.base); assertEquals(67300.0, c.precio, 0.0001)
    }

    @Test fun bitget_lee_data_con_lastPr_y_quoteVolume() {
        val c = unico(Exchanges.BITGET, """{"code":"00000","data":[{"symbol":"BTCUSDT","lastPr":"67400","quoteVolume":"800000"}]}""")
        assertEquals(67400.0, c.precio, 0.0001)
    }

    @Test fun mexc_lee_como_binance() {
        val c = unico(Exchanges.MEXC, """[{"symbol":"BTCUSDT","lastPrice":"67500","quoteVolume":"700000"}]""")
        assertEquals(67500.0, c.precio, 0.0001)
    }

    @Test fun json_que_no_es_el_esperado_da_lista_vacia_sin_romper() {
        assertTrue(Exchanges.BYBIT.parsear("""{"error":"bloqueado"}""").isEmpty())
        assertTrue(Exchanges.BINANCE.parsear("no es json").isEmpty())
    }

    @Test fun precio_o_volumen_invalidos_se_descartan() {
        assertTrue(Exchanges.MEXC.parsear("""[{"symbol":"BTCUSDT","lastPrice":"0","quoteVolume":"1"}]""").isEmpty())
        assertTrue(Exchanges.MEXC.parsear("""[{"symbol":"BTCUSDT","lastPrice":"5","quoteVolume":"abc"}]""").isEmpty())
    }
}

class AgregadorTest {
    private fun c(ex: String, base: String, precio: Double, vol: Double = 50000.0) =
        Cotizacion(ex, base, "USDT", precio, vol, "https://$ex")

    @Test fun junta_el_mismo_activo_de_varios_exchanges_y_ordena_por_precio() {
        val ops = Agregador.construir(listOf(c("A", "BTC", 102.0), c("B", "BTC", 99.0), c("C", "BTC", 100.0)))
        assertEquals(1, ops.size)
        val op = ops[0]
        assertEquals("B", op.barato.exchange); assertEquals("A", op.caro.exchange)
        assertEquals((102.0 - 99.0) / 99.0 * 100.0, op.margenPct, 0.0001)
    }

    @Test fun un_activo_en_un_solo_exchange_no_es_oportunidad() {
        assertTrue(Agregador.construir(listOf(c("A", "ETH", 3000.0))).isEmpty())
    }

    @Test fun ignora_pares_con_poco_volumen_aunque_sean_los_mas_baratos() {
        val ops = Agregador.construir(listOf(
            c("A", "BTC", 100.0), c("B", "BTC", 101.0),
            c("Libro vacío", "BTC", 1.0, vol = 500.0),
        ))
        assertEquals(setOf("A", "B"), ops[0].tickers.map { it.exchange }.toSet())
    }

    @Test fun usa_la_mediana_como_precio_de_referencia() {
        val op = Agregador.construir(listOf(c("A", "BTC", 100.0), c("B", "BTC", 200.0), c("C", "BTC", 110.0)))[0]
        assertEquals(110.0, op.moneda.precioUsd, 0.0001)
    }
}

class BuscadorExchangesTest {
    private val muestra = """[{"symbol":"BTCUSDT","lastPrice":"100","quoteVolume":"50000"}]"""

    @Test fun lee_todos_los_exchanges_y_salta_los_que_fallan() {
        val fA = Fuente("A", "u://a") { listOf(Cotizacion("A", "BTC", "USDT", 100.0, 50000.0, "")) }
        val fB = Fuente("B", "u://b") { listOf() }
        val fC = Fuente("C", "u://c") { listOf(Cotizacion("C", "BTC", "USDT", 105.0, 50000.0, "")) }
        val pedidos = CopyOnWriteArrayList<String>()
        val r = BuscadorExchanges({ url ->
            pedidos.add(url)
            if (url == "u://b") ResultadoPedido(451, null) else ResultadoPedido(200, muestra)
        }, listOf(fA, fB, fC), emptyList()).buscar {}

        assertEquals(listOf("u://a", "u://b", "u://c"), pedidos.toList())
        assertEquals(2, r.leidos)
        assertEquals(1, r.oportunidades.size)
        assertEquals(1, r.fallaron.size)
        assertTrue(r.fallaron[0].contains("B") && r.fallaron[0].contains("451"))
        assertFalse(r.cancelado)
    }

    @Test fun si_ningun_exchange_responde_avisa_sin_oportunidades() {
        val estados = CopyOnWriteArrayList<String>()
        val r = BuscadorExchanges({ ResultadoPedido(0, null) }, listOf(Fuente("A", "u://a") { emptyList() }), emptyList()).buscar { estados.add(it) }
        assertTrue(r.oportunidades.isEmpty())
        assertEquals(0, r.leidos)
        assertTrue(estados.any { it.contains("No se pudo leer ningún exchange") })
    }

    @Test fun cancelar_corta_entre_exchanges() {
        val pedidos = CopyOnWriteArrayList<String>()
        val holder = arrayOfNulls<BuscadorExchanges>(1)
        val buscador = BuscadorExchanges({ url ->
            pedidos.add(url)
            holder[0]!!.cancelar()
            ResultadoPedido(200, muestra)
        }, listOf(Fuente("A", "u://a") { emptyList() }, Fuente("B", "u://b") { emptyList() }), emptyList())
        holder[0] = buscador
        val r = buscador.buscar {}
        assertTrue(r.cancelado)
        assertEquals(1, pedidos.size)
    }
}

class TransferenciasTest {
    private val kucoinUsdt = """{"code":"200000","data":[{"currency":"USDT","chains":[
      {"chainName":"ERC20","isWithdrawEnabled":true,"isDepositEnabled":true,"withdrawalMinFee":"2","memoRegex":""},
      {"chainName":"TRC20","isWithdrawEnabled":true,"isDepositEnabled":true,"withdrawalMinFee":"1.5","memoRegex":""}]}]}"""
    private val gateUsdt = """[{"currency":"USDT","delisted":false,"chains":[
      {"name":"ETH","withdraw_disabled":false,"deposit_disabled":false},
      {"name":"TRX","withdraw_disabled":false,"deposit_disabled":true}]},
      {"currency":"VIEJA","delisted":true,"chains":[{"name":"ETH","withdraw_disabled":false,"deposit_disabled":false}]}]"""
    private val bitgetUsdt = """{"code":"00000","data":[{"coin":"USDT","chains":[
      {"chain":"ERC20","withdrawable":"true","rechargeable":"true","withdrawFee":"2","needTag":"false"},
      {"chain":"TRC20","withdrawable":"true","rechargeable":"true","withdrawFee":"1.5","needTag":"true"}]}]}"""

    @Test fun nombres_de_red_distintos_son_la_misma_red() {
        assertEquals("ETH", Redes.canonica("ERC20"))
        assertEquals("ARB", Redes.canonica("ARBEVM"))
        assertEquals("AVAX", Redes.canonica("AVAX_C"))
        assertEquals("TRX", Redes.canonica("TRC20"))
    }

    @Test fun kucoin_lee_redes_con_comision_y_flags() {
        val d = Transferencias.kucoin(kucoinUsdt)["USDT"]!!
        assertEquals(2, d.size)
        assertEquals(RedDe("ETH", true, true, 2.0, false), d[0])
    }

    @Test fun gate_salta_los_deslistados_y_lee_los_flags_invertidos() {
        val d = Transferencias.gate(gateUsdt)
        assertEquals(setOf("USDT"), d.keys)
        assertFalse(d["USDT"]!![1].puedeDepositar)
        assertNull(d["USDT"]!![0].comision)
    }

    @Test fun bitget_lee_el_tag_requerido() {
        val d = Transferencias.bitget(bitgetUsdt)["USDT"]!!
        assertTrue(d[1].requiereTag)
        assertEquals(1.5, d[1].comision!!, 0.0001)
    }

    private fun catalogo(hastaKucoin: String = kucoinUsdt) = Catalogo(mapOf(
        "Bitget" to Transferencias.bitget(bitgetUsdt),
        "KuCoin" to Transferencias.kucoin(hastaKucoin),
    ))

    @Test fun elige_la_red_en_comun_mas_barata() {
        // ERC20 cuesta 2 y TRC20 cuesta 1.5: las dos están en común, así que gana TRC20
        val e = catalogo().estado("USDT", "Bitget", "KuCoin")
        assertTrue(e is Catalogo.Estado.Posible)
        e as Catalogo.Estado.Posible
        assertEquals("TRX", e.red)
        assertEquals(1.5, e.comision!!, 0.0001)
        assertTrue(e.requiereTag)
    }

    @Test fun no_es_posible_si_el_destino_no_recibe_en_ninguna_red_en_comun() {
        val soloTrxSinDeposito = """{"code":"200000","data":[{"currency":"USDT","chains":[
          {"chainName":"TRC20","isWithdrawEnabled":true,"isDepositEnabled":false,"withdrawalMinFee":"1.5","memoRegex":""}]}]}"""
        val e = catalogo(soloTrxSinDeposito).estado("USDT", "Bitget", "KuCoin")
        assertTrue(e is Catalogo.Estado.NoPosible)
    }

    @Test fun sin_datos_de_un_exchange_es_no_verificable() {
        val e = catalogo().estado("USDT", "Bitget", "Binance")
        assertTrue(e is Catalogo.Estado.NoVerificable)
        assertTrue((e as Catalogo.Estado.NoVerificable).motivo.contains("Binance"))
    }

    @Test fun activo_que_no_figura_en_el_exchange_origen_no_es_posible() {
        assertTrue(catalogo().estado("ETH", "Bitget", "KuCoin") is Catalogo.Estado.NoPosible)
    }
}
