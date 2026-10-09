package com.zumo.vpn

import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Callable
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.ThreadLocalRandom
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/** Proxy SOCKS5 local: cada conexión se reenvía por un canal direct-tcpip de la sesión SSH. */
class SocksServer(private val port: Int, private val tunel: () -> SshTunnel?) {
    private companion object {
        const val DNS_ESPERA_MS = 3500      // lo máximo que se espera la respuesta de un DNS
    }

    private var server: ServerSocket? = null
    private val pool = Executors.newCachedThreadPool()
    @Volatile private var running = false

    private class EnCache(val ips: List<String>, val expira: Long)
    private val cacheDns = ConcurrentHashMap<String, EnCache>()
    private val dnsCaido = ConcurrentHashMap<String, Long>()    // lista de DNS -> hasta cuándo no se insiste con ella

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
            // Con un DNS elegido para este servidor, el nombre se resuelve con ese DNS (por el túnel); si no
            // se puede, se le pasa el nombre al servidor SSH y él lo resuelve, como siempre.
            val destino = if (t != null) (resolverConDns(t, host) ?: host) else host
            canal = t?.abrirCanal(destino, puerto)
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
                // Nota: pool.shutdownNow() (al desconectar la VPN) interrumpe este hilo si está
                // en medio del tráfico; sin capturar InterruptedException acá, esa excepción se
                // escapa sin control y tumba toda la app. Por eso TODO el cuerpo va en try/catch.
                try {
                    try { copiar(ri, out) } catch (_: Exception) {}
                    // el servidor cerró: se avisa al cliente (FIN) y se le da unos segundos para cerrar
                    try { c.shutdownOutput() } catch (_: Exception) {}
                    remotoFin.countDown()
                    if (!clienteFin.await(15, TimeUnit.SECONDS)) { try { c.close() } catch (_: Exception) {} }
                } catch (_: InterruptedException) {
                    try { c.close() } catch (_: Exception) {}
                } catch (_: Throwable) {
                    try { c.close() } catch (_: Exception) {}
                }
            }
            try { copiar(inp, ro) } catch (_: Exception) {}
            clienteFin.countDown()
            try { ro.close() } catch (_: Exception) {}
            try { remotoFin.await(30, TimeUnit.SECONDS) } catch (_: InterruptedException) {}
            ch.disconnect()
        } catch (e: Exception) {
            // conexión rechazada o caída: se cierra
        } finally {
            try { canal?.disconnect() } catch (_: Exception) {}
            try { c.close() } catch (_: Exception) {}
        }
    }

    /**
     * La IP de [nombre] según el DNS elegido para el servidor de [t] (Google, Cloudflare o uno a mano), o null si
     * ese servidor no tiene DNS elegido o no se pudo resolver (entonces lo resuelve el servidor SSH).
     */
    private fun resolverConDns(t: SshTunnel, nombre: String): String? {
        val dns = t.dns
        if (dns.isEmpty() || Dns.ipv4(nombre) || nombre.contains(':')) return null
        val lista = dns.joinToString(",")
        val ahora = System.currentTimeMillis()
        if ((dnsCaido[lista] ?: 0L) > ahora) return null         // ese DNS no contestó hace poco: no se pierde tiempo
        val clave = lista + "|" + nombre.lowercase()
        cacheDns[clave]?.let { if (it.expira > ahora) return it.ips.first() }
        var alguienContesto = false
        for (d in dns) {
            val r = consultarPorTunel(t, d, nombre) ?: continue      // sin respuesta: se prueba el siguiente DNS
            alguienContesto = true
            if (r.ips.isNotEmpty()) {
                val ttlMs = r.ttl.coerceIn(30, 300) * 1000L
                if (cacheDns.size > 4000) cacheDns.clear()
                cacheDns[clave] = EnCache(r.ips, ahora + ttlMs)
                return r.ips.first()
            }
            break      // contestó que no hay IPv4 para ese nombre: no tiene sentido preguntarle al otro
        }
        if (!alguienContesto) {
            dnsCaido[lista] = ahora + 60_000L
            Registro.add("DNS ${Dns.etiqueta(lista)} no contesta; por 1 minuto se usa el del servidor")
        }
        return null
    }

    /** Una pregunta DNS por TCP al puerto 53 de [dns], por un canal del túnel. null si no contestó a tiempo. */
    private fun consultarPorTunel(t: SshTunnel, dns: String, nombre: String): DnsConsulta.Respuesta? {
        val canalRef = AtomicReference<com.jcraft.jsch.ChannelDirectTCPIP?>(null)
        var tarea: java.util.concurrent.Future<DnsConsulta.Respuesta?>? = null
        return try {
            tarea = pool.submit(Callable<DnsConsulta.Respuesta?> {
                val c = t.abrirCanal(dns, 53) ?: return@Callable null
                canalRef.set(c)
                val entrada = c.inputStream
                val salida = c.outputStream
                c.connect(DNS_ESPERA_MS)
                val id = ThreadLocalRandom.current().nextInt(0x10000)
                val q = DnsConsulta.armar(nombre, id) ?: return@Callable null
                salida.write(byteArrayOf((q.size shr 8).toByte(), q.size.toByte()) + q)
                salida.flush()
                val cab = leer(entrada, 2)
                val largo = ((cab[0].toInt() and 0xff) shl 8) or (cab[1].toInt() and 0xff)
                if (largo < 12 || largo > 4096) return@Callable null
                DnsConsulta.leer(leer(entrada, largo), id)
            })
            tarea.get(DNS_ESPERA_MS + 500L, TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            try { tarea?.cancel(true) } catch (_: Exception) {}
            null
        } finally {
            try { canalRef.get()?.disconnect() } catch (_: Exception) {}
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
