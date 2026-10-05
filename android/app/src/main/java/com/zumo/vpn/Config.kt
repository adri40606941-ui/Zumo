package com.zumo.vpn

import android.util.Base64
import org.json.JSONObject

/** Configuración de conexión. Se comparte como un enlace zumo://... */
data class Config(
    val name: String = "Zumo",
    val host: String = "",          // VPS con SSH
    val sshPort: Int = 22,
    val mode: String = "ws",        // "ws" = WebSocket | "payload" = payload HTTP personalizado
    val proxyHost: String = "",     // a dónde se abre el TCP (vacío = host). Ej: dominio de CloudFront
    val proxyPort: Int = 80,
    val tls: Boolean = false,
    val sni: String = "",
    val wsHost: String = "",        // cabecera Host (vacío = host)
    val wsPath: String = "/",
    val payload: String = "",
) {
    fun valida(): Boolean = host.isNotBlank() && (mode != "payload" || payload.isNotBlank())

    fun toJson(): JSONObject = JSONObject()
        .put("name", name).put("host", host).put("sshPort", sshPort).put("mode", mode)
        .put("proxyHost", proxyHost).put("proxyPort", proxyPort).put("tls", tls).put("sni", sni)
        .put("wsHost", wsHost).put("wsPath", wsPath).put("payload", payload)

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
            mode = j.optString("mode", "ws"),
            proxyHost = j.optString("proxyHost", ""),
            proxyPort = j.optInt("proxyPort", 80),
            tls = j.optBoolean("tls", false),
            sni = j.optString("sni", ""),
            wsHost = j.optString("wsHost", ""),
            wsPath = j.optString("wsPath", "/"),
            payload = j.optString("payload", ""),
        )

        fun fromLink(link: String): Config? = try {
            val t = link.trim().removePrefix("zumo://")
            val raw = Base64.decode(t, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
            fromJson(JSONObject(String(raw, Charsets.UTF_8))).takeIf { it.host.isNotBlank() }
        } catch (e: Exception) {
            null
        }
    }
}
