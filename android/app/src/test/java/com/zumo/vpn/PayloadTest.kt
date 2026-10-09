package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Test

class PayloadTest {
    private val c = Config(name = "X", host = "app04.zumoserver.com", sshPort = 80, sni = "")

    @Test
    fun rotate_toma_un_valor_distinto_en_cada_conexion() {
        val p = "Host: cdn[rotate=2;3;1].panda7.online"
        assertEquals("Host: cdn2.panda7.online", Transport.expandir(p, c, 0))
        assertEquals("Host: cdn3.panda7.online", Transport.expandir(p, c, 1))
        assertEquals("Host: cdn1.panda7.online", Transport.expandir(p, c, 2))
        assertEquals("Host: cdn2.panda7.online", Transport.expandir(p, c, 3))
        assertEquals("Host: cdn1.panda7.online", Transport.expandir(p, c, 5))
    }

    @Test
    fun random_elige_uno_de_la_lista() {
        repeat(20) {
            val r = Transport.expandir("[random=a;b;c]", c)
            assertEquals(true, r in listOf("a", "b", "c"))
        }
    }

    @Test
    fun comodines_comunes() {
        assertEquals("a\r\n\r\n\r\nb", Transport.expandir("a[crlf*3]b", c))
        assertEquals("\n\n", Transport.expandir("[lf*2]", c))
        assertEquals("\n\r\t", Transport.expandir("[lfcr][tab]", c))
        assertEquals("GET app04.zumoserver.com HTTP/1.1", Transport.expandir("[method] [host] [protocol]", c))
        assertEquals("app04.zumoserver.com", Transport.expandir("[sni]", c))
        assertEquals("x.net", Transport.expandir("[sni]", c.copy(sni = "x.net")))
        assertEquals(true, Transport.expandir("User-Agent: [ua]", c).contains("Mozilla/5.0"))
    }

    @Test
    fun separadores_y_pausas() {
        assertEquals(listOf("A" to 0L, "B" to 150L), Transport.partes("A[split]B"))
        assertEquals(listOf("A" to 0L, "B" to 150L), Transport.partes("A[instant_split]B"))      // igual que siempre
        assertEquals(listOf("A" to 0L, "B" to 1500L), Transport.partes("A[delay_split]B"))
        assertEquals(listOf("A" to 0L, "B" to 400L, "C" to 150L), Transport.partes("A[split_delay=400]B[split]C"))
        assertEquals(listOf("sin cortes" to 0L), Transport.partes("sin cortes"))
        assertEquals(listOf("A" to 0L), Transport.partes("[split]A[split]"))
    }

    @Test
    fun payload_de_front_con_senuelo() {
        val p = "GET /app04 HTTP/1.1[crlf]Host: d39kimexpzqk76.cloudfront.net[crlf][crlf][split]" +
            "GET / HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf]Connection: Upgrade[crlf][crlf]"
        val partes = Transport.partir(Transport.expandir(p, c))
        assertEquals(2, partes.size)
        assertEquals("GET /app04 HTTP/1.1\r\nHost: d39kimexpzqk76.cloudfront.net\r\n\r\n", partes[0])
        assertEquals(true, partes[1].contains("Host: app04.zumoserver.com\r\n"))
    }

    @Test
    fun el_payload_del_ejemplo_con_rotate_se_arma() {
        val p = "COPY / HTTP/1.3[crlf]Host: [host][crlf][crlf]\n[lf][lf][split][lf][lf]\nX / HTTP/1.2[crlf]Host: [host][crlf][lf][crlf]" +
            "GET / HTTP/1.1[crlf]Host: cdn[rotate=2;3;1;4;5;6;7].panda7.online[crlf]Backend: vps196[crlf]Upgrade: websocket[crlf]Connection: Upgrade[crlf][crlf]"
        val e = Transport.expandir(p, c, 3)
        assertEquals(true, e.contains("Host: cdn4.panda7.online\r\n"))
        assertEquals(false, e.contains("[rotate"))
        assertEquals(2, Transport.partir(e).size)
    }
}
