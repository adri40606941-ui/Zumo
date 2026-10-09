package com.zumo.vpn

import java.io.ByteArrayOutputStream

/**
 * Consulta DNS de tipo A (IPv4) en el formato de red, para mandarla por TCP al puerto 53 a través del
 * túnel SSH. Sin dependencias de Android, para poder probarla con pruebas normales.
 */
object DnsConsulta {
    /** [rcode] 0 = bien, 3 = el nombre no existe...; [ips] vacío si no hay registros A. */
    class Respuesta(val rcode: Int, val ips: List<String>, val ttl: Int)

    /**
     * La pregunta "¿cuál es la IP de [nombre]?", SIN el prefijo de 2 bytes de largo que lleva el DNS por TCP.
     * null si el nombre no sirve (vacío, demasiado largo, o con una parte de más de 63 letras).
     */
    fun armar(nombre: String, id: Int): ByteArray? {
        val ascii = try { java.net.IDN.toASCII(nombre.trim().trimEnd('.')) } catch (_: Exception) { return null }
        if (ascii.isEmpty() || ascii.length > 253) return null
        val o = ByteArrayOutputStream()
        o.write((id shr 8) and 0xff); o.write(id and 0xff)
        o.write(0x01); o.write(0x00)                    // recursión deseada
        o.write(0); o.write(1)                          // una pregunta
        for (k in 0 until 6) o.write(0)                 // sin respuestas, autoridades ni extras
        for (parte in ascii.split('.')) {
            val b = parte.toByteArray(Charsets.ISO_8859_1)
            if (b.isEmpty() || b.size > 63) return null
            o.write(b.size); o.write(b)
        }
        o.write(0)
        o.write(0); o.write(1)                          // tipo A
        o.write(0); o.write(1)                          // clase IN
        return o.toByteArray()
    }

    /**
     * Lee la respuesta (sin el prefijo de largo). null si está rota o no corresponde a la pregunta [id].
     * Junta todos los registros A (las respuestas con CNAME traen primero el alias y después las IP).
     */
    fun leer(m: ByteArray, id: Int): Respuesta? {
        if (m.size < 12) return null
        fun u16(p: Int) = ((m[p].toInt() and 0xff) shl 8) or (m[p + 1].toInt() and 0xff)
        if (u16(0) != (id and 0xffff)) return null
        val flags = u16(2)
        if ((flags and 0x8000) == 0) return null        // no es una respuesta
        val rcode = flags and 0xf
        if (rcode != 0) return Respuesta(rcode, emptyList(), 0)
        val preguntas = u16(4)
        val respuestas = u16(6)
        var p = 12
        for (k in 0 until preguntas) {
            p = saltarNombre(m, p) ?: return null
            p += 4
            if (p > m.size) return null
        }
        val ips = ArrayList<String>()
        var ttl = Long.MAX_VALUE
        for (k in 0 until respuestas) {
            p = saltarNombre(m, p) ?: return null
            if (p + 10 > m.size) return null
            val tipo = u16(p)
            val clase = u16(p + 2)
            val t = (u16(p + 4).toLong() shl 16) or u16(p + 6).toLong()
            val largo = u16(p + 8)
            p += 10
            if (p + largo > m.size) return null
            if (tipo == 1 && clase == 1 && largo == 4) {
                ips.add((0 until 4).joinToString(".") { (m[p + it].toInt() and 0xff).toString() })
                if (t < ttl) ttl = t
            }
            p += largo
        }
        return Respuesta(0, ips, if (ips.isEmpty()) 0 else ttl.coerceIn(0L, 86400L).toInt())
    }

    /** Pasa de largo un nombre (con o sin compresión) y devuelve dónde sigue, o null si está cortado. */
    private fun saltarNombre(m: ByteArray, desde: Int): Int? {
        var p = desde
        while (true) {
            if (p >= m.size) return null
            val l = m[p].toInt() and 0xff
            when {
                l == 0 -> return p + 1
                (l and 0xC0) == 0xC0 -> return if (p + 2 <= m.size) p + 2 else null
                (l and 0xC0) != 0 -> return null
                else -> p += 1 + l
            }
        }
    }
}
