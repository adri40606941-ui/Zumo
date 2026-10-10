package com.zumo.port

import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/** A qué sistema autónomo (ASN, la "empresa" que anuncia esa IP en internet) pertenece una IP. */
class InfoAsn(val numero: Long, val nombre: String, val pais: String, val prefijo: String) {
    /** "AS13335 · CLOUDFLARENET · US" (sin el nombre o el país si no se pudieron averiguar). */
    val etiqueta: String
        get() = listOf("AS$numero", nombre, pais).filter { it.isNotBlank() }.joinToString(" · ")
}

/**
 * Averigua el ASN de una IP con Team Cymru (el servicio de IP→ASN más usado): es una consulta DNS de tipo TXT
 *   4.3.2.1.origin.asn.cymru.com  →  "13335 | 104.16.0.0/12 | US | arin | 2014-03-28"
 *   AS13335.asn.cymru.com         →  "13335 | US | arin | 2010-07-14 | CLOUDFLARENET, US"
 * Android no sabe pedir registros TXT, así que la consulta va por DNS sobre HTTPS (Cloudflare, y Google de respaldo):
 * sin claves, sin cuenta y por la misma red que eligió el usuario en la app.
 */
class BuscadorAsn(
    private val bajar: (String, Int) -> String? = { u, ms -> descargarDns(u, ms) },
) {
    private val porIp = ConcurrentHashMap<String, List<InfoAsn>>()
    private val nombres = ConcurrentHashMap<Long, Pair<String, String>>()   // ASN -> (nombre, país)
    private val fallosSeguidos = AtomicInteger(0)

    /** Muchas consultas seguidas sin respuesta: el servicio no se alcanza desde esta red, no se insiste. */
    val sinServicio: Boolean get() = fallosSeguidos.get() >= MAX_FALLOS

    /**
     * Los ASN de [ip] (casi siempre uno). Lista vacía = esa IP no tiene ASN (no se anuncia, o es de una red local).
     * null = no se pudo preguntar (sin internet o el servicio no contesta).
     */
    fun consultar(ip: String): List<InfoAsn>? {
        porIp[ip]?.let { return it }
        val consulta = nombreOrigen(ip)
        if (consulta == null || Objetivos.esLocal(ip)) return emptyList()
        if (sinServicio) return null
        val filas = txt(consulta)
        if (filas == null) { fallosSeguidos.incrementAndGet(); return null }
        fallosSeguidos.set(0)
        val origen = parsearOrigen(filas.firstOrNull().orEmpty())
        val lista = if (origen == null) emptyList() else origen.numeros.map { n ->
            val (nombre, pais) = nombreDe(n) ?: ("" to origen.pais)
            InfoAsn(n, nombre, pais.ifBlank { origen.pais }, origen.prefijo)
        }
        porIp[ip] = lista
        return lista
    }

    private fun nombreDe(n: Long): Pair<String, String>? {
        nombres[n]?.let { return it }
        val filas = txt("AS$n.asn.cymru.com") ?: return null
        val r = parsearNombre(filas.firstOrNull().orEmpty()) ?: return null
        nombres[n] = r
        return r
    }

    /** Las filas de texto de un registro TXT. Lista vacía = el nombre no existe; null = ningún servicio contestó. */
    private fun txt(nombre: String): List<String>? {
        for (base in PROVEEDORES) {
            val json = bajar(base + URLEncoder.encode(nombre, "UTF-8") + "&type=TXT", 6000) ?: continue
            when (estadoDns(json)) {
                0 -> return respuestasTxt(json)
                3 -> return emptyList()          // NXDOMAIN: ese nombre no existe, no es una falla
            }
        }
        return null
    }

    class Origen(val numeros: List<Long>, val prefijo: String, val pais: String)

    companion object {
        const val MAX_FALLOS = 6
        private val PROVEEDORES = listOf(
            "https://cloudflare-dns.com/dns-query?name=",
            "https://dns.google/resolve?name=",
        )
        private val RE_DATA = Regex(""""data"\s*:\s*"((?:[^"\\]|\\.)*)"""")
        private val RE_STATUS = Regex(""""Status"\s*:\s*(\d+)""")
        private val RE_PAIS_FINAL = Regex(""",\s*[A-Z]{2}$""")

        /** El nombre DNS que hay que preguntar: "4.3.2.1.origin.asn.cymru.com" (o origin6 con los 32 dígitos al revés). null si no es una IP. */
        fun nombreOrigen(ip: String): String? {
            val v4 = Objetivos.ipv4(ip)
            if (v4 != null) {
                val o = ip.split('.')
                return o.reversed().joinToString(".") + ".origin.asn.cymru.com"
            }
            if (!ip.contains(':') || !ip.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' || it == ':' || it == '.' }) return null
            return try {
                val bytes = InetAddress.getByName(ip).address       // es una IP escrita: no consulta ningún DNS
                if (bytes.size != 16) return null
                val digitos = bytes.flatMap { listOf((it.toInt() shr 4) and 15, it.toInt() and 15) }
                digitos.reversed().joinToString(".") { Integer.toHexString(it) } + ".origin6.asn.cymru.com"
            } catch (_: Exception) { null }
        }

        /** "Status" de la respuesta DNS en JSON (0 = bien, 3 = no existe). -1 si no es una respuesta DNS. */
        fun estadoDns(json: String): Int = RE_STATUS.find(json)?.groupValues?.get(1)?.toIntOrNull() ?: -1

        /** Los textos de los registros TXT de una respuesta DNS en JSON, ya sin las comillas. */
        fun respuestasTxt(json: String): List<String> = RE_DATA.findAll(json).map { m ->
            m.groupValues[1].replace("\\\"", "\"").replace("\\\\", "\\").trim().trim('"').trim()
        }.filter { it.isNotEmpty() }.toList()

        /** "13335 | 104.16.0.0/12 | US | arin | 2014-03-28" → ASN, prefijo y país. A veces hay más de un ASN en la primera columna. */
        fun parsearOrigen(fila: String): Origen? {
            val c = fila.split('|').map { it.trim() }
            val numeros = c.getOrNull(0).orEmpty().split(' ', ',').mapNotNull { it.trim().toLongOrNull() }.filter { it > 0 }
            if (numeros.isEmpty()) return null
            return Origen(numeros, c.getOrNull(1).orEmpty(), c.getOrNull(2).orEmpty())
        }

        /** "13335 | US | arin | 2010-07-14 | CLOUDFLARENET, US" → ("CLOUDFLARENET", "US"). */
        fun parsearNombre(fila: String): Pair<String, String>? {
            val c = fila.split('|').map { it.trim() }
            val nombre = c.getOrNull(4) ?: return null
            if (nombre.isBlank()) return null
            return nombre.replace(RE_PAIS_FINAL, "").trim() to c.getOrNull(1).orEmpty()
        }

        fun descargarDns(url: String, esperaMs: Int): String? = try {
            val c = URL(url).openConnection() as HttpURLConnection
            c.connectTimeout = 5000
            c.readTimeout = esperaMs
            c.setRequestProperty("Accept", "application/dns-json")
            c.setRequestProperty("User-Agent", "ZumoPort/1.0")
            if (c.responseCode != 200) null else c.inputStream.bufferedReader().use { it.readText() }
        } catch (_: Exception) { null }
    }
}
