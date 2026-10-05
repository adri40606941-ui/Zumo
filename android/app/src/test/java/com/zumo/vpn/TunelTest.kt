package com.zumo.vpn

import org.apache.sshd.common.forward.DefaultForwarderFactory
import org.apache.sshd.server.SshServer
import org.apache.sshd.server.forward.AcceptAllForwardingFilter
import org.apache.sshd.server.keyprovider.SimpleGeneratorHostKeyProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files

/** Prueba de punta a punta en la JVM: SSH real (Apache MINA) + proxy WebSocket falso + SOCKS local. */
class TunelTest {
    private lateinit var sshd: SshServer
    private lateinit var http: ServerSocket
    private var sshPort = 0
    private var httpPort = 0
    private val cerrar = mutableListOf<AutoCloseable>()

    @Before
    fun arrancar() {
        sshd = SshServer.setUpDefaultServer()
        sshd.port = 0
        sshd.keyPairProvider = SimpleGeneratorHostKeyProvider(Files.createTempFile("hk", ".ser"))
        sshd.setPasswordAuthenticator { u, p, _ -> u == "cliente" && p == "clave1" }
        sshd.forwardingFilter = AcceptAllForwardingFilter.INSTANCE
        sshd.forwarderFactory = DefaultForwarderFactory.INSTANCE
        sshd.start()
        sshPort = sshd.port

        http = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        Thread {
            try {
                while (true) {
                    val c = http.accept()
                    Thread {
                        try {
                            val i = c.getInputStream()
                            var fin = 0
                            while (fin < 4) {
                                val b = i.read(); if (b < 0) break
                                fin = when { (b == 13 && (fin == 0 || fin == 2)) || (b == 10 && (fin == 1 || fin == 3)) -> fin + 1; b == 13 -> 1; else -> 0 }
                            }
                            val body = "hola desde el servidor"
                            c.getOutputStream().write("HTTP/1.0 200 OK\r\nContent-Length: ${body.length}\r\n\r\n$body".toByteArray())
                            c.getOutputStream().flush()
                        } catch (_: Exception) {} finally { try { c.close() } catch (_: Exception) {} }
                    }.start()
                }
            } catch (_: Exception) {}
        }.start()
        httpPort = http.localPort
    }

    @After
    fun parar() {
        cerrar.forEach { try { it.close() } catch (_: Exception) {} }
        try { sshd.stop(true) } catch (_: Exception) {}
        try { http.close() } catch (_: Exception) {}
    }

    /** Proxy que lee un "payload", contesta con [respuesta] y reenvía el resto al SSH. */
    private class FalsoProxy(val sshPort: Int, val respuesta: String, val espera: String = "\r\n\r\n", val cerrar: Boolean = false) : AutoCloseable {
        val ss = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        val puerto get() = ss.localPort
        @Volatile var recibido = ""
        init {
            Thread {
                try {
                    while (true) {
                        val c = ss.accept()
                        Thread { atender(c) }.start()
                    }
                } catch (_: Exception) {}
            }.start()
        }
        private fun atender(c: Socket) {
            try {
                val i = c.getInputStream()
                val sb = ByteArrayOutputStream()
                while (!sb.toString(Charsets.ISO_8859_1).endsWith(espera)) {
                    val b = i.read(); if (b < 0) return
                    sb.write(b)
                }
                recibido = sb.toString(Charsets.ISO_8859_1)
                if (cerrar) { c.getOutputStream().write(respuesta.toByteArray()); c.getOutputStream().flush(); return }
                val up = Socket("127.0.0.1", sshPort)
                if (respuesta.isNotEmpty()) { c.getOutputStream().write(respuesta.toByteArray()); c.getOutputStream().flush() }
                Thread { copiar(up.getInputStream(), c.getOutputStream()) }.start()
                copiar(i, up.getOutputStream())
            } catch (_: Exception) {
            } finally { try { c.close() } catch (_: Exception) {} }
        }
        private fun copiar(a: InputStream, b: OutputStream) {
            val buf = ByteArray(8192)
            while (true) { val r = a.read(buf); if (r < 0) break; b.write(buf, 0, r); b.flush() }
        }
        override fun close() { ss.close() }
    }

    private fun socksGet(socksPort: Int, host: String, port: Int): String {
        Socket("127.0.0.1", socksPort).use { s ->
            s.soTimeout = 15000
            val o = s.getOutputStream(); val i = s.getInputStream()
            o.write(byteArrayOf(5, 1, 0)); o.flush()
            val g = ByteArray(2); i.readNBytes(g, 0, 2)
            assertEquals(0, g[1].toInt())
            val hb = host.toByteArray()
            o.write(byteArrayOf(5, 1, 0, 3, hb.size.toByte()) + hb + byteArrayOf((port shr 8).toByte(), port.toByte())); o.flush()
            val r = ByteArray(10); i.readNBytes(r, 0, 10)
            assertEquals("SOCKS rep", 0, r[1].toInt())
            o.write("GET / HTTP/1.0\r\nHost: x\r\n\r\n".toByteArray()); o.flush()
            return String(i.readAllBytes())
        }
    }

    private fun conSocks(t: SshTunnel, f: (Int) -> Unit) {
        val sp = ServerSocket(0).use { it.localPort }
        val srv = SocksServer(sp) { t }
        srv.start()
        try { f(sp) } finally { srv.stop(); t.close() }
    }

    @Test
    fun websocket_ssh_y_socks_de_punta_a_punta() {
        val px = FalsoProxy(sshPort, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n").also { cerrar += it }
        val cfg = Config(host = "127.0.0.1", sshPort = px.puerto,
            payload = "GET /ssh HTTP/1.1[crlf]Host: ejemplo.cloudfront.net[crlf]Upgrade: websocket[crlf]Connection: Upgrade[crlf][crlf]")
        val t = SshTunnel(cfg, "cliente", "clave1")
        t.connect()
        assertTrue(t.conectado)
        assertTrue("cabecera WS: ${px.recibido}", px.recibido.startsWith("GET /ssh HTTP/1.1\r\nHost: ejemplo.cloudfront.net\r\nUpgrade: websocket"))
        conSocks(t) { sp ->
            val r = socksGet(sp, "127.0.0.1", httpPort)
            assertTrue(r, r.contains("hola desde el servidor"))
        }
    }

    @Test
    fun payload_personalizado_con_comodines_y_sin_respuesta_http() {
        val px = FalsoProxy(sshPort, "").also { cerrar += it }   // no contesta HTTP: va directo el banner SSH
        val cfg = Config(host = "127.0.0.1", sshPort = px.puerto,
            payload = "CONNECT [host_port] HTTP/1.1[crlf]Host: cdn.ejemplo.com[crlf][crlf]")
        val t = SshTunnel(cfg, "cliente", "clave1")
        t.connect()
        assertEquals("CONNECT 127.0.0.1:${px.puerto} HTTP/1.1\r\nHost: cdn.ejemplo.com\r\n\r\n", px.recibido)
        conSocks(t) { sp -> assertTrue(socksGet(sp, "127.0.0.1", httpPort).contains("hola")) }
    }

    @Test
    fun ssh_directo() {
        val t = SshTunnel(Config(host = "localhost", sshPort = sshPort), "cliente", "clave1")
        t.connect()
        conSocks(t) { sp -> assertTrue(socksGet(sp, "127.0.0.1", httpPort).contains("hola")) }
    }

    @Test
    fun clave_incorrecta_da_error() {
        val t = SshTunnel(Config(host = "localhost", sshPort = sshPort), "cliente", "mala")
        try { t.connect(); fail("debía fallar") } catch (e: Exception) {
            assertTrue(e.message ?: "", (e.message ?: "").contains("Auth", true))
        }
    }

    @Test
    fun respuesta_http_de_error_se_informa() {
        val px = FalsoProxy(sshPort, "HTTP/1.1 403 Forbidden\r\n\r\n", cerrar = true).also { cerrar += it }
        val cfg = Config(host = "127.0.0.1", sshPort = px.puerto, payload = "GET / HTTP/1.1[crlf][crlf]")
        try { SshTunnel(cfg, "cliente", "clave1").connect(); fail("debía fallar") } catch (e: Exception) {
            assertTrue(e.message ?: "", (e.message ?: "").contains("403"))
        }
    }

    @Test
    fun payload_con_cabeceras_senuelo_estilo_http_custom() {
        val payload = "HEAD / HTTP/1.1[crlf]Host:[host][crlf][crlf][split][crlf][crlf]GET- / HTTP/1.1[crlf]Host:[host][lf][lf]" +
            "GET /appp4 HTTP/1.1[crlf]Host:dyyl4x18ayspo.cloudfront.net[lf]Connection:  Upgrade[lf]Upgrade: websocket[lf]" +
            "User-Agent: Googlebot/2.1 (+http://www.google.com/bot.html)\nConnection:keep-Alive[lf][lf][split]"
        val respuestas = "HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n" +
            "HTTP/1.1 400 Bad Request\r\nContent-Length: 11\r\n\r\nBad Request" +
            "HTTP/1.1 302 Found\r\nLocation: /x\r\nContent-Length: 4\r\n\r\n" +   // HEAD-like: sin cuerpo
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"
        val px = FalsoProxy(sshPort, respuestas, espera = "Connection:keep-Alive\n\n").also { cerrar += it }
        val cfg = Config(host = "127.0.0.1", sshPort = px.puerto, payload = payload)
        val t = SshTunnel(cfg, "cliente", "clave1")
        t.connect()
        assertEquals(
            "HEAD / HTTP/1.1\r\nHost:127.0.0.1\r\n\r\n\r\n\r\nGET- / HTTP/1.1\r\nHost:127.0.0.1\n\nGET /appp4 HTTP/1.1\r\n" +
                "Host:dyyl4x18ayspo.cloudfront.net\nConnection:  Upgrade\nUpgrade: websocket\n" +
                "User-Agent: Googlebot/2.1 (+http://www.google.com/bot.html)\nConnection:keep-Alive\n\n", px.recibido
        )
        assertEquals(2, Transport.partir(Transport.expandir(payload, cfg)).size)
        conSocks(t) { sp -> assertTrue(socksGet(sp, "127.0.0.1", httpPort).contains("hola")) }
    }

    @Test
    fun limpia_el_host_pegado_por_el_usuario() {
        assertEquals(Config(host = "vps.ejemplo.com", sshPort = 8080), Config(host = " https://vps.ejemplo.com:8080/ruta ", sshPort = 22).limpiar())
        assertEquals(Config(host = "10.0.0.5", sshPort = 443), Config(host = "10.0.0.5:443").limpiar())
        assertEquals(Config(host = "dominio.com", sshPort = 80), Config(host = "dominio.com", sshPort = 80).limpiar())
    }

    @Test
    fun comodines_y_split() {
        val c = Config(host = "vps.ejemplo.com", sshPort = 22)
        assertEquals("GET vps.ejemplo.com:22 HTTP/1.1\r\n", Transport.expandir("GET [host_port] [protocol][crlf]", c))
        assertEquals(listOf("A", "B"), Transport.partir("A[split]B"))
        assertEquals(24, Transport.base64(ByteArray(16) { 1 }).length)
        assertEquals("TWFu", Transport.base64("Man".toByteArray()))
        assertEquals("TWE=", Transport.base64("Ma".toByteArray()))
    }
}
