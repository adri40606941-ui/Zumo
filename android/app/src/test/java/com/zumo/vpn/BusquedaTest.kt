package com.zumo.vpn

import com.zumo.vpn.Busqueda.Paso
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** El cliente no elige servidor: la app prueba el token en cada uno hasta que alguno lo conoce. */
class BusquedaTest {
    private fun lista(n: Int) = (1..n).map { Config(name = "S$it", host = "s$it.ejemplo.com", sshPort = 80) }

    @Test
    fun candidatos_empieza_por_el_ultimo_donde_entro() {
        val l = lista(4)
        assertEquals(listOf("S3", "S1", "S2", "S4"), Servidores.candidatos(l, "S3", null).map { it.name })
        assertEquals(listOf("S1", "S2", "S3", "S4"), Servidores.candidatos(l, "", null).map { it.name })
        assertEquals(listOf("S1", "S2", "S3", "S4"), Servidores.candidatos(l, "ya-no-existe", null).map { it.name })
    }

    @Test
    fun candidatos_descarta_servidores_invalidos_y_sin_lista_usa_la_cuenta_guardada() {
        val l = lista(2) + Config(name = "roto", host = "", sshPort = 80)
        assertEquals(listOf("S1", "S2"), Servidores.candidatos(l, "", null).map { it.name })
        val guardada = Config(name = "archivo", host = "x.com", sshPort = 22)
        assertEquals(listOf(guardada), Servidores.candidatos(emptyList(), "", guardada))
        assertTrue(Servidores.candidatos(emptyList(), "", null).isEmpty())
        assertTrue(Servidores.candidatos(emptyList(), "", Config(host = "")).isEmpty())
    }

    @Test
    fun el_token_esta_en_el_tercero_y_se_recuerda() {
        val b = Busqueda(lista(15))
        assertEquals("S1", b.actual().name)
        assertEquals(Paso.SIGUIENTE, b.fallo(true))
        assertEquals("S2", b.actual().name)
        assertEquals(Paso.SIGUIENTE, b.fallo(true))
        assertEquals("S3", b.actual().name)
        b.exito()
        assertEquals("S3", b.actual().name)
        // se cayó la sesión y no responde: prueba los otros, empezando por los de la lista
        assertEquals(Paso.SIGUIENTE, b.fallo(false))
        assertEquals("S1", b.actual().name)
    }

    @Test
    fun si_ninguno_conoce_el_token_se_frena_y_no_se_queda_probando() {
        val b = Busqueda(lista(15))
        repeat(14) { assertEquals(Paso.SIGUIENTE, b.fallo(true)) }
        assertEquals(Paso.NINGUNO, b.fallo(true))
    }

    @Test
    fun si_alguno_no_responde_no_se_dice_que_el_token_no_sirve() {
        val b = Busqueda(lista(3))
        assertEquals(Paso.SIGUIENTE, b.fallo(true))
        assertEquals(Paso.SIGUIENTE, b.fallo(false))      // este estaba caído
        assertEquals(Paso.REINTENTAR, b.fallo(true))       // vuelta completa: esperar y volver a empezar
        assertEquals("S1", b.actual().name)                // arranca de nuevo por el primero
        assertEquals(Paso.SIGUIENTE, b.fallo(true))
    }

    @Test
    fun el_ultimo_servidor_que_funciono_es_el_primero_en_la_vuelta_siguiente() {
        val b = Busqueda(lista(3))
        b.fallo(true); b.fallo(true)                       // S1 y S2 no lo conocen
        b.exito()                                          // S3 sí
        b.fallo(false); b.fallo(false)                     // se cae y nadie responde
        assertEquals(Paso.REINTENTAR, b.fallo(false))
        assertEquals("S3", b.actual().name)
    }

    @Test
    fun con_un_solo_servidor_se_comporta_como_antes() {
        val b = Busqueda(lista(1))
        assertEquals(Paso.RECHAZADO, b.fallo(true))        // usuario o contraseña incorrectos
        assertEquals(Paso.REINTENTAR, b.fallo(false))      // sin red: reintentar
        assertEquals("S1", b.actual().name)
    }

    @Test(expected = IllegalArgumentException::class)
    fun sin_candidatos_no_hay_busqueda() {
        Busqueda(emptyList())
    }

    @Test
    fun en_paralelo_todos_rechazan_es_error_de_usuario() {
        val b = Busqueda(lista(3))
        assertEquals(Paso.NINGUNO, b.resultadoRonda(setOf("S1", "S2", "S3"), emptySet()))
    }

    @Test
    fun en_paralelo_si_alguno_esta_caido_reintenta_y_a_la_segunda_corta() {
        val b = Busqueda(lista(3))
        assertEquals(Paso.REINTENTAR, b.resultadoRonda(setOf("S1", "S2"), setOf("S3")))
        assertEquals(Paso.NINGUNO, b.resultadoRonda(setOf("S1", "S2"), setOf("S3")))
    }

    @Test
    fun en_paralelo_si_nadie_responde_reintenta_siempre() {
        val b = Busqueda(lista(3))
        repeat(4) { assertEquals(Paso.REINTENTAR, b.resultadoRonda(emptySet(), setOf("S1", "S2", "S3"))) }
    }

    @Test
    fun en_paralelo_un_exito_reinicia_la_cuenta() {
        val b = Busqueda(lista(3))
        assertEquals(Paso.REINTENTAR, b.resultadoRonda(setOf("S1"), setOf("S2", "S3")))
        b.exitoNombre("S2")
        assertEquals("S2", b.actual().name)
        assertEquals(Paso.REINTENTAR, b.resultadoRonda(setOf("S1"), setOf("S2", "S3")))
    }
}
