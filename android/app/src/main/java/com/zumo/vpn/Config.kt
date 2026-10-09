package com.zumo.vpn

import android.util.Base64
import org.json.JSONObject

/** Configuración de conexión: SSH directo (dominio o IP) con payload opcional. Se comparte como zumo://... */
data class Config(
    val name: String = "Zumo",
    val host: String = "",          // dominio o IP del servidor SSH
    val sshPort: Int = 22,          // puerto al que se conecta (22, 80, 443...)
    val payload: String = "",       // payload HTTP opcional (comodines: [host] [port] [host_port] [crlf] [lf] [split]...)
    val tls: Boolean = false,       // envolver la conexión en TLS (puerto 443)
    val sni: String = "",           // SNI para TLS (vacío = el host)
) {
    fun valida(): Boolean = hosts().isNotEmpty() && sshPort in 1..65535

    /** Los dominios o IP de este servidor (se pueden poner varios, separados por coma, espacio o punto y coma). */
    fun hosts(): List<String> = host.split(',', ';', ' ', '\t', '\n', '\r').map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    /** Esta misma configuración pero con un solo host (el que se va a usar para conectar). */
    fun con(h: String): Config = copy(host = h)

    /** Limpia lo que el usuario pegó: "http://", "/", espacios y "dominio:puerto". */
    fun limpiar(): Config {
        var p = sshPort
        var puertoTomado = false
        val limpios = hosts().map { crudo ->
            var h = crudo.removePrefix("https://").removePrefix("http://").substringBefore("/").trim()
            if (h.count { it == ':' } == 1) {
                val (a, b) = h.split(":")
                b.toIntOrNull()?.let { if (!puertoTomado) { p = it; puertoTomado = true }; h = a }
            }
            h
        }.filter { it.isNotEmpty() }.distinct()
        return copy(host = limpios.joinToString(","), sshPort = p)
    }

    fun toJson(): JSONObject = JSONObject()
        .put("name", name).put("host", host).put("sshPort", sshPort)
        .put("payload", payload).put("tls", tls).put("sni", sni)

    fun toLink(): String =
        "zumo://" + Base64.encodeToString(
            toJson().toString().toByteArray(Charsets.UTF_8),
            Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING
        )

    companion object {
        fun fromJson(j: JSONObject): Config = Config(
            name = j.optString("name", "Zumo"),
            host = j.optString("host", ""),
            sshPort = j.optInt("sshPort", 22),
            payload = j.optString("payload", ""),
            tls = j.optBoolean("tls", false),
            sni = j.optString("sni", ""),
        ).limpiar()

        fun fromLink(link: String): Config? = try {
            val t = link.trim().removePrefix("zumo://")
            val raw = Base64.decode(t, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
            fromJson(JSONObject(String(raw, Charsets.UTF_8))).takeIf { it.valida() }
        } catch (e: Exception) {
            null
        }
    }
}
