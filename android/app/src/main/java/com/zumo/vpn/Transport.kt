package com.zumo.vpn

import java.io.IOException
import java.io.InputStream
import java.io.PushbackInputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.security.SecureRandom
import javax.net.ssl.SNIHostName
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/** Socket ya conectado (y con el payload / WebSocket negociado) listo para hablar SSH. */
class Tunnel(val socket: Socket, val input: InputStream)

object Transport {

    fun connect(c: Config): Tunnel {
        val tcpHost = c.proxyHost.ifBlank { c.host }
        val tcpPort = if (c.mode == "direct") c.sshPort else c.proxyPort
        var s = Socket()
        s.tcpNoDelay = true
        s.keepAlive = true
        s.connect(InetSocketAddress(tcpHost, tcpPort), 15000)
        if (c.tls) {
            val sniName = c.sni.ifBlank { c.wsHost.ifBlank { tcpHost } }
            val ss = (SSLSocketFactory.getDefault() as SSLSocketFactory).createSocket(s, sniName, tcpPort, true) as SSLSocket
            val p = ss.sslParameters
            p.serverNames = listOf(SNIHostName(sniName))
            ss.sslParameters = p
            ss.soTimeout = 15000
            ss.startHandshake()
            s = ss
        }
        if (c.mode == "direct") {
            return Tunnel(s, PushbackInputStream(s.getInputStream(), 8192))
        }
        s.soTimeout = 15000
        val out = s.getOutputStream()
        val pin = PushbackInputStream(s.getInputStream(), 8192)
        val bruto = if (c.mode == "payload") c.payload else wsRequest(c)
        for ((i, parte) in partir(expandir(bruto, c)).withIndex()) {
            if (i > 0) Thread.sleep(150)
            out.write(parte.toByteArray(Charsets.ISO_8859_1))
            out.flush()
        }
        leerRespuestasHttp(pin)
        s.soTimeout = 0
        return Tunnel(s, pin)
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

    fun wsRequest(c: Config): String {
        val key = ByteArray(16).also { SecureRandom().nextBytes(it) }
        val host = c.wsHost.ifBlank { c.host }
        val path = c.wsPath.ifBlank { "/" }
        return "GET $path HTTP/1.1[crlf]Host: $host[crlf]Upgrade: websocket[crlf]Connection: Upgrade[crlf]" +
            "Sec-WebSocket-Key: ${base64(key)}[crlf]Sec-WebSocket-Version: 13[crlf][crlf]"
    }

    /** Reemplaza los comodines del payload: [host] [port] [host_port] [crlf] [cr] [lf] [protocol]. */
    fun expandir(p: String, c: Config): String = p
        .replace("\\r", "\r").replace("\\n", "\n")
        .replace("[host_port]", "${c.host}:${c.sshPort}")
        .replace("[host]", c.host)
        .replace("[port]", c.sshPort.toString())
        .replace("[protocol]", "HTTP/1.1")
        .replace("[crlf]", "\r\n").replace("[cr]", "\r").replace("[lf]", "\n")

    /** [split] y [instant_split] envían el payload en partes. */
    fun partir(p: String): List<String> =
        p.split("[split]", "[instant_split]").filter { it.isNotEmpty() }.ifEmpty { listOf(p) }

    /**
     * Si el servidor contesta con cabeceras HTTP (101 / 200...), las consume. Si contesta directo con
     * el banner de SSH, no toca nada. Soporta varias respuestas seguidas.
     */
    private fun leerRespuestasHttp(pin: PushbackInputStream) {
        repeat(4) {
            val head = ByteArray(5)
            var n = 0
            while (n < head.size) {
                val r = pin.read(head, n, head.size - n)
                if (r < 0) throw IOException("El servidor cerró la conexión")
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
                    else -> 0
                }
                if (sb.length > 16384) throw IOException("Respuesta HTTP demasiado larga")
            }
            val linea = sb.lineSequence().first()
            val code = linea.split(" ").getOrNull(1)?.toIntOrNull() ?: 0
            if (code !in 200..299 && code != 101) throw IOException("El servidor respondió: $linea")
        }
    }
}
