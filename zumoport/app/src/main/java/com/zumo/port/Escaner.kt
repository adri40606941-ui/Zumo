package com.zumo.port

import java.io.ByteArrayOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.security.cert.X509Certificate
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.Semaphore
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager

/** Un puerto abierto en un equipo, con lo que se pudo averiguar de él. */
class Hallazgo(
    val equipo: String,          // lo que se escribió: IP o dominio
    val ip: String,              // la IP a la que se conectó
    val puerto: Int,
    val tipo: Tipo,
    val http: Int = 0,           // código de respuesta HTTP (0 si no se probó o no contestó)
    val detalle: String = "",    // "nginx", "SSH-2.0-OpenSSH...", "redirige a https://..."
) {
    enum class Tipo { WEB, TLS_WEB, TLS_SIN_WEB, BANNER, ABIERTO }

    /** Respuesta web correcta (2xx o 3xx). */
    val webOk: Boolean get() = http in 200..399

    fun resumen(): String {
        val t = when (tipo) {
            Tipo.WEB -> if (http > 0) "HTTP $http" else "HTTP"
            Tipo.TLS_WEB -> if (http > 0) "HTTPS $http" else "HTTPS"
            Tipo.TLS_SIN_WEB -> "TLS"
            Tipo.BANNER -> "banner"
            Tipo.ABIERTO -> "abierto"
        }
        return if (detalle.isBlank()) t else "$t · $detalle"
    }
}

/** Cuántos hilos y cuánto esperar por puerto. Suave cuida la batería y la red; Rápido barre más por segundo. */
enum class Velocidad(val titulo: String, val hilos: Int, val esperaMs: Int) {
    SUAVE("Suave", 40, 1500),
    NORMAL("Normal", 150, 1000),
    RAPIDA("Rápida", 400, 600),
}

class Opciones(
    val velocidad: Velocidad = Velocidad.NORMAL,
    val verificarWeb: Boolean = true,
    val leerBanner: Boolean = true,
)

class Escaner(private val resolver: (String) -> String? = { n -> resolverIpv4(n) }) {
    private val cancelado = AtomicBoolean(false)

    fun cancelar() { cancelado.set(true) }

    /**
     * Prueba cada puerto de cada equipo. [alHallar] se llama (desde varios hilos) por cada puerto abierto y
     * [alAvanzar] con (hechos, total) cada tanto. Vuelve cuando termina o cuando se cancela.
     */
    fun escanear(
        equipos: Sequence<String>,
        puertos: List<Int>,
        opc: Opciones,
        total: Long,
        alHallar: (Hallazgo) -> Unit,
        alAvanzar: (Long, Long) -> Unit,
    ) {
        cancelado.set(false)
        val pool = Executors.newFixedThreadPool(opc.velocidad.hilos)
        val cupo = Semaphore(opc.velocidad.hilos * 4)
        val hechos = AtomicLong(0)
        val totalTareas = total * puertos.size
        val ips = ConcurrentHashMap<String, String>()
        var ultimoAviso = 0L
        try {
            for (equipo in equipos) {
                for (puerto in puertos) {
                    if (cancelado.get()) break
                    cupo.acquire()
                    if (cancelado.get()) { cupo.release(); break }
                    pool.execute {
                        try {
                            if (!cancelado.get()) {
                                val ip = ips.getOrPut(equipo) { resolver(equipo) ?: "" }
                                if (ip.isNotEmpty()) sondear(equipo, ip, puerto, opc)?.let(alHallar)
                            }
                        } catch (_: Exception) {
                        } finally {
                            hechos.incrementAndGet()
                            cupo.release()
                        }
                    }
                    val ahora = System.currentTimeMillis()
                    if (ahora - ultimoAviso > 150) { ultimoAviso = ahora; alAvanzar(hechos.get(), totalTareas) }
                }
                if (cancelado.get()) break
            }
        } finally {
            pool.shutdown()
            if (cancelado.get()) pool.shutdownNow()
            try { pool.awaitTermination(1, TimeUnit.HOURS) } catch (_: InterruptedException) { pool.shutdownNow() }
            alAvanzar(hechos.get(), totalTareas)
        }
    }

    /**
     * Prueba UN puerto que ya se sabe abierto, para ver qué responde por HTTP: HEAD / con el nombre en Host (y en SNI si es
     * HTTPS). Los puertos que no se reconocen como web (un puerto raro de un panel, por ejemplo) se prueban como HTTP y, si no
     * contestan HTTP, como HTTPS; lo que no habla web devuelve su banner (SSH, FTP, SMTP…) o solo "abierto".
     */
    fun probar(equipo: String, ip: String, puerto: Int, opc: Opciones = Opciones()): Hallazgo? {
        if (Puertos.esTls(puerto) || Puertos.esWeb(puerto) || puerto in PUERTOS_CON_SALUDO) return sondear(equipo, ip, puerto, opc)
        try {
            val s = Socket()
            try {
                s.tcpNoDelay = true
                s.connect(InetSocketAddress(ip, puerto), opc.velocidad.esperaMs)
                s.soTimeout = (opc.velocidad.esperaMs * 2).coerceIn(1000, 4000)
                val r = pedirWeb(s, equipo)
                if (r != null) return Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.WEB, r.codigo, r.detalle)
            } finally { try { s.close() } catch (_: Exception) {} }
        } catch (_: Exception) { }
        try {
            val s = Socket()
            try {
                s.tcpNoDelay = true
                s.connect(InetSocketAddress(ip, puerto), opc.velocidad.esperaMs)
                s.soTimeout = (opc.velocidad.esperaMs * 2).coerceIn(1000, 4000)
                val h = sondearTls(equipo, ip, puerto, s)
                if (h.tipo == Hallazgo.Tipo.TLS_WEB || h.tipo == Hallazgo.Tipo.TLS_SIN_WEB) return h
            } finally { try { s.close() } catch (_: Exception) {} }
        } catch (_: Exception) { }
        return sondear(equipo, ip, puerto, opc)
    }

    private fun sondear(equipo: String, ip: String, puerto: Int, opc: Opciones): Hallazgo? {
        val s = Socket()
        try {
            s.tcpNoDelay = true
            s.connect(InetSocketAddress(ip, puerto), opc.velocidad.esperaMs)
        } catch (_: Exception) {
            try { s.close() } catch (_: Exception) {}
            return null
        }
        try {
            val espera = (opc.velocidad.esperaMs * 2).coerceIn(1000, 4000)
            s.soTimeout = espera
            if (opc.verificarWeb && Puertos.esTls(puerto)) return sondearTls(equipo, ip, puerto, s)
            if (opc.verificarWeb && Puertos.esWeb(puerto)) {
                val r = pedirWeb(s, equipo) ?: pedirWebConGet(equipo, ip, puerto, opc)
                if (r != null) return Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.WEB, r.codigo, r.detalle)
                return Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.ABIERTO)
            }
            if (opc.leerBanner && puerto in PUERTOS_CON_SALUDO) {
                s.soTimeout = 900
                val b = leerSaludo(s)
                if (b.isNotEmpty()) return Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.BANNER, 0, b)
            }
            return Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.ABIERTO)
        } catch (_: Exception) {
            return Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.ABIERTO)
        } finally {
            try { s.close() } catch (_: Exception) {}
        }
    }

    private fun sondearTls(equipo: String, ip: String, puerto: Int, base: Socket): Hallazgo {
        var ss: SSLSocket? = null
        try {
            ss = (CONTEXTO_TLS.socketFactory.createSocket(base, if (Objetivos.esIp(equipo)) ip else equipo, puerto, true)) as SSLSocket
            if (!Objetivos.esIp(equipo)) {
                val p = ss.sslParameters
                p.serverNames = listOf(SNIHostName(equipo))
                ss.sslParameters = p
            }
            ss.startHandshake()
        } catch (_: Exception) {
            return Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.ABIERTO, 0, "no habla TLS")
        }
        return try {
            val r = pedirWeb(ss, equipo)
            if (r != null) Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.TLS_WEB, r.codigo, r.detalle)
            else Hallazgo(equipo, ip, puerto, Hallazgo.Tipo.TLS_SIN_WEB)
        } finally {
            try { ss.close() } catch (_: Exception) {}
        }
    }

    class RespuestaWeb(val codigo: Int, val detalle: String)

    /** Manda un HEAD y lee la línea de estado y las cabeceras que sirven (Server, Location). */
    private fun pedirWeb(s: Socket, equipo: String, metodo: String = "HEAD"): RespuestaWeb? {
        val host = if (Objetivos.esIp(equipo)) "localhost" else equipo
        val agente = if (metodo == "HEAD") "ZumoPort/1.0" else "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
        val pedido = "$metodo / HTTP/1.1\r\nHost: $host\r\nUser-Agent: $agente\r\nAccept: */*\r\nConnection: close\r\n\r\n"
        s.getOutputStream().write(pedido.toByteArray(Charsets.ISO_8859_1))
        s.getOutputStream().flush()
        val buf = ByteArrayOutputStream()
        val tmp = ByteArray(1024)
        val ins = s.getInputStream()
        while (buf.size() < 8192) {
            val n = try { ins.read(tmp) } catch (_: Exception) { -1 }
            if (n <= 0) break
            buf.write(tmp, 0, n)
            if (String(buf.toByteArray(), Charsets.ISO_8859_1).contains("\r\n\r\n")) break
        }
        return leerCabecera(String(buf.toByteArray(), Charsets.ISO_8859_1))
    }

    /** Algunos servidores o redes cortan el HEAD: se reintenta con un GET normal en otra conexión. */
    private fun pedirWebConGet(equipo: String, ip: String, puerto: Int, opc: Opciones): RespuestaWeb? {
        val s = Socket()
        return try {
            s.tcpNoDelay = true
            s.connect(InetSocketAddress(ip, puerto), opc.velocidad.esperaMs)
            s.soTimeout = (opc.velocidad.esperaMs * 2).coerceIn(1000, 4000)
            pedirWeb(s, equipo, "GET")
        } catch (_: Exception) { null } finally { try { s.close() } catch (_: Exception) {} }
    }

    private fun leerSaludo(s: Socket): String {
        val tmp = ByteArray(200)
        val n = try { s.getInputStream().read(tmp) } catch (_: Exception) { -1 }
        if (n <= 0) return ""
        return String(tmp, 0, n, Charsets.ISO_8859_1).lineSequence().firstOrNull().orEmpty().filter { it.code in 32..126 }.trim().take(80)
    }

    companion object {
        val PUERTOS_CON_SALUDO = setOf(21, 22, 25, 110, 143, 465, 587, 993, 995, 3306, 5900)

        /** Un contexto TLS que acepta cualquier certificado: acá solo se mira si el puerto contesta, no se envía nada privado. */
        private val CONTEXTO_TLS: SSLContext by lazy {
            val todos = object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<X509Certificate>?, authType: String?) {}
                override fun checkServerTrusted(chain: Array<X509Certificate>?, authType: String?) {}
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            }
            SSLContext.getInstance("TLS").also { it.init(null, arrayOf(todos), null) }
        }

        /** Primera IPv4 del nombre (o la misma IP si ya lo es). null si no se puede resolver. */
        fun resolverIpv4(nombre: String): String? {
            if (Objetivos.ipv4(nombre) != null) return nombre
            return try {
                val todas = java.net.InetAddress.getAllByName(nombre)
                (todas.firstOrNull { it is java.net.Inet4Address } ?: todas.firstOrNull())?.hostAddress
            } catch (_: Exception) { null }
        }

        /** Lee "HTTP/1.1 301 Moved" + cabeceras. null si no parece una respuesta HTTP. */
        fun leerCabecera(texto: String): RespuestaWeb? {
            val lineas = texto.split("\r\n")
            val estado = lineas.firstOrNull().orEmpty()
            if (!estado.startsWith("HTTP/")) return null
            val codigo = estado.split(" ").getOrNull(1)?.toIntOrNull() ?: return null
            var servidor = ""
            var destino = ""
            for (l in lineas.drop(1)) {
                val i = l.indexOf(':')
                if (i <= 0) continue
                val k = l.substring(0, i).trim().lowercase()
                val v = l.substring(i + 1).trim()
                if (k == "server") servidor = v.take(40)
                if (k == "location") destino = v.take(60)
            }
            val detalle = when {
                destino.isNotEmpty() && servidor.isNotEmpty() -> "$servidor → $destino"
                destino.isNotEmpty() -> "→ $destino"
                else -> servidor
            }
            return RespuestaWeb(codigo, detalle)
        }
    }
}
