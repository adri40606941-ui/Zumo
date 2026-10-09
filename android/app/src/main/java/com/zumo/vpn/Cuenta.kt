package com.zumo.vpn

import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Nombre del cliente y vencimiento de su cuenta, para mostrar "Nombre [dd/mm]" al conectar.
 * Los guarda el bot en la VPS; la app los pide con su token a la misma dirección de donde baja la lista.
 */
object Cuenta {
    class Datos(val nombre: String, val vence: String)

    private val FECHA = Regex("^(\\d{4})-(\\d{2})-(\\d{2})$")

    /** Las direcciones donde preguntar: la de la lista de la VPS cambiando el final por /cuenta?t=TOKEN (GitHub no sirve). */
    fun urls(actualizar: String, token: String): List<String> {
        val t = URLEncoder.encode(token, "UTF-8")
        return Servidores.separarUrls(actualizar)
            .filter { !it.contains("githubusercontent.com") && it.substringBefore('?').endsWith("/servidores.bin") }
            .map { it.substringBefore('?').removeSuffix("servidores.bin") + "cuenta?t=" + t }
    }

    /** La respuesta del bot: primera línea el nombre, segunda el vencimiento (AAAA-MM-DD, puede faltar). null si no sirve. */
    fun parsear(texto: String): Datos? {
        val l = texto.lines()
        val nombre = l.getOrNull(0)?.trim().orEmpty().take(48)
        val vence = l.getOrNull(1)?.trim().orEmpty().let { if (FECHA.matches(it)) it else "" }
        return if (nombre.isEmpty() && vence.isEmpty()) null else Datos(nombre, vence)
    }

    /** "Adrián [09/11]": nombre y día/mes del vencimiento. Vacío si no hay nada que mostrar. */
    fun etiqueta(nombre: String, vence: String): String {
        val m = FECHA.find(vence)
        val f = if (m != null) "[${m.groupValues[3]}/${m.groupValues[2]}]" else ""
        return listOf(nombre.trim(), f).filter { it.isNotEmpty() }.joinToString(" ")
    }

    /** Pregunta al bot (sin hilo principal). null si no hay dirección, no contesta o el token no existe. */
    fun consultar(actualizar: String, token: String): Datos? {
        for (u in urls(actualizar, token)) {
            try {
                val c = (URL(u).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 6000; readTimeout = 6000; useCaches = false
                    setRequestProperty("User-Agent", "ZumoVPN")
                }
                try {
                    if (c.responseCode != 200) continue
                    val cuerpo = c.inputStream.use { it.readBytes() }.take(2048).toByteArray().toString(Charsets.UTF_8)
                    parsear(cuerpo)?.let { return it }
                } finally { c.disconnect() }
            } catch (_: Exception) { }
        }
        return null
    }
}
