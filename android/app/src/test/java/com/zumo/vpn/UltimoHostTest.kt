package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class UltimoHostTest {
    private fun c(host: String) = Config(name = "APP", host = host)
    private val a = c("a.com"); private val b = c("b.com"); private val d = c("d.com")

    @Test
    fun el_ultimo_que_funciono_sale_primero_sin_esperar_a_los_demas() {
        val t0 = System.currentTimeMillis()
        val r = Hosts.ordenarConUltimo(listOf(a, b, d), "APP", "d.com") { x -> if (x.host == "d.com") true else { Thread.sleep(4000); false } }
        assertEquals(listOf("d.com", "a.com", "b.com"), r.map { it.host })
        assertTrue("no esperó a los lentos", System.currentTimeMillis() - t0 < 1500)
    }

    @Test
    fun si_el_ultimo_ya_no_contesta_se_ordena_como_siempre() {
        val r = Hosts.ordenarConUltimo(listOf(a, b, d), "APP", "d.com", 1500) { x -> x.host == "b.com" }
        assertEquals("b.com", r.first().host)
        assertEquals(3, r.size)
    }

    @Test
    fun sin_ultimo_guardado_o_con_un_solo_host_no_cambia_nada() {
        assertEquals(listOf("a.com"), Hosts.ordenarConUltimo(listOf(a), "APP", "a.com") { true }.map { it.host })
        val r = Hosts.ordenarConUltimo(listOf(a, b), "APP", "") { true }
        assertEquals(2, r.size)
    }
}
