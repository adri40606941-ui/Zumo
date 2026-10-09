package com.zumo.vpn

import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.io.PushbackInputStream
import java.net.InetSocketAddress
import java.net.Socket
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/** Socket ya conectado (y con el payload / WebSocket negociado) listo para hablar SSH. */
class Tunnel(val socket: Socket, val input: InputStream, val output: OutputStream)

/** Registra en un BufferCrudo los primeros bytes que pasan por el stream, tal cual, antes de
 *  que el payload o JSch los toquen. Sirve para ver si lo que entra/sale es el protocolo SSH
 *  posta o viene envuelto en algo (WebSocket, una respuesta HTTP mal cortada, etc.). No cambia
 *  el comportamiento del stream, solo mira lo que pasa. */
private class StreamEspia(private val base: InputStream, private val destino: BufferCrudo) : InputStream() {
    override fun read(): Int {
        val v = base.read()
        if (v >= 0) destino.agregar(byteArrayOf(v.toByte()), 0, 1)
        return v
    }
    override fun read(b: ByteArray, off: Int, len: Int): Int {
        val n = base.read(b, off, len)
        if (n > 0) destino.agregar(b, off, n)
        return n
    }
}

private class StreamEspiaSalida(private val base: OutputStream, private val destino: BufferCrudo) : OutputStream() {
    override fun write(b: Int) {
        base.write(b)
        destino.agregar(byteArrayOf(b.toByte()), 0, 1)
    }
    override fun write(b: ByteArray, off: Int, len: Int) {
        base.write(b, off, len)
        destino.agregar(b, off, len)
    }
    override fun flush() = base.flush()
    override fun close() = base.close()
}

/**
 * Después del primer write() (la línea "SSH-2.0-..." con la que JSch se identifica), espera un
 * toque antes de dejar pasar lo que sigue (el primer paquete binario, KEXINIT). JSch manda esa
 * línea y el paquete binario en dos escrituras seguidas sin pausa, y si llegan pegadas en el
 * mismo paquete de red, algunos de estos proxys de payload (son scripts caseros) no separan bien
 * dónde termina el texto y empieza lo binario, y el servidor real termina viendo una
 * identificación "corrupta". Forzar un corte imita lo que hacen clientes como HTTP Custom.
 */
private class PrimerCorte(private val base: OutputStream) : OutputStream() {
    private var primero = true
    private fun despuesDelPrimero() {
        if (!primero) return
        primero = false
        try { base.flush() } catch (_: Exception) {}
        try { Thread.sleep(120) } catch (_: InterruptedException) { Thread.currentThread().interrupt() }
    }
    override fun write(b: Int) { base.write(b); despuesDelPrimero() }
    override fun write(b: ByteArray, off: Int, len: Int) { base.write(b, off, len); despuesDelPrimero() }
    override fun flush() = base.flush()
    override fun close() = base.close()
}

object Transport {

    /** Envuelve un OutputStream para separar, con una pequeña pausa, su primera escritura del
     *  resto (ver [PrimerCorte]). Lo usa SshTunnel para el stream que le entrega a JSch. */
    fun primerCorte(out: OutputStream): OutputStream = PrimerCorte(out)

    /** TCP (y TLS si se pidió) hacia el servidor, ya sacado de la VPN con [proteger]. */
    internal fun abrirSocket(c: Config, etapa: (String) -> Unit = {}, proteger: (Socket) -> Unit = {}): Socket {
        var s = Socket()
        // Hay que sacar esta conexión de la VPN antes de que exista el túnel (TUN): si no, el
        // propio tráfico SSH que sostiene la VPN entraría a la VPN y se cortaría en bucle.
        proteger(s)
        s.tcpNoDelay = true
        s.keepAlive = true
        s.connect(InetSocketAddress(c.host, c.sshPort), c.conTimeout)
        if (c.tls) {
            etapa("Estableciendo canal seguro")
            val sniName = c.sni.ifBlank { c.host }
            val ss = (SSLSocketFactory.getDefault() as SSLSocketFactory).createSocket(s, sniName, c.sshPort, true) as SSLSocket
            val p = ss.sslParameters
            p.serverNames = listOf(SNIHostName(sniName))
            ss.sslParameters = p
            ss.soTimeout = 15000
            ss.startHandshake()
            s = ss
        }
        return s
    }

    /** Conecta por TCP (y TLS si se pidió). Con payload: lo envía y consume las respuestas HTTP
     *  (WebSocket / proxy). Sin payload: detecta por el propio puerto si hay un servidor BHTTP
     *  (lo reconoce por su respuesta) y lo usa; si no, SSH directo. */
    fun connect(c: Config, etapa: (String) -> Unit = {}, proteger: (Socket) -> Unit = {}): Tunnel {
        etapa("Conectando al servidor")
        if (c.payload.isBlank()) {
            etapa("Detectando tipo de servidor")
            if (Bhttp.sonda { abrirSocket(c, {}, proteger) }) {
                etapa("Abriendo sesión BHTTP")
                return Bhttp.abrir { abrirSocket(c, {}, proteger) }
            }
        }
        var s = abrirSocket(c, etapa, proteger)
        val pin = PushbackInputStream(StreamEspia(s.getInputStream(), CrudoDebug.entrada), 8192)
        val salida = StreamEspiaSalida(s.getOutputStream(), CrudoDebug.salida)
        if (c.payload.isNotBlank()) {
            etapa("Enviando solicitud")
            s.soTimeout = 15000
            for ((parte, espera) in partes(expandir(c.payload, c))) {
                if (espera > 0) Thread.sleep(espera)
                salida.write(parte.toByteArray(Charsets.ISO_8859_1))
                salida.flush()
            }
            etapa("Esperando respuesta del servidor")
            leerRespuestasHttp(pin)
            s.soTimeout = 0
        }
        return Tunnel(s, pin, salida)
    }

    /** Base64 sin depender de android.util (para poder probarlo en la JVM). */
    fun base64(b: ByteArray): String {
        val t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        val sb = StringBuilder()
        var i = 0
        while (i < b.size) {
            val n = minOf(3, b.size - i)
            var v = 0
            for (k in 0 until 3) v = (v shl 8) or (if (k < n) b[i + k].toInt() and 0xff else 0)
            for (k in 0 until 4) sb.append(if (k <= n) t[(v shr (18 - 6 * k)) and 63] else '=')
            i += 3
        }
        return sb.toString()
    }

    /** Cuenta de intentos: [rotate=a;b;c] toma el siguiente valor en cada conexión. */
    private val rotador = java.util.concurrent.atomic.AtomicInteger(0)
    private const val UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
    private val RE_ROTATE = Regex("\\[rotate=([^\\]]*)\\]", RegexOption.IGNORE_CASE)
    private val RE_RANDOM = Regex("\\[(?:random|rand)=([^\\]]*)\\]", RegexOption.IGNORE_CASE)
    private val RE_CRLF_N = Regex("\\[crlf\\*(\\d{1,2})\\]", RegexOption.IGNORE_CASE)
    private val RE_LF_N = Regex("\\[lf\\*(\\d{1,2})\\]", RegexOption.IGNORE_CASE)

    private fun opciones(s: String) = s.split(';', ',', '|').map { it.trim() }.filter { it.isNotEmpty() }

    /**
     * Reemplaza los comodines del payload (iguales a los de HTTP Custom / HTTP Injector):
     *  [host] [port] [host_port] [ssh_host] [ssh_port] [sni] [protocol] [method] [ua]
     *  [crlf] [cr] [lf] [lfcr] [tab] [crlf*N] [lf*N]
     *  [rotate=a;b;c]  un valor distinto en cada conexión, en orden (a, b, c, a...)
     *  [random=a;b;c]  un valor al azar
     * Los separadores de partes ([split], [instant_split], [delay_split], [split_delay=ms]) los lee [partes].
     * [vuelta] es el número de intento (para [rotate]); por defecto sube solo en cada conexión.
     */
    fun expandir(p: String, c: Config, vuelta: Int = rotador.getAndIncrement()): String {
        val v = vuelta and Int.MAX_VALUE
        return p
            .replace("\\r", "\r").replace("\\n", "\n")
            .let { RE_ROTATE.replace(it) { m -> opciones(m.groupValues[1]).let { o -> if (o.isEmpty()) "" else o[v % o.size] } } }
            .let { RE_RANDOM.replace(it) { m -> opciones(m.groupValues[1]).let { o -> if (o.isEmpty()) "" else o[java.util.concurrent.ThreadLocalRandom.current().nextInt(o.size)] } } }
            .let { RE_CRLF_N.replace(it) { m -> "\r\n".repeat(m.groupValues[1].toInt()) } }
            .let { RE_LF_N.replace(it) { m -> "\n".repeat(m.groupValues[1].toInt()) } }
            .replace("[host_port]", "${c.host}:${c.sshPort}")
            .replace("[ssh_host]", c.host).replace("[ssh_port]", c.sshPort.toString())
            .replace("[host]", c.host)
            .replace("[port]", c.sshPort.toString())
            .replace("[sni]", c.sni.ifBlank { c.host })
            .replace("[protocol]", "HTTP/1.1")
            .replace("[method]", "GET")
            .replace("[ua]", UA)
            .replace("[lfcr]", "\n\r")
            .replace("[crlf]", "\r\n").replace("[cr]", "\r").replace("[lf]", "\n").replace("[tab]", "\t")
    }

    private val RE_SEPARADOR = Regex("\\[(instant_split|delay_split|split_delay=\\d{1,5}|split)\\]", RegexOption.IGNORE_CASE)
    private const val ESPERA_SPLIT = 150L        // pausa entre partes ([split] e [instant_split] siempre esperaron esto: no se cambia)
    private const val ESPERA_DELAY_SPLIT = 1500L

    /** Las partes del payload, cada una con cuánto esperar antes de mandarla (ms). La primera sale sin espera. */
    fun partes(p: String): List<Pair<String, Long>> {
        val out = ArrayList<Pair<String, Long>>()
        var desde = 0
        var espera = 0L
        for (m in RE_SEPARADOR.findAll(p)) {
            val trozo = p.substring(desde, m.range.first)
            if (trozo.isNotEmpty()) { out.add(trozo to (if (out.isEmpty()) 0L else espera)) }
            val k = m.groupValues[1].lowercase()
            espera = when {
                k == "delay_split" -> ESPERA_DELAY_SPLIT
                k.startsWith("split_delay=") -> k.substringAfter('=').toLong().coerceIn(0L, 10000L)
                else -> ESPERA_SPLIT
            }
            desde = m.range.last + 1
        }
        val resto = p.substring(desde)
        if (resto.isNotEmpty()) out.add(resto to (if (out.isEmpty()) 0L else espera))
        return if (out.isEmpty()) listOf(p to 0L) else out
    }

    /** [split] y compañía envían el payload en partes. */
    fun partir(p: String): List<String> = partes(p).map { it.first }

    /**
     * Lee lo que el servidor conteste al payload. Es tolerante, como HTTP Custom: las respuestas HTTP
     * "de relleno" (200, 101, 400, 403...) a los pedidos señuelo se consumen y se ignoran, sin importar
     * cuántas vengan encadenadas (algunos payloads mandan varios pedidos señuelo, cada uno con su propia
     * respuesta). Termina recién cuando lo siguiente ya no es HTTP (ahí empieza el banner SSH-2.0...).
     * Antes cortaba apenas veía un 101, dejando sin consumir cualquier respuesta señuelo siguiente.
     */
    private fun leerRespuestasHttp(pin: PushbackInputStream) {
        var ultima = ""
        repeat(12) {
            val head = ByteArray(5)
            var n = 0
            while (n < head.size) {
                val r = try { pin.read(head, n, head.size - n) } catch (e: java.net.SocketTimeoutException) {
                    throw IOException(if (ultima.isEmpty()) "El servidor no respondió" else "El servidor respondió: $ultima")
                }
                if (r < 0) throw IOException(if (ultima.isEmpty()) "El servidor cerró la conexión" else "El servidor respondió: $ultima")
                n += r
            }
            if (String(head, Charsets.ISO_8859_1) != "HTTP/") {
                pin.unread(head)
                return
            }
            val sb = StringBuilder("HTTP/")
            var fin = 0
            while (fin < 4) {
                val b = pin.read()
                if (b < 0) throw IOException("Respuesta incompleta del servidor")
                sb.append(b.toChar())
                fin = when {
                    (b == 13 && (fin == 0 || fin == 2)) || (b == 10 && (fin == 1 || fin == 3)) -> fin + 1
                    b == 13 -> 1
                    b == 10 -> if (sb.endsWith("\n\n")) 4 else 0
                    else -> 0
                }
                if (sb.length > 16384) throw IOException("Respuesta HTTP demasiado larga")
            }
            val cabecera = sb.toString()
            ultima = cabecera.lineSequence().first().trim()
            val code = ultima.split(" ").getOrNull(1)?.toIntOrNull() ?: 0
            // cuerpo de la respuesta señuelo (si lo hay): se descarta sin tocar lo que venga después
            val largo = Regex("(?im)^content-length:\\s*(\\d+)").find(cabecera)?.groupValues?.get(1)?.toIntOrNull() ?: 0
            if (largo > 0 && code != 204 && code != 304) {
                val pk = ByteArray(5)
                var m = 0
                while (m < pk.size) { val r = pin.read(pk, m, pk.size - m); if (r < 0) break; m += r }
                val ini = String(pk, 0, m, Charsets.ISO_8859_1)
                if (m == 5 && (ini == "HTTP/" || ini.startsWith("SSH-"))) {
                    pin.unread(pk, 0, m)          // era la respuesta a un HEAD: no tiene cuerpo
                } else {
                    var resto = largo - m
                    val buf = ByteArray(2048)
                    while (resto > 0) {
                        val r = pin.read(buf, 0, minOf(buf.size, resto))
                        if (r < 0) break
                        resto -= r
                    }
                }
            }
        }
    }
}
