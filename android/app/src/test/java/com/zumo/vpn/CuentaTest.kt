package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CuentaTest {
    /** Un "ahora" fijo: 09/10/2026 a las 10:00 (hora local del equipo que corre la prueba). */
    private val hoy: Long = java.util.Calendar.getInstance().apply { clear(); set(2026, 9, 9, 10, 0, 0) }.timeInMillis

    @Test
    fun etiqueta_con_nombre_dia_mes_y_dias_que_faltan() {
        assertEquals("Adrián [09/11] · 31 d", Cuenta.etiqueta("Adrián", "2026-11-09", hoy))
        assertEquals("Adrián [16/10] · 7 d", Cuenta.etiqueta("Adrián", "2026-10-16", hoy))
        assertEquals("Adrián [09/10] · vence hoy", Cuenta.etiqueta("Adrián", "2026-10-09", hoy))
        assertEquals("Adrián [05/10] · vencida", Cuenta.etiqueta("Adrián", "2026-10-05", hoy))
        assertEquals("[09/11] · 31 d", Cuenta.etiqueta("", "2026-11-09", hoy))
    }

    @Test
    fun etiqueta_sin_fecha_es_solo_el_nombre() {
        assertEquals("Adrián", Cuenta.etiqueta("Adrián", "", hoy))
        assertEquals("", Cuenta.etiqueta("", "", hoy))
        assertEquals("Ana", Cuenta.etiqueta(" Ana ", "mañana", hoy))
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
