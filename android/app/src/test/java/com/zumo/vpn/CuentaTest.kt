package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CuentaTest {
    @Test
    fun etiqueta_con_nombre_y_dia_mes() {
        assertEquals("Adrián [09/11]", Cuenta.etiqueta("Adrián", "2026-11-09"))
        assertEquals("Adrián", Cuenta.etiqueta("Adrián", ""))
        assertEquals("[09/11]", Cuenta.etiqueta("", "2026-11-09"))
        assertEquals("", Cuenta.etiqueta("", ""))
        assertEquals("Ana", Cuenta.etiqueta(" Ana ", "mañana"))
    }

    @Test
    fun lee_la_respuesta_del_bot() {
        val d = Cuenta.parsear("Adrián\n2026-11-09\n")!!
        assertEquals("Adrián", d.nombre); assertEquals("2026-11-09", d.vence)
        assertEquals("", Cuenta.parsear("Ana\n\n")!!.vence)
        assertEquals("", Cuenta.parsear("Ana\nbasura\n")!!.vence)
        assertNull(Cuenta.parsear(""))
        assertNull(Cuenta.parsear("\n\n"))
    }

    @Test
    fun pregunta_en_la_vps_y_no_en_github() {
        val u = Cuenta.urls("https://vps.ejemplo.com/servidores.bin|https://raw.githubusercontent.com/x/Zumo/apk/servidores.bin", "b9da1a72f68f59b8")
        assertEquals(listOf("https://vps.ejemplo.com/cuenta?t=b9da1a72f68f59b8"), u)
        assertEquals(emptyList<String>(), Cuenta.urls("", "abc"))
        assertEquals(emptyList<String>(), Cuenta.urls("https://raw.githubusercontent.com/x/Zumo/apk/servidores.bin", "abc"))
    }
}
