package com.zumo.vpn

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList

/** El proxy del botón "WiFi": entiende pedidos de proxy y los saca por el túnel (aquí, un servidor local falso). */
class HttpProxyServerTest {
    private lateinit var origen: ServerSocket
    private lateinit var eco: ServerSocket
    private lateinit var proxy: HttpProxyServer
    private var puertoProxy = 0
    private val recibido = CopyOnWriteArrayList<String>()
    private val pedidos = CopyOnWriteArrayList<Pair<String, Int>>()

    private fun libre(): Int = ServerSocket(0).use { it.localPort }

    @Before
    fun armar() {
        origen = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        Thread {
            while (!origen.isClosed) {
                try {
                    val c = origen.accept()
                    Thread {
                        try {
                            val i = c.getInputStream(); val o = c.getOutputStream()
                            val sb = StringBuilder()
                            // lee la cabecera
                            while (!sb.endsWith("\r\n\r\n")) { val b = i.read(); if (b < 0) break; sb.append(b.toChar()) }
                            val largo = Regex("(?i)content-length: *(\\d+)").find(sb)?.groupValues?.get(1)?.toInt() ?: 0
                            repeat(largo) { i.read() }          // el cuerpo, para cerrar sin dejar datos sin leer
                            recibido.add(sb.toString())
                            o.write("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok".toByteArray())
                            o.flush()
                        } catch (_: Exception) {
                        } finally { try { c.close() } catch (_: Exception) {} }
                    }.start()
                } catch (_: Exception) { break }
            }
        }.start()
        eco = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        Thread {
            while (!eco.isClosed) {
                try {
                    val c = eco.accept()
                    Thread {
                        try {
                            val i = c.getInputStream(); val o = c.getOutputStream()
                            val b = ByteArray(1024)
                            while (true) { val n = i.read(b); if (n < 0) break; o.write(b, 0, n); o.flush() }
                        } catch (_: Exception) {
                        } finally { try { c.close() } catch (_: Exception) {} }
                    }.start()
                } catch (_: Exception) { break }
            }
        }.start()
        puertoProxy = libre()
        proxy = HttpProxyServer(puertoProxy) { host, port ->
            pedidos.add(Pair(host, port))
            if (host == "no.existe") null
            else {
                val s = Socket("127.0.0.1", if (port == 443) eco.localPort else origen.localPort)   // "el túnel": llega a un servidor falso
                object : HttpProxyServer.Salida {
                    override val entrada: InputStream = s.getInputStream()
                    override val salida: OutputStream = s.getOutputStream()
                    override fun cerrar() { try { s.close() } catch (_: Exception) {} }
                }
            }
        }
        proxy.start(InetAddress.getByName("127.0.0.1"))
    }

    @After
    fun cerrar() {
        proxy.stop()
        origen.close()
        eco.close()
    }

    private fun leerTodo(s: Socket): String = s.getInputStream().readBytes().toString(Charsets.ISO_8859_1)

    @Test
    fun pedido_http_comun_sale_por_el_tunel_y_vuelve() {
        Socket("127.0.0.1", puertoProxy).use { c ->
            c.soTimeout = 5000
            c.getOutputStream().write(("GET http://ejemplo.com:8080/a/b?x=1 HTTP/1.1\r\nHost: ejemplo.com:8080\r\n" +
                "Proxy-Connection: keep-alive\r\nProxy-Authorization: Basic abc\r\nConnection: keep-alive\r\nAccept: */*\r\n\r\n").toByteArray())
            val r = leerTodo(c)
            assertTrue(r, r.startsWith("HTTP/1.1 200 OK"))
            assertTrue(r.endsWith("ok"))
        }
        assertEquals(listOf(Pair("ejemplo.com", 8080)), pedidos.toList())
        val visto = recibido.single()
        assertTrue(visto, visto.startsWith("GET /a/b?x=1 HTTP/1.1\r\n"))
        assertTrue(visto.contains("Host: ejemplo.com:8080\r\n"))
        assertTrue(visto.contains("Accept: */*\r\n"))
        assertFalse(visto.lowercase().contains("proxy-"))
        assertEquals(1, Regex("(?i)connection:").findAll(visto).count())
        assertTrue(visto.contains("Connection: close\r\n"))
    }

    @Test
    fun el_cuerpo_de_un_post_tambien_pasa() {
        Socket("127.0.0.1", puertoProxy).use { c ->
            c.soTimeout = 5000
            c.getOutputStream().write("POST http://ejemplo.com/s HTTP/1.1\r\nHost: ejemplo.com\r\nContent-Length: 3\r\n\r\nabc".toByteArray())
            assertTrue(leerTodo(c).startsWith("HTTP/1.1 200"))
        }
        assertTrue(recibido.single().startsWith("POST /s HTTP/1.1"))
    }

    @Test
    fun connect_abre_un_tunel_de_ida_y_vuelta() {
        Socket("127.0.0.1", puertoProxy).use { c ->
            c.soTimeout = 5000
            val o = c.getOutputStream(); val i = c.getInputStream()
            o.write("CONNECT seguro.com:443 HTTP/1.1\r\nHost: seguro.com:443\r\n\r\n".toByteArray()); o.flush()
            val cab = StringBuilder()
            while (!cab.endsWith("\r\n\r\n")) cab.append(i.read().toChar())
            assertTrue(cab.toString(), cab.startsWith("HTTP/1.1 200"))
            o.write("hola".toByteArray()); o.flush()
            val b = ByteArray(4); var n = 0
            while (n < 4) n += i.read(b, n, 4 - n)
            assertEquals("hola", String(b))
        }
        assertEquals(Pair("seguro.com", 443), pedidos.first())
    }

    @Test
    fun si_el_tunel_no_llega_da_502() {
        Socket("127.0.0.1", puertoProxy).use { c ->
            c.soTimeout = 5000
            c.getOutputStream().write("GET http://no.existe/ HTTP/1.1\r\nHost: no.existe\r\n\r\n".toByteArray())
            assertTrue(leerTodo(c).startsWith("HTTP/1.1 502"))
        }
    }

    @Test
    fun un_pedido_roto_da_400_y_no_sale_a_ningun_lado() {
        Socket("127.0.0.1", puertoProxy).use { c ->
            c.soTimeout = 5000
            c.getOutputStream().write("GET /sin-host HTTP/1.1\r\n\r\n".toByteArray())   // no es un pedido de proxy
            assertTrue(leerTodo(c).startsWith("HTTP/1.1 400"))
        }
        assertTrue(pedidos.isEmpty())
    }

    @Test
    fun parsea_destinos() {
        val p = HttpProxyServer.parsear("CONNECT [2001:db8::1]:8443 HTTP/1.1\r\n\r\n")!!
        assertTrue(p.connect); assertEquals("2001:db8::1", p.host); assertEquals(8443, p.puerto)
        assertEquals(443, HttpProxyServer.parsear("CONNECT a.com HTTP/1.1\r\n\r\n")!!.puerto)
        val h = HttpProxyServer.parsear("GET http://a.com?x=1 HTTP/1.0\r\nHost: a.com\r\n\r\n")!!
        assertEquals(Pair("a.com", 80), Pair(h.host, h.puerto))
        assertTrue(String(h.cabecera).startsWith("GET /?x=1 HTTP/1.0\r\n"))
        assertNull(HttpProxyServer.parsear("CONNECT a.com:99999 HTTP/1.1\r\n\r\n"))
        assertNull(HttpProxyServer.parsear("CONNECT a.com:abc HTTP/1.1\r\n\r\n"))
        assertNull(HttpProxyServer.parsear("CONNECT :443 HTTP/1.1\r\n\r\n"))
        assertNull(HttpProxyServer.parsear("GET http://u:p@a.com/ HTTP/1.1\r\n\r\n"))
        assertNull(HttpProxyServer.parsear("GET ftp://a.com/ HTTP/1.1\r\n\r\n"))
        assertNull(HttpProxyServer.parsear("basura"))
        assertNull(HttpProxyServer.parsear("GET http://a.com/ SMTP/1\r\n\r\n"))
    }

    @Test
    fun solo_acepta_redes_privadas() {
        for (ok in listOf("192.168.43.7", "10.1.2.3", "172.20.0.5", "127.0.0.1")) assertTrue(ok, HttpProxyServer.permitido(InetAddress.getByName(ok)))
        for (no in listOf("8.8.8.8", "181.23.4.5", "172.32.0.1")) assertFalse(no, HttpProxyServer.permitido(InetAddress.getByName(no)))
    }

    @Test
    fun elige_la_direccion_del_hotspot() {
        val l = listOf(Pair("lo", "127.0.0.1"), Pair("rmnet_data0", "10.20.30.40"), Pair("tun0", "10.0.0.2"),
            Pair("wlan0", "192.168.43.1"), Pair("ap0", "192.168.43.1"), Pair("rndis0", "192.168.42.129"), Pair("eth0", "8.8.4.4"))
        assertEquals(listOf("192.168.43.1", "192.168.42.129"), HttpProxyServer.candidatas(l))
        assertTrue(HttpProxyServer.candidatas(emptyList()).isEmpty())
    }

    @Test
    fun cabecera_demasiado_grande_se_descarta() {
        val enorme = ("GET http://a.com/ HTTP/1.1\r\n" + "X: " + "a".repeat(20000)).byteArray()
        assertNull(HttpProxyServer.leerCabecera(enorme.inputStream()))
        assertNotNull(HttpProxyServer.leerCabecera("GET / HTTP/1.1\r\n\r\n".toByteArray().inputStream()))
    }

    private fun String.byteArray() = toByteArray()
}
