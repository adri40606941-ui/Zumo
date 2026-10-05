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

    /** Conecta por TCP (y TLS si se pidió) y, si hay payload, lo envía y consume las respuestas HTTP. */
    fun connect(c: Config, etapa: (String) -> Unit = {}, proteger: (Socket) -> Unit = {}): Tunnel {
        etapa("Conectando al servidor")
        var s = Socket()
        // Hay que sacar esta conexión de la VPN antes de que exista el túnel (TUN): si no, el
        // propio tráfico SSH que sostiene la VPN entraría a la VPN y se cortaría en bucle.
        proteger(s)
        s.tcpNoDelay = true
        s.keepAlive = true
        s.connect(InetSocketAddress(c.host, c.sshPort), 15000)
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
        val pin = PushbackInputStream(StreamEspia(s.getInputStream(), CrudoDebug.entrada), 8192)
        val salida = StreamEspiaSalida(s.getOutputStream(), CrudoDebug.salida)
        if (c.payload.isNotBlank()) {
            etapa("Enviando solicitud")
            s.soTimeout = 15000
            for ((i, parte) in partir(expandir(c.payload, c)).withIndex()) {
                if (i > 0) Thread.sleep(150)
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

    /** Reemplaza los comodines del payload: [host] [port] [host_port] [crlf] [cr] [lf] [protocol]. */
    fun expandir(p: String, c: Config): String = p
        .replace("\\r", "\r").replace("\\n", "\n")
        .replace("[crlf*2]", "\r\n\r\n")
        .replace("[host_port]", "${c.host}:${c.sshPort}")
        .replace("[ssh_host]", c.host).replace("[ssh_port]", c.sshPort.toString())
        .replace("[host]", c.host)
        .replace("[port]", c.sshPort.toString())
        .replace("[protocol]", "HTTP/1.1")
        .replace("[crlf]", "\r\n").replace("[cr]", "\r").replace("[lf]", "\n")

    /** [split] y [instant_split] envían el payload en partes. */
    fun partir(p: String): List<String> =
        p.split("[split]", "[instant_split]").filter { it.isNotEmpty() }.ifEmpty { listOf(p) }

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
