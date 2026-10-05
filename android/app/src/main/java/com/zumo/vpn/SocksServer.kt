package com.zumo.vpn

import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Proxy SOCKS5 local: cada conexión se reenvía por un canal direct-tcpip de la sesión SSH. */
class SocksServer(private val port: Int, private val tunel: () -> SshTunnel?) {
    private var server: ServerSocket? = null
    private val pool = Executors.newCachedThreadPool()
    @Volatile private var running = false

    fun start() {
        val ss = ServerSocket(port, 256, InetAddress.getByName("127.0.0.1"))
        ss.reuseAddress = true
        server = ss
        running = true
        Thread({
            while (running) {
                try {
                    val c = ss.accept()
                    pool.execute { atender(c) }
                } catch (e: Exception) {
                    if (!running) break
                }
            }
        }, "socks-accept").start()
    }

    fun stop() {
        running = false
        try { server?.close() } catch (_: Exception) {}
        pool.shutdownNow()
    }

    private fun leer(i: InputStream, n: Int): ByteArray {
        val b = ByteArray(n)
        var o = 0
        while (o < n) {
            val r = i.read(b, o, n - o)
            if (r < 0) throw java.io.EOFException()
            o += r
        }
        return b
    }

    private fun responder(o: OutputStream, rep: Int) {
        o.write(byteArrayOf(5, rep.toByte(), 0, 1, 0, 0, 0, 0, 0, 0))
        o.flush()
    }

    private fun atender(c: Socket) {
        var canal: com.jcraft.jsch.ChannelDirectTCPIP? = null
        try {
            c.tcpNoDelay = true
            c.soTimeout = 15000
            val inp = c.getInputStream()
            val out = c.getOutputStream()
            val g = leer(inp, 2)
            if (g[0].toInt() != 5) return
            leer(inp, g[1].toInt() and 0xff)
            out.write(byteArrayOf(5, 0)); out.flush()

            val h = leer(inp, 4)
            if (h[1].toInt() != 1) { responder(out, 7); return }   // solo CONNECT
            val host = when (h[3].toInt()) {
                1 -> leer(inp, 4).joinToString(".") { (it.toInt() and 0xff).toString() }
                3 -> String(leer(inp, leer(inp, 1)[0].toInt() and 0xff), Charsets.ISO_8859_1)
                4 -> InetAddress.getByAddress(leer(inp, 16)).hostAddress ?: ""
                else -> { responder(out, 8); return }
            }
            val pb = leer(inp, 2)
            val puerto = ((pb[0].toInt() and 0xff) shl 8) or (pb[1].toInt() and 0xff)

            val t = tunel()
            canal = t?.abrirCanal(host, puerto)
            if (canal == null) { responder(out, 1); return }
            val ri = canal.inputStream
            val ro = canal.outputStream
            try {
                canal.connect(12000)
            } catch (e: Exception) {
                responder(out, 5)   // conexión rechazada
                return
            }
            responder(out, 0)
            c.soTimeout = 0

            val remotoFin = CountDownLatch(1)
            val clienteFin = CountDownLatch(1)
            val ch = canal
            pool.execute {
                try { copiar(ri, out) } catch (_: Exception) {}
                // el servidor cerró: se avisa al cliente (FIN) y se le da unos segundos para cerrar
                try { c.shutdownOutput() } catch (_: Exception) {}
                remotoFin.countDown()
                if (!clienteFin.await(15, TimeUnit.SECONDS)) { try { c.close() } catch (_: Exception) {} }
            }
            try { copiar(inp, ro) } catch (_: Exception) {}
            clienteFin.countDown()
            try { ro.close() } catch (_: Exception) {}
            remotoFin.await(30, TimeUnit.SECONDS)
            ch.disconnect()
        } catch (e: Exception) {
            // conexión rechazada o caída: se cierra
        } finally {
            try { canal?.disconnect() } catch (_: Exception) {}
            try { c.close() } catch (_: Exception) {}
        }
    }

    private fun copiar(i: InputStream, o: OutputStream) {
        val b = ByteArray(16384)
        while (true) {
            val r = i.read(b)
            if (r < 0) break
            o.write(b, 0, r)
            o.flush()
        }
    }
}
