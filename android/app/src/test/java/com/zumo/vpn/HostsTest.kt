package com.zumo.vpn

import com.zumo.vpn.Busqueda.Paso
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Un servidor con varios dominios o IP: se conecta aunque algunos no respondan. */
class HostsTest {
    private fun multi(h: String) = Config(name = "M", host = h, sshPort = 80)

    @Test
    fun config_lee_varios_hosts_y_los_limpia() {
        val c = Config(name = "M", host = "a.com, https://b.com/x  1.2.3.4:443;a.com", sshPort = 80).limpiar()
        assertEquals(listOf("a.com", "b.com", "1.2.3.4"), c.hosts())
        assertEquals("a.com,b.com,1.2.3.4", c.host)
        assertEquals(443, c.sshPort)       // el puerto del primer host que lo trae
        assertTrue(c.valida())
        assertEquals("b.com", c.con("b.com").host)
        assertTrue(!Config(host = " , ").valida())
    }

    @Test
    fun un_solo_host_se_comporta_como_siempre() {
        val c = Config(name = "M", host = "vps.com:8080", sshPort = 80).limpiar()
        assertEquals(listOf("vps.com"), c.hosts())
        assertEquals(8080, c.sshPort)
    }

    @Test
    fun el_servidor_con_varios_hosts_son_varios_candidatos() {
        val l = listOf(multi("a.com,b.com"), Config(name = "S2", host = "s2.com", sshPort = 80))
        assertEquals(listOf("a.com", "b.com", "s2.com"), Servidores.candidatos(l, "", null).map { it.host })
        // el último donde entró va primero, con todos sus hosts
        assertEquals(listOf("s2.com", "a.com", "b.com"), Servidores.candidatos(l, "S2", null).map { it.host })
        assertEquals(listOf("a.com", "b.com", "s2.com"), Servidores.candidatos(l, "M", null).map { it.host })
    }

    @Test
    fun ordenar_pone_primero_el_que_responde_y_deja_al_final_los_caidos() {
        val r = Hosts.ordenar(listOf("caido1", "vivo", "caido2"), 2000) { it == "vivo" }
        assertEquals(listOf("vivo", "caido1", "caido2"), r)
        // ninguno responde: se mantiene el orden original
        assertEquals(listOf("x", "y"), Hosts.ordenar(listOf("x", "y"), 2000) { false })
        assertEquals(listOf("solo"), Hosts.ordenar(listOf("solo"), 2000) { false })
    }

    @Test
    fun ordenar_no_espera_a_los_lentos_si_ya_respondio_uno() {
        val t0 = System.currentTimeMillis()
        val r = Hosts.ordenar(listOf("lento", "rapido"), 5000) { if (it == "rapido") true else { Thread.sleep(3000); true } }
        assertEquals("rapido", r.first())
        assertTrue(System.currentTimeMillis() - t0 < 2500)
    }

    @Test
    fun ordenarTodos_pone_primero_al_ultimo_despues_los_rapidos_y_al_final_los_caidos() {
        val l = listOf("S1", "S2", "S3", "S4").map { Config(name = it, host = "${it.lowercase()}.com", sshPort = 80) }
        val tiempo = mapOf("S1" to 400L, "S2" to 50L, "S3" to 0L)
        val r = Hosts.ordenarTodos(l, ultimo = "S1", esperaMs = 3000) { c ->
            if (c.name == "S4") false else { Thread.sleep(tiempo.getValue(c.name)); true }
        }
        // el último que anduvo (S1) primero, luego por rapidez (S3 antes que S2), y el caído (S4) al final con poco tiempo
        assertEquals(listOf("S1", "S3", "S2", "S4"), r.map { it.name })
        assertEquals(Hosts.TIMEOUT_CAIDO, r.last().conTimeout)
        assertEquals(15000, r.first().conTimeout)
    }

    @Test
    fun ordenarTodos_no_espera_mas_del_limite_si_alguno_se_cuelga() {
        val l = listOf("S1", "S2", "S3").map { Config(name = it, host = "${it.lowercase()}.com", sshPort = 80) }
        val t0 = System.currentTimeMillis()
        val r = Hosts.ordenarTodos(l, ultimo = "", esperaMs = 600) { c -> if (c.name == "S2") { Thread.sleep(5000); true } else true }
        assertTrue(System.currentTimeMillis() - t0 < 2500)
        assertEquals("S2", r.last().name)                 // el que no contestó a tiempo va al final
        assertEquals(setOf("S1", "S3"), r.take(2).map { it.name }.toSet())
    }

    @Test
    fun busqueda_prueba_el_otro_host_si_uno_cae_y_no_insiste_si_rechaza_el_token() {
        val l = listOf(multi("a.com,b.com"), Config(name = "S2", host = "s2.com", sshPort = 80)).flatMap { c -> c.hosts().map { c.con(it) } }
        val b = Busqueda(l)
        assertEquals("a.com", b.actual().host)
        assertEquals(Paso.SIGUIENTE, b.fallo(false))       // a.com caído → otro host del mismo servidor
        assertEquals("b.com", b.actual().host)
        assertEquals(Paso.SIGUIENTE, b.fallo(true))        // el servidor M no conoce el token
        assertEquals("s2.com", b.actual().host)
        assertEquals(Paso.NINGUNO, b.fallo(true))        // ninguno conoce el token
    }

    @Test
    fun rechazo_en_un_host_salta_los_demas_hosts_de_ese_servidor() {
        val l = listOf(multi("a.com,b.com,c.com"), Config(name = "S2", host = "s2.com", sshPort = 80)).flatMap { c -> c.hosts().map { c.con(it) } }
        val b = Busqueda(l)
        assertEquals(Paso.SIGUIENTE, b.fallo(true))
        assertEquals("s2.com", b.actual().host)            // b.com y c.com no se tocan
        assertEquals(Paso.NINGUNO, b.fallo(true))
    }

    @Test
    fun un_servidor_con_varios_hosts_y_todos_caidos_reintenta_y_si_rechaza_es_rechazado() {
        val l = multi("a.com,b.com").let { c -> c.hosts().map { c.con(it) } }
        val b = Busqueda(l)
        assertEquals(Paso.SIGUIENTE, b.fallo(false))
        assertEquals(Paso.REINTENTAR, b.fallo(false))
        assertEquals("a.com", b.actual().host)
        assertEquals(Paso.RECHAZADO, b.fallo(true))
    }

    @Test
    fun token_inexistente_con_un_host_caido_se_frena_a_la_segunda_vuelta() {
        // S1 rechaza, S2 nunca responde (caído): antes quedaba reintentando para siempre
        val b = Busqueda(listOf(Config(name = "S1", host = "a.com", sshPort = 80), Config(name = "S2", host = "b.com", sshPort = 80)))
        assertEquals(Paso.SIGUIENTE, b.fallo(true))
        assertEquals(Paso.REINTENTAR, b.fallo(false))      // primera vuelta: puede ser que el token esté en el caído
        assertEquals(Paso.SIGUIENTE, b.fallo(true))
        assertEquals(Paso.NINGUNO, b.fallo(false))         // segunda vuelta igual: se frena
    }

    @Test
    fun si_nadie_responde_nunca_se_dice_que_el_token_no_sirve() {
        val b = Busqueda(listOf(Config(name = "S1", host = "a.com", sshPort = 80), Config(name = "S2", host = "b.com", sshPort = 80)))
        repeat(5) {
            assertEquals(Paso.SIGUIENTE, b.fallo(false))
            assertEquals(Paso.REINTENTAR, b.fallo(false))
        }
    }
}
