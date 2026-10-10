package com.zumo.vpn

import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * Cliente BHTTP v1 (el "BHTTP" de Zumo y de bilola-server): SSH dentro de pedidos binarios sobre TCP.
 *
 * Cada pedido: modo(1) + sesión(16) + secuencia(8) + largo(4) + cuerpo cifrado con XOR (flujo SHA-256).
 * Cada respuesta: estado(1) + largo(4) + cuerpo.
 *  - modo 0: sonda ("BHP1"), modo 1: abrir (seq 0, vacío) y subir datos (seq 0,1,2... uno por pedido),
 *  - modo 3: bajar en lote (tamaño(4) + cantidad(2)) → "cantidad" respuestas de estado 2 con largo(4)+datos,
 *  - modo 4: cerrar la sesión.
 * El servidor corta la conexión TCP después de abrir y ante cualquier error; por eso se abre aparte.
 */
object Bhttp {
    private const val SUBIDA_MAX = 16384
    private const val BAJADA_TAM = 16384
    private const val BAJADA_CANT = 4
    private const val INTENTOS = 4               // reintentos de un pedido antes de dar la sesión por perdida
    private const val PAUSA_REINTENTO_MS = 250L

    /** Flujo de claves: bloque = sid + modo + seq(8) + resp + contador(4); SHA-256; XOR cada 32 bytes. */
    fun xor(sid: ByteArray, modo: Int, seq: Long, resp: Boolean, datos: ByteArray, off: Int = 0, len: Int = datos.size - off): ByteArray {
        val out = ByteArray(len)
        val md = MessageDigest.getInstance("SHA-256")
        val blk = ByteArray(30)
        System.arraycopy(sid, 0, blk, 0, 16)
        blk[16] = modo.toByte()
        for (i in 0 until 8) blk[17 + i] = (seq ushr (56 - 8 * i)).toByte()
        blk[25] = if (resp) 1 else 0
        var i = 0
        var c = 0
        while (i < len) {
            blk[26] = (c ushr 24).toByte(); blk[27] = (c ushr 16).toByte(); blk[28] = (c ushr 8).toByte(); blk[29] = c.toByte()
            val k = md.digest(blk)
            val n = minOf(32, len - i)
            for (j in 0 until n) out[i + j] = (datos[off + i + j].toInt() xor k[j].toInt()).toByte()
            i += 32; c++
        }
        return out
    }

    internal fun pedido(modo: Int, sid: ByteArray, seq: Long, cuerpo: ByteArray = ByteArray(0), off: Int = 0, len: Int = cuerpo.size - off): ByteArray {
        val h = ByteArray(29 + len)
        h[0] = modo.toByte()
        System.arraycopy(sid, 0, h, 1, 16)
        for (i in 0 until 8) h[17 + i] = (seq ushr (56 - 8 * i)).toByte()
        h[25] = (len ushr 24).toByte(); h[26] = (len ushr 16).toByte(); h[27] = (len ushr 8).toByte(); h[28] = len.toByte()
        if (len > 0) System.arraycopy(xor(sid, modo, seq, false, cuerpo, off, len), 0, h, 29, len)
        return h
    }

    private class Respuesta(val estado: Int, val cuerpo: ByteArray)

    private fun leerExacto(i: InputStream, n: Int): ByteArray {
        val b = ByteArray(n)
        var m = 0
        while (m < n) {
            val r = i.read(b, m, n - m)
            if (r < 0) throw IOException("El servidor cerró la conexión")
            m += r
        }
        return b
    }

    private fun leerRespuesta(i: InputStream): Respuesta {
        val h = leerExacto(i, 5)
        val n = ((h[1].toInt() and 0xff) shl 24) or ((h[2].toInt() and 0xff) shl 16) or ((h[3].toInt() and 0xff) shl 8) or (h[4].toInt() and 0xff)
        if (n < 0 || n > (1 shl 20)) throw IOException("Respuesta BHTTP inválida")
        return Respuesta(h[0].toInt() and 0xff, leerExacto(i, n))
    }

    /** ¿Hay un servidor BHTTP en ese puerto? Un SSH normal habla primero ("SSH-"); uno BHTTP contesta
     *  a la sonda con estado 0 y "BHP1". Cualquier otra cosa o silencio → no es BHTTP. */
    fun sonda(abrir: () -> Socket): Boolean {
        val s = try { abrir() } catch (e: Exception) { return false }
        return try {
            s.soTimeout = 4000
            val sid = ByteArray(16).also { SecureRandom().nextBytes(it) }
            val sondaCuerpo = byteArrayOf('B'.code.toByte(), 'H'.code.toByte(), 'P'.code.toByte(), '1'.code.toByte(), 1, 0, 0, 0, 0, 0)
            s.getOutputStream().write(pedido(0, sid, 0, sondaCuerpo))
            s.getOutputStream().flush()
            val r = leerRespuesta(s.getInputStream())
            r.estado == 0 && r.cuerpo.size >= 4 && String(xor(sid, 0, 0, true, r.cuerpo), Charsets.ISO_8859_1).startsWith("BHP1")
        } catch (e: Exception) {
            false
        } finally {
            try { s.close() } catch (_: Exception) {}
        }
    }

    /** Abre una sesión BHTTP y la entrega como [Tunnel] (con un Socket "de mentira" que la cierra). */
    fun abrir(abrir: () -> Socket): Tunnel {
        val ses = Sesion(abrir)
        ses.iniciar()
        val sock = SocketBhttp(ses)
        return Tunnel(sock, ses.entrada, ses.salida)
    }

    /** Socket sin conectar que sólo existe para que JSch/SshTunnel tengan algo que cerrar. */
    private class SocketBhttp(private val ses: Sesion) : Socket() {
        override fun close() { ses.cerrar(); super.close() }
    }

    internal class Sesion(private val abrirSocket: () -> Socket) {
        private val sid = ByteArray(16).also { SecureRandom().nextBytes(it) }
        private var subida: Socket? = null
        private var bajada: Socket? = null
        private var seqSubida = 0L
        private var seqBajada = 0L
        private val lock = Object()
        private var buf = ByteArray(0)
        private var error: IOException? = null
        @Volatile private var cerrado = false

        val entrada = object : InputStream() {
            override fun read(): Int {
                val b = ByteArray(1)
                return if (read(b, 0, 1) < 0) -1 else b[0].toInt() and 0xff
            }
            override fun read(b: ByteArray, off: Int, len: Int): Int {
                if (len == 0) return 0
                synchronized(lock) {
                    while (buf.isEmpty() && error == null && !cerrado) lock.wait()
                    if (buf.isEmpty()) {
                        if (cerrado) return -1
                        throw error!!
                    }
                    val n = minOf(len, buf.size)
                    System.arraycopy(buf, 0, b, off, n)
                    buf = buf.copyOfRange(n, buf.size)
                    return n
                }
            }
            override fun available(): Int = synchronized(lock) { buf.size }
            override fun close() { cerrar() }
        }

        val salida = object : OutputStream() {
            override fun write(b: Int) { write(byteArrayOf(b.toByte()), 0, 1) }
            override fun write(b: ByteArray, off: Int, len: Int) {
                var p = off
                val fin = off + len
                while (p < fin) {
                    val n = minOf(SUBIDA_MAX, fin - p)
                    subir(b, p, n)
                    p += n
                }
            }
            override fun close() { cerrar() }
        }

        fun iniciar() {
            // abrir: el servidor cierra esa conexión TCP al responder
            val s = abrirSocket()
            try {
                s.soTimeout = 15000
                s.getOutputStream().write(pedido(1, sid, 0))
                s.getOutputStream().flush()
                val r = leerRespuesta(s.getInputStream())
                if (r.estado != 0) throw IOException("El servidor BHTTP rechazó la sesión: " + String(r.cuerpo, Charsets.ISO_8859_1).take(80))
            } finally {
                try { s.close() } catch (_: Exception) {}
            }
            val t = Thread({ bucleBajada() }, "bhttp-bajada")
            t.isDaemon = true
            t.start()
        }

        /** Un pedido y su respuesta por una conexión persistente; si el servidor la cerró (límite de
         *  pedidos por conexión, inactividad) o la red parpadeó, se abre otra y se repite hasta [INTENTOS] veces,
         *  esperando un poco más cada vez. */
        private fun pedirPor(get: () -> Socket?, set: (Socket?) -> Unit, datos: ByteArray, cant: Int): List<Respuesta> {
            var ultimo: Exception? = null
            repeat(INTENTOS) { n ->
                try {
                    if (n > 0) Thread.sleep(PAUSA_REINTENTO_MS * n)
                    var s = get()
                    if (s == null) {
                        s = abrirSocket(); s.soTimeout = 20000; set(s)
                    }
                    s.getOutputStream().write(datos)
                    s.getOutputStream().flush()
                    val i = s.getInputStream()
                    return List(cant) { leerRespuesta(i) }
                } catch (e: Exception) {
                    ultimo = e
                    try { get()?.close() } catch (_: Exception) {}
                    set(null)
                    if (cerrado) throw IOException("Sesión cerrada")
                }
            }
            throw IOException("BHTTP: ${ultimo?.message ?: "sin respuesta"}")
        }

        private fun subir(b: ByteArray, off: Int, len: Int) {
            synchronized(subidaLock) {
                if (cerrado) throw IOException("Sesión cerrada")
                val r = pedirPor({ subida }, { subida = it }, pedido(1, sid, seqSubida, b, off, len), 1)[0]
                if (r.estado != 0) throw IOException("BHTTP: " + String(r.cuerpo, Charsets.ISO_8859_1).take(80))
                seqSubida++
            }
        }
        private val subidaLock = Object()

        private fun bucleBajada() {
            var vacias = 0
            try {
                while (!cerrado) {
                    val cuerpo = byteArrayOf((BAJADA_TAM ushr 24).toByte(), (BAJADA_TAM ushr 16).toByte(), (BAJADA_TAM ushr 8).toByte(), BAJADA_TAM.toByte(), 0, BAJADA_CANT.toByte())
                    val rs = pedirPor({ bajada }, { bajada = it }, pedido(3, sid, seqBajada, cuerpo), BAJADA_CANT)
                    var total = 0
                    for ((k, r) in rs.withIndex()) {
                        if (r.estado != 2 || r.cuerpo.size < 4) {
                            throw IOException("BHTTP: " + String(r.cuerpo, Charsets.ISO_8859_1).take(80).ifBlank { "sesión terminada" })
                        }
                        val n = ((r.cuerpo[0].toInt() and 0xff) shl 24) or ((r.cuerpo[1].toInt() and 0xff) shl 16) or
                            ((r.cuerpo[2].toInt() and 0xff) shl 8) or (r.cuerpo[3].toInt() and 0xff)
                        if (n < 0 || n > r.cuerpo.size - 4) throw IOException("BHTTP: respuesta inválida")
                        if (n > 0) {
                            val p = xor(sid, 3, seqBajada + k, true, r.cuerpo, 4, n)
                            synchronized(lock) { buf += p; lock.notifyAll() }
                            total += n
                        }
                    }
                    seqBajada += BAJADA_CANT
                    if (total == 0) {
                        vacias++
                        // en reposo se pregunta cada vez menos seguido (menos batería y datos)
                        if (vacias > 3) Thread.sleep(if (vacias > 600) 80 else if (vacias > 100) 40 else 15)
                    } else vacias = 0
                }
            } catch (e: Exception) {
                synchronized(lock) {
                    if (!cerrado && error == null) error = (e as? IOException) ?: IOException(e.toString())
                    lock.notifyAll()
                }
            }
        }

        fun cerrar() {
            synchronized(lock) {
                if (cerrado) return
                cerrado = true
                lock.notifyAll()
            }
            // modo 4 avisa al servidor para liberar la sesión (cuenta para el límite de conexiones del usuario)
            val t = Thread {
                try {
                    val s = abrirSocket()
                    s.soTimeout = 3000
                    s.getOutputStream().write(pedido(4, sid, 0))
                    s.getOutputStream().flush()
                    try { leerRespuesta(s.getInputStream()) } catch (_: Exception) {}
                    s.close()
                } catch (_: Exception) {}
            }
            t.isDaemon = true
            t.start()
            try { subida?.close() } catch (_: Exception) {}
            try { bajada?.close() } catch (_: Exception) {}
        }
    }
}
