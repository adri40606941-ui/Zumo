package com.zumo.vpn

import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Proxy HTTP para compartir la VPN por el hotspot ("WiFi"): el otro celular lo pone en su WiFi
 * (Proxy manual: IP del hotspot y puerto) y su tráfico sale por el túnel de esta app.
 * Entiende CONNECT (HTTPS) y pedidos HTTP comunes; cada conexión sale por [abrir] (un canal del túnel SSH).
 * Solo acepta clientes de redes privadas (los del hotspot): nadie de afuera puede usarlo.
 */
class HttpProxyServer(private val puerto: Int, private val abrir: (String, Int) -> Salida?) {

    interface Salida {
        val entrada: InputStream
        val salida: OutputStream
        fun cerrar()
    }

    /** Lo que pide el cliente: [connect] = túnel HTTPS; si no, [cabecera] es el pedido reescrito para el servidor. */
    internal class Peticion(val connect: Boolean, val host: String, val puerto: Int, val cabecera: ByteArray)

    private var server: ServerSocket? = null
    private val pool = Executors.newCachedThreadPool()
    @Volatile private var running = false

    fun start(bind: InetAddress? = null) {
        val ss = ServerSocket()
        ss.reuseAddress = true
        ss.bind(java.net.InetSocketAddress(bind, puerto), 128)
        server = ss
        running = true
        Thread({
            while (running) {
                try {
                    val c = ss.accept()
                    if (!permitido(c.inetAddress)) { try { c.close() } catch (_: Exception) {}; continue }
                    pool.execute { atender(c) }
                } catch (e: Exception) {
                    if (!running) break
                }
            }
        }, "wifi-proxy-accept").start()
    }

    fun stop() {
        running = false
        try { server?.close() } catch (_: Exception) {}
        pool.shutdownNow()
    }

    private fun atender(c: Socket) {
        var s: Salida? = null
        try {
            c.tcpNoDelay = true
            c.soTimeout = 15000
            val inp = c.getInputStream()
            val out = c.getOutputStream()
            val (cabeza, sobra) = leerCabecera(inp) ?: return
            val p = parsear(cabeza)
            if (p == null) { responder(out, "400 Bad Request"); return }
            s = abrir(p.host, p.puerto)
            if (s == null) { responder(out, "502 Bad Gateway"); return }
            val remoto = s
            if (p.connect) {
                out.write("HTTP/1.1 200 Connection Established\r\n\r\n".toByteArray()); out.flush()
            } else {
                remoto.salida.write(p.cabecera)
            }
            if (sobra.isNotEmpty()) remoto.salida.write(sobra)
            remoto.salida.flush()
            c.soTimeout = 0

            val remotoFin = CountDownLatch(1)
            val clienteFin = CountDownLatch(1)
            pool.execute {
                // Todo el cuerpo en try/catch: pool.shutdownNow() (al apagar) interrumpe este hilo y no debe tumbar la app.
                try {
                    try { copiar(remoto.entrada, out) } catch (_: Exception) {}
                    try { c.shutdownOutput() } catch (_: Exception) {}
                    remotoFin.countDown()
                    if (!clienteFin.await(15, TimeUnit.SECONDS)) { try { c.close() } catch (_: Exception) {} }
                } catch (_: Throwable) {
                    try { c.close() } catch (_: Exception) {}
                }
            }
            try { copiar(inp, remoto.salida) } catch (_: Exception) {}
            clienteFin.countDown()
            try { remoto.salida.close() } catch (_: Exception) {}
            try { remotoFin.await(30, TimeUnit.SECONDS) } catch (_: InterruptedException) {}
        } catch (_: Exception) {
            // cliente caído o pedido roto: se cierra
        } finally {
            try { s?.cerrar() } catch (_: Exception) {}
            try { c.close() } catch (_: Exception) {}
        }
    }

    private fun responder(o: OutputStream, estado: String) {
        try { o.write("HTTP/1.1 $estado\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".toByteArray()); o.flush() } catch (_: Exception) {}
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

    companion object {
        private const val MAX_CABECERA = 16 * 1024

        /** Solo los del hotspot (redes privadas) o la propia app (pruebas); nunca una dirección pública. */
        fun permitido(a: InetAddress): Boolean = a.isSiteLocalAddress || a.isLoopbackAddress

        /** Lee hasta el fin de la cabecera (línea vacía). Devuelve (cabecera, bytes que ya llegaron después), o null. */
        internal fun leerCabecera(inp: InputStream): Pair<String, ByteArray>? {
            val buf = ByteArrayOutputStream()
            var ult = 0                       // últimos 4 bytes, para reconocer \r\n\r\n sin copiar el buffer
            var n = 0
            while (n < MAX_CABECERA) {
                val b = inp.read()
                if (b < 0) return null
                buf.write(b); n++
                ult = (ult shl 8) or b
                if (n >= 4 && ult == 0x0D0A0D0A) return Pair(String(buf.toByteArray(), Charsets.ISO_8859_1), ByteArray(0))
            }
            return null
        }

        private fun hostPuerto(s: String, porDefecto: Int): Pair<String, Int>? {
            if (s.isEmpty() || s.contains('@')) return null
            val host: String
            var p = porDefecto
            if (s.startsWith("[")) {                       // [IPv6]:puerto
                val f = s.indexOf(']')
                if (f < 0) return null
                host = s.substring(1, f)
                val resto = s.substring(f + 1)
                if (resto.isNotEmpty()) { if (!resto.startsWith(":")) return null; p = resto.substring(1).toIntOrNull() ?: return null }
            } else {
                val i = s.lastIndexOf(':')
                if (i >= 0) { host = s.substring(0, i); p = s.substring(i + 1).toIntOrNull() ?: return null } else host = s
            }
            if (host.isEmpty() || host.any { it.isWhitespace() } || p !in 1..65535) return null
            return Pair(host, p)
        }

        /** Entiende la cabecera del cliente. null si no es un pedido de proxy válido. */
        internal fun parsear(cabeza: String): Peticion? {
            val lineas = cabeza.split("\r\n")
            val primera = lineas[0].split(" ")
            if (primera.size != 3 || !primera[2].startsWith("HTTP/")) return null
            val metodo = primera[0].uppercase()
            val objetivo = primera[1]
            if (metodo == "CONNECT") {
                val (h, p) = hostPuerto(objetivo, 443) ?: return null
                return Peticion(true, h, p, ByteArray(0))
            }
            if (!objetivo.startsWith("http://", ignoreCase = true)) return null
            val sinEsquema = objetivo.substring(7)
            val corte = sinEsquema.indexOfFirst { it == '/' || it == '?' }
            val autoridad = if (corte < 0) sinEsquema else sinEsquema.substring(0, corte)
            var ruta = if (corte < 0) "/" else sinEsquema.substring(corte)
            if (ruta.startsWith("?")) ruta = "/$ruta"
            val (h, p) = hostPuerto(autoridad, 80) ?: return null
            val sb = StringBuilder("$metodo $ruta ${primera[2]}\r\n")
            for (l in lineas.drop(1)) {
                if (l.isEmpty()) continue
                val nombre = l.substringBefore(':').trim().lowercase()
                if (nombre in setOf("proxy-connection", "proxy-authorization", "connection", "keep-alive")) continue
                sb.append(l).append("\r\n")
            }
            sb.append("Connection: close\r\n\r\n")
            return Peticion(false, h, p, sb.toString().toByteArray(Charsets.ISO_8859_1))
        }

        /**
         * Direcciones del propio teléfono donde el otro celular puede encontrarlo (la del hotspot).
         * Se descartan datos móviles, túneles y bucle local. [interfaces] = (nombre, IPv4).
         */
        fun candidatas(interfaces: List<Pair<String, String>>): List<String> =
            interfaces.filter { (n, ip) ->
                val m = n.lowercase()
                !m.startsWith("lo") && !m.startsWith("tun") && !m.startsWith("rmnet") && !m.startsWith("ccmni") &&
                    !m.startsWith("dummy") && !m.startsWith("v4-") && !m.startsWith("clat") && ip.isNotBlank() &&
                    runCatching { InetAddress.getByName(ip).isSiteLocalAddress }.getOrDefault(false)
            }.map { it.second }.distinct()

        fun direccionesDelTelefono(): List<String> = try {
            val l = ArrayList<Pair<String, String>>()
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                if (!ni.isUp) continue
                for (a in ni.inetAddresses) if (a is java.net.Inet4Address) l.add(Pair(ni.name, a.hostAddress ?: ""))
            }
            candidatas(l)
        } catch (_: Exception) {
            emptyList()
        }
    }
}
