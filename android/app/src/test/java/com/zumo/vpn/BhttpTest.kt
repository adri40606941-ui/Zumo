package com.zumo.vpn

import org.apache.sshd.common.forward.DefaultForwarderFactory
import org.apache.sshd.server.SshServer
import org.apache.sshd.server.forward.AcceptAllForwardingFilter
import org.apache.sshd.server.keyprovider.SimpleGeneratorHostKeyProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.DataInputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue

/** BHTTP v1 de punta a punta en la JVM: servidor BHTTP falso (mismo protocolo) delante de un SSH real. */
class BhttpTest {
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
                            // respuesta grande: obliga a varias bajadas en lote
                            val body = "x".repeat(300_000)
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

    /** Servidor BHTTP v1 mínimo: sondea, abre sesión (y corta esa conexión), sube en orden de secuencia,
     *  baja en lote, cierra. [pedidosPorConexion] corta la conexión TCP cada tantos pedidos, como el real. */
    private class FalsoBhttp(val backend: Int, val pedidosPorConexion: Int = 1_000_000) : AutoCloseable {
        val ss = ServerSocket(0, 20, InetAddress.getByName("127.0.0.1"))
        val puerto get() = ss.localPort
        class Ses(val s: Socket) {
            val cola = LinkedBlockingQueue<ByteArray>()
            @Volatile var esperado = 0L
            @Volatile var fin = false
        }
        val sesiones = ConcurrentHashMap<String, Ses>()
        @Volatile var cerradas = 0
        @Volatile var sondas = 0
        init {
            Thread {
                try { while (true) { val c = ss.accept(); Thread { atender(c) }.start() } } catch (_: Exception) {}
            }.start()
        }
        private fun resp(c: Socket, st: Int, d: ByteArray) {
            val h = byteArrayOf(st.toByte(), (d.size ushr 24).toByte(), (d.size ushr 16).toByte(), (d.size ushr 8).toByte(), d.size.toByte())
            c.getOutputStream().write(h + d)
            c.getOutputStream().flush()
        }
        private fun atender(c: Socket) {
            try {
                val i = DataInputStream(c.getInputStream())
                var n = 0
                while (true) {
                    val modo = i.read(); if (modo < 0) return
                    val sid = ByteArray(16); i.readFully(sid)
                    val seq = i.readLong()
                    val ln = i.readInt()
                    val cuerpo = ByteArray(ln); i.readFully(cuerpo)
                    val plano = Bhttp.xor(sid, modo, seq, false, cuerpo)
                    val k = sid.joinToString("") { "%02x".format(it) }
                    val ses = sesiones[k]
                    when (modo) {
                        0 -> { sondas++; resp(c, 0, Bhttp.xor(sid, 0, 0, true, "BHP1\u0001\u0000\u0000\u0000\u0000\u0000".toByteArray(Charsets.ISO_8859_1))) }
                        1 -> {
                            if (ses == null) {
                                if (ln != 0 || seq != 0L) { resp(c, 1, "unknown session".toByteArray()); return }
                                val s = Socket("127.0.0.1", backend)
                                val x = Ses(s); sesiones[k] = x
                                Thread {
                                    val b = ByteArray(8192)
                                    try { while (true) { val r = s.getInputStream().read(b); if (r < 0) break; x.cola.put(b.copyOf(r)) } } catch (_: Exception) {}
                                    x.fin = true
                                }.start()
                                resp(c, 0, ByteArray(0)); return   // el real corta la conexión tras abrir
                            }
                            if (seq != ses.esperado) { resp(c, 1, "bad seq".toByteArray()); return }
                            ses.esperado++
                            ses.s.getOutputStream().write(plano); ses.s.getOutputStream().flush()
                            resp(c, 0, ByteArray(0))
                        }
                        3 -> {
                            if (ses == null) { resp(c, 1, "unknown session".toByteArray()); return }
                            val tam = ((plano[0].toInt() and 0xff) shl 24) or ((plano[1].toInt() and 0xff) shl 16) or ((plano[2].toInt() and 0xff) shl 8) or (plano[3].toInt() and 0xff)
                            val cant = ((plano[4].toInt() and 0xff) shl 8) or (plano[5].toInt() and 0xff)
                            for (j in 0 until cant) {
                                var d = ses.cola.poll(2, java.util.concurrent.TimeUnit.MILLISECONDS)
                                if (d == null && ses.fin) { resp(c, 1, "closed".toByteArray()); return }
                                if (d == null) d = ByteArray(0)
                                if (d.size > tam) { ses.cola.put(d.copyOfRange(tam, d.size)); d = d.copyOf(tam) }
                                val p = byteArrayOf((d.size ushr 24).toByte(), (d.size ushr 16).toByte(), (d.size ushr 8).toByte(), d.size.toByte())
                                resp(c, 2, p + Bhttp.xor(sid, 3, seq + j, true, d))
                            }
                        }
                        4 -> { cerradas++; ses?.s?.close(); sesiones.remove(k); resp(c, 0, ByteArray(0)); return }
                        else -> { resp(c, 1, "unsupported mode $modo".toByteArray()); return }
                    }
                    if (++n >= pedidosPorConexion) return
                }
            } catch (_: Exception) {
            } finally { try { c.close() } catch (_: Exception) {} }
        }
        override fun close() { ss.close(); sesiones.values.forEach { try { it.s.close() } catch (_: Exception) {} } }
    }

    private fun socksGet(socksPort: Int, host: String, port: Int): String {
        Socket("127.0.0.1", socksPort).use { s ->
            s.soTimeout = 30000
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

    private fun probar(fb: FalsoBhttp) {
        val cfg = Config(host = "127.0.0.1", sshPort = fb.puerto)   // sin payload: la app detecta BHTTP por el puerto
        val t = SshTunnel(cfg, "cliente", "clave1")
        t.connect()
        assertTrue(t.conectado)
        assertTrue("debía haber sondeado", fb.sondas >= 1)
        val sp = ServerSocket(0).use { it.localPort }
        val srv = SocksServer(sp) { t }
        srv.start()
        try {
            val r = socksGet(sp, "127.0.0.1", httpPort)
            assertTrue("respuesta de ${r.length} bytes", r.length > 300_000 && r.endsWith("xxxx"))
        } finally { srv.stop(); t.close() }
        val limite = System.currentTimeMillis() + 5000
        while (fb.cerradas == 0 && System.currentTimeMillis() < limite) Thread.sleep(50)
        assertEquals("la sesión BHTTP debe cerrarse (modo 4)", 1, fb.cerradas)
    }

    @Test
    fun bhttp_detectado_por_el_puerto_sin_payload() {
        probar(FalsoBhttp(sshPort).also { cerrar += it })
    }

    @Test
    fun bhttp_se_recupera_si_el_servidor_corta_la_conexion_tcp() {
        probar(FalsoBhttp(sshPort, pedidosPorConexion = 3).also { cerrar += it })
    }

    @Test
    fun con_payload_no_se_usa_bhttp() {
        val fb = FalsoBhttp(sshPort).also { cerrar += it }
        val cfg = Config(host = "127.0.0.1", sshPort = fb.puerto, payload = "GET / HTTP/1.1[crlf][crlf]")
        try { SshTunnel(cfg, "cliente", "clave1").connect() } catch (_: Exception) {}
        assertEquals("con payload no debe sondear", 0, fb.sondas)
    }

    @Test
    fun puerto_ssh_normal_no_es_bhttp() {
        assertFalse(Bhttp.sonda { Socket("127.0.0.1", sshPort) })
    }

    @Test
    fun flujo_de_claves_coincide_con_el_servidor_real() {
        // vector calculado con el cliente de referencia (Go/Python): sid = 16 x 0x01, modo 3, seq 7, bajada
        val sid = ByteArray(16) { 1 }
        val x = Bhttp.xor(sid, 3, 7, true, ByteArray(40))
        assertEquals(176, x[32].toInt() and 0xff)   // primer byte de sha256(sid+3+seq 7+1+contador 1), calculado en Python
        assertEquals(40, x.size)
    }
}
