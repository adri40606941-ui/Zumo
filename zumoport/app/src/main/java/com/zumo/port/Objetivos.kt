package com.zumo.port

/**
 * Qué se va a escanear: se entienden IP sueltas, rangos y bloques CIDR, y también nombres de dominio.
 *
 *   192.168.1.10                un equipo
 *   192.168.1.1-254             rango corto (solo cambia el último número)
 *   192.168.1.1-192.168.2.20    rango completo
 *   10.0.0.0/24                 bloque CIDR (de /16 a /32)
 *   ejemplo.com                 un dominio (se resuelve al escanear)
 *
 * Se pueden poner varios separados por coma, espacio, punto y coma o renglón nuevo.
 */
object Objetivos {
    /** Tope de equipos por escaneo (un /16 entero). Más que eso tardaría horas desde un celular. */
    const val MAX_EQUIPOS = 65536L

    /** Un renglón ya entendido: [cantidad] equipos que se recorren con [equipos] sin armar la lista entera. */
    class Entrada(val texto: String, val cantidad: Long, private val desde: Long, private val nombre: String?) {
        fun equipos(): Sequence<String> =
            if (nombre != null) sequenceOf(nombre)
            else generateSequence(desde) { it + 1 }.take(cantidad.toInt()).map { aTexto(it) }
    }

    class Parseo(val entradas: List<Entrada>, val errores: List<String>) {
        val total: Long get() = entradas.sumOf { it.cantidad }
        fun equipos(): Sequence<String> = entradas.asSequence().flatMap { it.equipos() }.distinct()
    }

    private val RE_IPV4 = Regex("""^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$""")
    private val RE_CIDR = Regex("""^[0-9.]+/[0-9]{1,2}$""")
    private val RE_DOMINIO = Regex("""^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$""")

    fun analizar(texto: String): Parseo {
        val entradas = ArrayList<Entrada>()
        val errores = ArrayList<String>()
        val partes = texto.split(',', ';', ' ', '\t', '\n', '\r').map { limpiar(it) }.filter { it.isNotEmpty() }
        if (partes.isEmpty()) errores.add("Escribí una IP, un rango o un dominio")
        for (p in partes) {
            val r = entender(p)
            if (r.second != null) errores.add(r.second!!) else entradas.add(r.first!!)
        }
        val total = entradas.sumOf { it.cantidad }
        if (errores.isEmpty() && total > MAX_EQUIPOS) errores.add("Son $total equipos: el máximo por escaneo es $MAX_EQUIPOS")
        return Parseo(if (errores.isEmpty()) entradas else emptyList(), errores)
    }

    /** Quita lo que se pega de más: "http://", "https://", rutas, el puerto y espacios. */
    fun limpiar(s: String): String {
        var t = s.trim().lowercase().removePrefix("https://").removePrefix("http://")
        if (RE_CIDR.matches(t)) return t        // 10.0.0.0/24: la barra es parte del bloque, no una ruta
        t = t.substringBefore("/").substringBefore("?")
        if (t.count { it == ':' } == 1) t = t.substringBefore(":")
        return t.trim('.', ' ')
    }

    private fun entender(p: String): Pair<Entrada?, String?> {
        if (p.contains("/")) return cidr(p)
        if (p.contains("-") && RE_IPV4.matches(p.substringBefore("-"))) return rango(p)
        val ip = ipv4(p)
        if (ip != null) return Entrada(p, 1, ip, null) to null
        if (RE_IPV4.matches(p)) return null to "\"$p\" no es una IP válida (cada número va de 0 a 255)"
        if (RE_DOMINIO.matches(p)) return Entrada(p, 1, 0, p) to null
        return null to "No entiendo \"$p\""
    }

    private fun cidr(p: String): Pair<Entrada?, String?> {
        val base = ipv4(p.substringBefore("/")) ?: return null to "\"$p\": la IP del bloque no es válida"
        val bits = p.substringAfter("/").toIntOrNull()
        if (bits == null || bits !in 0..32) return null to "\"$p\": el /n tiene que estar entre 16 y 32"
        if (bits < 16) return null to "\"$p\" tiene demasiados equipos (el mínimo es /16)"
        val tam = 1L shl (32 - bits)
        val ini = base and (tam - 1).inv() and 0xFFFFFFFFL
        // en un bloque normal, la primera dirección (red) y la última (difusión) no son equipos
        return if (bits >= 31) Entrada(p, tam, ini, null) to null
        else Entrada(p, tam - 2, ini + 1, null) to null
    }

    private fun rango(p: String): Pair<Entrada?, String?> {
        val izq = p.substringBefore("-")
        val der = p.substringAfter("-")
        val ini = ipv4(izq) ?: return null to "\"$p\": la IP inicial no es válida"
        val fin: Long = if (der.contains(".")) {
            ipv4(der) ?: return null to "\"$p\": la IP final no es válida"
        } else {
            val n = der.toIntOrNull()
            if (n == null || n !in 0..255) return null to "\"$p\": el último número tiene que estar entre 0 y 255"
            (ini and 0xFFFFFF00L) or n.toLong()
        }
        if (fin < ini) return null to "\"$p\": el final es menor que el inicio"
        return Entrada(p, fin - ini + 1, ini, null) to null
    }

    /** "1.2.3.4" → número de 32 bits, o null si no es una IPv4 válida. */
    fun ipv4(s: String): Long? {
        val m = RE_IPV4.matchEntire(s) ?: return null
        var v = 0L
        for (i in 1..4) {
            val n = m.groupValues[i].toInt()
            if (n > 255) return null
            v = (v shl 8) or n.toLong()
        }
        return v
    }

    fun aTexto(v: Long): String = "${(v shr 24) and 255}.${(v shr 16) and 255}.${(v shr 8) and 255}.${v and 255}"

    fun esIp(s: String): Boolean = ipv4(s) != null || s.contains(':')
}

/** Listas de puertos: "80,443,8000-8100", y las listas ya armadas que se eligen con un toque. */
object Puertos {
    const val MAX_PUERTOS = 2000

    val WEB = listOf(80, 443, 8080, 8443, 8000, 8888)
    val COMUNES = listOf(
        21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445, 465, 587, 993, 995,
        1080, 1194, 1723, 3000, 3306, 3389, 5432, 5900, 6379, 8000, 8080, 8443, 8888, 9000, 9090, 27017,
    )

    class Parseo(val puertos: List<Int>, val error: String?)

    fun analizar(texto: String): Parseo {
        val set = java.util.TreeSet<Int>()
        for (parte in texto.split(',', ';', ' ', '\n', '\t').map { it.trim() }.filter { it.isNotEmpty() }) {
            if (parte.contains("-")) {
                val a = parte.substringBefore("-").toIntOrNull()
                val b = parte.substringAfter("-").toIntOrNull()
                if (a == null || b == null || a !in 1..65535 || b !in 1..65535 || b < a) return Parseo(emptyList(), "\"$parte\" no es un rango de puertos válido")
                if (b - a + 1 > MAX_PUERTOS) return Parseo(emptyList(), "\"$parte\": el máximo es $MAX_PUERTOS puertos por rango")
                for (p in a..b) set.add(p)
            } else {
                val n = parte.toIntOrNull()
                if (n == null || n !in 1..65535) return Parseo(emptyList(), "\"$parte\" no es un puerto (de 1 a 65535)")
                set.add(n)
            }
            if (set.size > MAX_PUERTOS) return Parseo(emptyList(), "Demasiados puertos (máximo $MAX_PUERTOS)")
        }
        if (set.isEmpty()) return Parseo(emptyList(), "Escribí al menos un puerto")
        return Parseo(set.toList(), null)
    }

    /** Puertos que hablan web (HTTP) y los que además van dentro de TLS (HTTPS). */
    fun esWeb(p: Int): Boolean = p in setOf(80, 81, 443, 591, 3000, 5000, 8000, 8008, 8080, 8081, 8088, 8443, 8888, 9000, 9090, 9443, 4443)
    fun esTls(p: Int): Boolean = p in setOf(443, 4443, 8443, 9443, 8883)
}
