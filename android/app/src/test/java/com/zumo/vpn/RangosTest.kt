package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicInteger

/** Rango de IP en el host: la app busca la IP del rango donde contesta el servidor. */
class RangosTest {

    @Test
    fun reconoce_cidr_y_rangos_con_guion() {
        assertEquals(254L, Rangos.tamano("104.16.0.0/24"))           // sin red ni broadcast
        assertEquals(1L, Rangos.tamano("104.16.0.7/32"))
        assertEquals(71L, Rangos.tamano("104.16.1.10-104.16.1.80"))
        assertEquals(71L, Rangos.tamano("104.16.1.10-80"))           // forma corta: solo el último número
        assertEquals(Rangos.limites("104.16.0.9/24"), Rangos.limites("104.16.0.0/24"))   // la IP base se ajusta a la red
        assertTrue(Rangos.esRango("10.0.0.0/16"))
    }

    @Test
    fun rechaza_lo_que_no_es_rango() {
        for (t in listOf("zumoserver.com", "1.2.3.4", "1.2.3.0/8", "1.2.3.0/33", "1.2.3.300/24", "1.2.3.80-1.2.3.10",
                "1.2.3.4-x", "a.com/24", "1.2.3.4-256", "")) {
            assertFalse(t, Rangos.esRango(t))
        }
    }

    @Test
    fun ips_del_rango_sin_repetir_y_recortadas() {
        val l = Rangos.ips("192.168.5.0/24")
        assertEquals(254, l.size)
        assertEquals(254, l.toSet().size)
        assertTrue(l.all { it.startsWith("192.168.5.") && it != "192.168.5.0" && it != "192.168.5.255" })
        val grande = Rangos.ips("10.0.0.0/16", max = 300)
        assertEquals(300, grande.size)
        assertEquals(300, grande.toSet().size)
        assertTrue(grande.all { it.startsWith("10.0.") })
        assertEquals(listOf("8.8.8.8"), Rangos.ips("8.8.8.8/32"))
    }

    @Test
    fun la_config_deja_el_rango_tal_cual() {
        val c = Config(name = "R", host = "104.16.0.0/24, otro.com:443", sshPort = 80).limpiar()
        assertEquals(listOf("104.16.0.0/24", "otro.com"), c.hosts())
        assertEquals(443, c.sshPort)
        val d = Config(name = "R", host = "104.16.1.10-80", sshPort = 80).limpiar()
        assertEquals("104.16.1.10-80", d.host)
        assertTrue(d.valida())
    }

    @Test
    fun buscar_encuentra_la_que_sirve_y_prueba_primero_la_anterior() {
        val lista = Rangos.ips("10.1.1.0/24")
        assertEquals("10.1.1.77", Rangos.buscar(lista, hilos = 8) { it == "10.1.1.77" })
        // la anterior sirve: no se prueba ninguna otra
        val probadas = AtomicInteger(0)
        assertEquals("10.1.1.5", Rangos.buscar(lista, primero = "10.1.1.5") { probadas.incrementAndGet(); it == "10.1.1.5" })
        assertEquals(1, probadas.get())
        // la anterior ya no sirve: se busca en el resto
        assertEquals("10.1.1.9", Rangos.buscar(lista, primero = "10.1.1.5") { it == "10.1.1.9" })
        assertNull(Rangos.buscar(lista, hilos = 8) { false })
        assertNull(Rangos.buscar(lista, cancelado = { true }) { true })
    }

    /** Un servidor de mentira: lee el payload y contesta como un proxy WebSocket seguido del saludo SSH. */
    private fun servidor(respuesta: String): ServerSocket {
        val ss = ServerSocket(0)
        Thread {
            try {
                ss.accept().use { s ->
                    val buf = ByteArray(4096)
                    s.getInputStream().read(buf)
                    s.getOutputStream().write(respuesta.toByteArray(Charsets.ISO_8859_1))
                    s.getOutputStream().flush()
                    Thread.sleep(300)
                }
            } catch (_: Exception) { }
        }.apply { isDaemon = true; start() }
        return ss
    }

    @Test
    fun sonda_ve_el_ssh_detras_del_payload() {
        val payload = "GET / HTTP/1.1[crlf]Host: front.example[crlf]Upgrade: websocket[crlf][crlf]"
        servidor("HTTP/1.1 101 Switching Protocols\r\n\r\nSSH-2.0-OpenSSH_9.6\r\n").use { ss ->
            val c = Config(name = "R", host = "127.0.0.1", sshPort = ss.localPort, payload = payload)
            assertTrue(Rangos.sondaSsh(c, {}))
        }
        // contesta, pero no es nuestro servidor (un CDN sin el túnel): no sirve
        servidor("HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n").use { ss ->
            val c = Config(name = "R", host = "127.0.0.1", sshPort = ss.localPort, payload = payload)
            assertFalse(Rangos.sondaSsh(c, {}, leerMs = 1000))
        }
    }

    @Test
    fun un_rango_cuenta_como_vivo_en_el_sondeo() {
        assertTrue(Hosts.sondeoTcp(Config(name = "R", host = "104.16.0.0/24", sshPort = 80)) {})
    }
}
