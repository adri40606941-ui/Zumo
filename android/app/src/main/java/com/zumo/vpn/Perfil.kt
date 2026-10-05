package com.zumo.vpn

import org.json.JSONObject

/**
 * Un "perfil" exportable: la configuración del servidor + cómo iniciar sesión (usuario/clave o HWID).
 * Es lo que se guarda en el archivo .zumoconf para mandarlo por WhatsApp u otra app y que, al abrirlo,
 * la app quede lista para conectar sin tener que tipear nada.
 */
data class Perfil(
    val cfg: Config,
    val user: String = "",
    val pass: String = "",
    val useHwid: Boolean = false,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("cfg", cfg.toJson())
        .put("user", user)
        .put("pass", pass)
        .put("hwid", useHwid)

    companion object {
        fun fromJson(j: JSONObject): Perfil? = try {
            val c = Config.fromJson(j.getJSONObject("cfg"))
            if (!c.valida()) null else Perfil(
                cfg = c,
                user = j.optString("user", ""),
                pass = j.optString("pass", ""),
                useHwid = j.optBoolean("hwid", false),
            )
        } catch (e: Exception) {
            null
        }

        /** Intenta leer un perfil completo; si el texto es un enlace zumo:// viejo (sin login), arma uno solo con el servidor. */
        fun desdeTexto(t: String): Perfil? {
            val s = t.trim()
            try {
                if (s.startsWith("zumo://")) {
                    Config.fromLink(s)?.let { return Perfil(it) }
                }
                return fromJson(JSONObject(s))
            } catch (e: Exception) {
                return null
            }
        }
    }
}
