package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DnsTest {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    // Los mensajes de abajo los armó un script aparte (no esta misma clase): la pregunta por www.ejemplo.com con id 0x1234
    // y una respuesta con un alias (CNAME, con nombres comprimidos) y dos IP.
    private val consulta = "1234010000010000000000000377777707656a656d706c6f03636f6d0000010001"
    private val respuesta = "1234818000010003000000000377777707656a656d706c6f03636f6d0000010001c00c000500010000012c00110363646e07656a656d706c6f036e657400c02d000100010000007800045db8d822c02d000100010000003c00045db8d823"
    private val noExiste = "1234818300010000000000000377777707656a656d706c6f03636f6d0000010001"
    private val soloAlias = "1234818000010001000000000377777707656a656d706c6f03636f6d0000010001c00c000500010000012c00110363646e07656a656d706c6f036e657400"
    private val cortada = "1234818000010003000000000377777707656a656d706c6f03636f6d0000010001c00c000500010000012c00110363646e07656a656d706c6f036e657400c02d000100010000007800045db8d822c02d000100010000003c"

    @Test
    fun arma_la_pregunta_igual_que_el_script_de_referencia() {
        assertEquals(consulta, DnsConsulta.armar("www.ejemplo.com", 0x1234)!!.joinToString("") { "%02x".format(it) })
        // el punto final y las mayúsculas no cambian el largo de las partes
        assertEquals(consulta.length, DnsConsulta.armar("www.ejemplo.com.", 0x1234)!!.size * 2)
    }

    @Test
    fun nombres_que_no_sirven_no_se_arman() {
        assertNull(DnsConsulta.armar("", 1))
        assertNull(DnsConsulta.armar("a..b.com", 1))
        assertNull(DnsConsulta.armar("x".repeat(64) + ".com", 1))
        assertNull(DnsConsulta.armar(("abcdefghi.").repeat(26) + "com", 1))     // más de 253 letras
    }

    @Test
    fun lee_las_ip_aunque_vengan_detras_de_un_alias() {
        val r = DnsConsulta.leer(hex(respuesta), 0x1234)!!
        assertEquals(0, r.rcode)
        assertEquals(listOf("93.184.216.34", "93.184.216.35"), r.ips)
        assertEquals(60, r.ttl)             // el menor de los dos (120 y 60)
    }

    @Test
    fun el_nombre_que_no_existe_o_sin_registros_a_no_da_ip() {
        val nx = DnsConsulta.leer(hex(noExiste), 0x1234)!!
        assertEquals(3, nx.rcode)
        assertTrue(nx.ips.isEmpty())
        val alias = DnsConsulta.leer(hex(soloAlias), 0x1234)!!
        assertEquals(0, alias.rcode)
        assertTrue(alias.ips.isEmpty())
    }

    @Test
    fun descarta_lo_roto_o_lo_que_no_es_la_respuesta_pedida() {
        assertNull("otro id", DnsConsulta.leer(hex(respuesta), 0x9999))
        assertNull("cortada", DnsConsulta.leer(hex(cortada), 0x1234))
        assertNull("muy corta", DnsConsulta.leer(ByteArray(5), 0x1234))
        assertNull("es una pregunta, no una respuesta", DnsConsulta.leer(hex(consulta), 0x1234))
        // un puntero de compresión que se sale del mensaje no tiene que colgar ni tirar excepción
        val rota = hex(respuesta).copyOf(); rota[rota.size - 6] = 0xC0.toByte()
        DnsConsulta.leer(rota, 0x1234)
    }

    @Test
    fun elige_los_dns_por_nombre() {
        assertEquals(listOf("8.8.8.8", "8.8.4.4"), Dns.servidores("google"))
        assertEquals(listOf("8.8.8.8", "8.8.4.4"), Dns.servidores("  Google "))
        assertEquals(listOf("1.1.1.1", "1.0.0.1"), Dns.servidores("cloudflare"))
        assertEquals(listOf("1.1.1.1", "1.0.0.1"), Dns.servidores("CF"))
        assertTrue(Dns.servidores("").isEmpty())
        assertTrue(Dns.servidores("auto").isEmpty())
        assertTrue(Dns.servidores("-").isEmpty())
    }

    @Test
    fun dns_a_mano_acepta_solo_ip_validas() {
        assertEquals(listOf("9.9.9.9", "149.112.112.112"), Dns.servidores("9.9.9.9, 149.112.112.112"))
        assertEquals(listOf("9.9.9.9"), Dns.servidores("9.9.9.9;9.9.9.9"))                      // sin repetir
        assertEquals(listOf("1.2.3.4"), Dns.servidores("dns.malo.com 1.2.3.4 999.1.1.1 1.2.3"))   // lo inválido se descarta
        assertEquals(4, Dns.servidores("1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4 5.5.5.5").size)         // máximo 4
        assertTrue(Dns.servidores("no soy una ip").isEmpty())
        assertFalse(Dns.ipv4("256.1.1.1")); assertFalse(Dns.ipv4("1.1.1")); assertFalse(Dns.ipv4("a.b.c.d")); assertTrue(Dns.ipv4("0.0.0.0"))
    }

    @Test
    fun la_forma_guardada_es_siempre_la_misma() {
        assertEquals("google", Dns.normalizar("GOOGLE"))
        assertEquals("cloudflare", Dns.normalizar("cloudflare"))
        assertEquals("9.9.9.9,8.8.8.8", Dns.normalizar("9.9.9.9 8.8.8.8"))
        assertEquals("", Dns.normalizar("cualquier cosa"))
        assertEquals("Google (8.8.8.8)", Dns.etiqueta("google"))
        assertEquals("9.9.9.9, 8.8.8.8", Dns.etiqueta("9.9.9.9,8.8.8.8"))
    }

    @Test
    fun el_dns_viaja_con_el_servidor() {
        val lista = Servidores.parsear(
            """
            [APP 01]
            host = a.com
            dns = google

            [APP 02]
            host = b.com
            dns = 9.9.9.9, 1.1.1.1

            [APP 03]
            host = c.com
            """.trimIndent()
        )
        assertEquals(listOf("google", "9.9.9.9,1.1.1.1", ""), lista.map { it.dns })
        assertEquals(listOf("8.8.8.8", "8.8.4.4"), lista[0].dnsServidores())
        assertTrue(lista[2].dnsServidores().isEmpty())
        // al elegir un host de un servidor con varios, el DNS sigue siendo el mismo
        assertEquals("google", lista[0].con("x.com").dns)
        assertNotNull(lista[1])
    }
}
