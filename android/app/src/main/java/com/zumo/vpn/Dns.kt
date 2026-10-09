package com.zumo.vpn

/**
 * DNS que usa la app para resolver los nombres de las páginas mientras está conectada a un servidor.
 * Se elige por servidor (línea `dns =` de servidores.txt):
 *
 *   dns =                      el DNS del propio servidor (como siempre)
 *   dns = google               8.8.8.8 y 8.8.4.4
 *   dns = cloudflare           1.1.1.1 y 1.0.0.1
 *   dns = 9.9.9.9, 149.112.112.112     a mano: hasta 4 IP, separadas por coma o espacio
 */
object Dns {
    val GOOGLE = listOf("8.8.8.8", "8.8.4.4")
    val CLOUDFLARE = listOf("1.1.1.1", "1.0.0.1")
    const val MAX_MANUALES = 4

    private val AUTO = setOf("", "-", "no", "auto", "automatico", "automático", "predeterminado", "default", "servidor")
    private val NOMBRES_GOOGLE = setOf("google", "gdns")
    private val NOMBRES_CLOUDFLARE = setOf("cloudflare", "cf")

    /** Una IPv4 con sus cuatro números entre 0 y 255. */
    fun ipv4(s: String): Boolean {
        val p = s.split('.')
        return p.size == 4 && p.all { it.isNotEmpty() && it.length <= 3 && it.all { c -> c in '0'..'9' } && it.toInt() in 0..255 }
    }

    private fun manuales(valor: String): List<String> =
        valor.split(',', ';', ' ', '\t', '\n', '\r').map { it.trim() }.filter { it.isNotEmpty() && ipv4(it) }
            .distinct().take(MAX_MANUALES)

    /** Forma guardada: "" (el del servidor) | "google" | "cloudflare" | "ip,ip". Lo que no se entienda queda "". */
    fun normalizar(valor: String): String {
        val v = valor.trim().lowercase()
        return when {
            v in AUTO -> ""
            v in NOMBRES_GOOGLE -> "google"
            v in NOMBRES_CLOUDFLARE -> "cloudflare"
            else -> manuales(v).joinToString(",")
        }
    }

    /** Las IP de ese DNS, en el orden en que se prueban. Vacío = que resuelva el servidor SSH. */
    fun servidores(valor: String): List<String> = when (val n = normalizar(valor)) {
        "" -> emptyList()
        "google" -> GOOGLE
        "cloudflare" -> CLOUDFLARE
        else -> n.split(',')
    }

    /** Cómo se muestra en el registro. */
    fun etiqueta(valor: String): String = when (val n = normalizar(valor)) {
        "" -> "el del servidor"
        "google" -> "Google (8.8.8.8)"
        "cloudflare" -> "Cloudflare (1.1.1.1)"
        else -> n.replace(",", ", ")
    }
}
