package com.zumo.vpn

import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * Rango de IP en el campo host de un servidor. En vez de un dominio o una IP fija se puede poner:
 *   104.16.0.0/24              (CIDR, de /16 a /32)
 *   104.16.1.10-104.16.1.80    (de una IP a otra)
 *   104.16.1.10-80             (lo mismo, cambiando solo el último número)
 * Antes de conectar, la app prueba las IP del rango (varias a la vez, en orden aleatorio) mandando el payload,
 * y se queda con la primera donde contesta el SSH. Esa IP se recuerda y la próxima vez se prueba primero.
 *
 * Ojo: el payload se manda a la IP, así que [host] en el payload pasa a ser esa IP. Si el servidor está
 * detrás de un CDN (Cloudflare, CloudFront), escribí el dominio a mano en el Host: del payload (y en sni con TLS).
 */
object Rangos {
    /** Máximo de IP que se prueban de un rango (un /22). Más que eso tardaría demasiado en el celular. */
    const val MAX_IPS = 1024
    const val HILOS = 24
    const val TIEMPO_TOTAL_MS = 30_000L

    private val RE_IP = Regex("^(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})$")

    private fun aNumero(ip: String): Long? {
        val m = RE_IP.matchEntire(ip.trim()) ?: return null
        var n = 0L
        for (g in m.groupValues.drop(1)) {
            val v = g.toInt()
            if (v > 255) return null
            n = (n shl 8) or v.toLong()
        }
        return n
    }

    fun aTexto(n: Long): String = (3 downTo 0).joinToString(".") { ((n shr (it * 8)) and 255).toString() }

    /** Primera y última IP del rango (inclusive), o null si el texto no es un rango válido o es demasiado grande. */
    fun limites(texto: String): Pair<Long, Long>? {
        val t = texto.trim()
        val (ini, fin) = when {
            "/" in t -> {
                val base = aNumero(t.substringBefore("/")) ?: return null
                val bits = t.substringAfter("/").toIntOrNull() ?: return null
                if (bits !in 16..32) return null
                val mascara = if (bits == 0) 0L else (0xFFFFFFFFL shl (32 - bits)) and 0xFFFFFFFFL
                val red = base and mascara
                var a = red
                var b = red or (mascara.inv() and 0xFFFFFFFFL)
                if (bits <= 30) { a += 1; b -= 1 }          // sin la dirección de red ni la de broadcast
                a to b
            }
            "-" in t -> {
                val izq = t.substringBefore("-").trim()
                val der = t.substringAfter("-").trim()
                val a = aNumero(izq) ?: return null
                val b = aNumero(der) ?: der.toIntOrNull()?.takeIf { it in 0..255 }?.let { (a and 0xFFFFFF00L) or it.toLong() } ?: return null
                a to b
            }
            else -> return null
        }
        if (fin < ini || fin - ini + 1 > MAX_IPS * 64L) return null      // /16 como máximo
        return ini to fin
    }

    fun esRango(texto: String): Boolean = limites(texto) != null

    /** Cuántas IP tiene el rango (sin recortar). */
    fun tamano(texto: String): Long = limites(texto)?.let { it.second - it.first + 1 } ?: 0L

    /**
     * Las IP que se van a probar, en orden aleatorio (así no todos los celulares golpean la misma IP).
     * Si el rango tiene más de [max], se toman [max] al azar, repartidas por todo el rango.
     */
    fun ips(texto: String, max: Int = MAX_IPS, azar: java.util.Random = java.util.Random()): List<String> {
        val (a, b) = limites(texto) ?: return emptyList()
        val total = b - a + 1
        if (total <= max) return (a..b).map { aTexto(it) }.shuffled(azar)
        val elegidas = HashSet<Long>()
        while (elegidas.size < max) elegidas.add(a + (azar.nextDouble() * total).toLong().coerceAtMost(total - 1))
        return elegidas.map { aTexto(it) }.shuffled(azar)
    }

    /**
     * Prueba las IP de [lista] con [probar] ([hilos] a la vez) y devuelve la primera que sirve, o null.
     * [primero] (la que funcionó la vez pasada) se prueba sola antes que las demás.
     * Corta si se encuentra una, si [cancelado] da true o si pasan [totalMs].
     */
    fun buscar(
        lista: List<String>, primero: String? = null, hilos: Int = HILOS, totalMs: Long = TIEMPO_TOTAL_MS,
        cancelado: () -> Boolean = { false }, progreso: (probadas: Int, total: Int) -> Unit = { _, _ -> },
        probar: (String) -> Boolean,
    ): String? {
        if (!primero.isNullOrBlank() && !cancelado()) {
            if (try { probar(primero) } catch (_: Exception) { false }) return primero
        }
        val resto = lista.filter { it != primero }
        if (resto.isEmpty()) return null
        val encontrada = AtomicReference<String?>(null)
        val parar = AtomicBoolean(false)
        val siguiente = AtomicInteger(0)
        val probadas = AtomicInteger(0)
        val limite = System.currentTimeMillis() + totalMs
        val hilosVivos = (1..hilos.coerceIn(1, resto.size)).map {
            Thread({
                while (!parar.get()) {
                    if (cancelado() || System.currentTimeMillis() > limite) { parar.set(true); break }
                    val i = siguiente.getAndIncrement()
                    if (i >= resto.size) break
                    val ip = resto[i]
                    val ok = try { probar(ip) } catch (_: Exception) { false }
                    progreso(probadas.incrementAndGet(), resto.size)
                    if (ok && encontrada.compareAndSet(null, ip)) { parar.set(true); break }
                }
            }, "zumo-rango").also { it.isDaemon = true; it.start() }
        }
        // Se espera a que alguno la encuentre o se terminen todas (los que sigan probando una IP lenta mueren solos).
        while (encontrada.get() == null && hilosVivos.any { it.isAlive }) {
            if (cancelado() || System.currentTimeMillis() > limite + 1000) { parar.set(true); break }
            try { Thread.sleep(50) } catch (_: InterruptedException) { parar.set(true); break }
        }
        parar.set(true)
        return encontrada.get()
    }

    /**
     * Prueba si en [c] (host = una IP del rango) contesta nuestro servidor: conecta, manda el payload y espera ver el
     * saludo "SSH-" en la respuesta. Sin payload, también vale un servidor BHTTP. No inicia sesión (no gasta intentos).
     */
    fun sondaSsh(c: Config, proteger: (Socket) -> Unit, conectarMs: Int = 2500, leerMs: Int = 3500): Boolean {
        val rapida = c.copy(conTimeout = conectarMs)
        try {
            Transport.abrirSocket(rapida, {}, proteger).use { s ->
                s.soTimeout = leerMs
                val out = s.getOutputStream()
                if (c.payload.isNotBlank()) {
                    for ((parte, espera) in Transport.partes(Transport.expandir(c.payload, c))) {
                        if (espera > 0) Thread.sleep(espera)
                        out.write(parte.toByteArray(Charsets.ISO_8859_1))
                        out.flush()
                    }
                }
                val inp = s.getInputStream()
                val visto = StringBuilder()
                val buf = ByteArray(2048)
                val fin = System.currentTimeMillis() + leerMs
                while (visto.length < 16384 && System.currentTimeMillis() < fin) {
                    val n = try { inp.read(buf) } catch (_: java.net.SocketTimeoutException) { break }
                    if (n < 0) break
                    visto.append(String(buf, 0, n, Charsets.ISO_8859_1))
                    if (visto.contains("SSH-")) return true
                }
            }
        } catch (_: Exception) { }
        if (c.payload.isBlank()) return try { Bhttp.sonda { Transport.abrirSocket(rapida, {}, proteger) } } catch (_: Exception) { false }
        return false
    }
}
