package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Test

class DiagnosticoTest {
    private fun tipo(error: String, con: Boolean = false, ando: Boolean = false) =
        Diagnostico.de(con, ando, "Abriendo túnel", error).tipo

    @Test
    fun conectado_no_muestra_error() {
        assertEquals(Diagnostico.Tipo.BIEN, tipo("", con = true))
        assertEquals(Diagnostico.Tipo.BIEN, tipo("viejo error", con = true))
    }

    @Test
    fun conectando_muestra_el_paso() {
        val a = Diagnostico.de(false, true, "Abriendo túnel", "")
        assertEquals(Diagnostico.Tipo.EN_CURSO, a.tipo)
        assertEquals("Abriendo túnel", a.porque)
    }

    @Test
    fun explica_cada_error_conocido() {
        assertEquals(Diagnostico.Tipo.TOKEN, tipo("Token expirado. Pedí que te lo activen y volvé a intentar."))
        assertEquals(Diagnostico.Tipo.SERVIDOR, tipo("Error de servidor. Ningún servidor respondió; probá de nuevo en un rato."))
        assertEquals(Diagnostico.Tipo.INTERNET, tipo("Conectando → No se encontró el servidor (revisá tu internet)"))
        assertEquals(Diagnostico.Tipo.SERVIDOR, tipo("Tiempo agotado: el servidor no respondió"))
        assertEquals(Diagnostico.Tipo.SERVIDOR, tipo("No se pudo conectar (puerto cerrado o bloqueado)"))
        assertEquals(Diagnostico.Tipo.OTRO, tipo("No se pudo iniciar la VPN: x"))
        assertEquals("cosa rara", Diagnostico.de(false, false, "", "cosa rara").porque)
    }
}
