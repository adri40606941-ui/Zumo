package com.zumo.vpn

import org.json.JSONObject
import java.util.Calendar

/**
 * Una cuenta completa: servidor (con su payload), usuario, clave y fecha de vencimiento (AAAA-MM-DD).
 * Es lo que lleva el archivo .zs que genera el bot de Telegram.
 */
data class Perfil(
    val cfg: Config,
    val user: String = "",
    val pass: String = "",
    val exp: String = "",
) {
    fun toJson(): JSONObject = JSONObject()
        .put("cfg", cfg.toJson())
        .put("user", user)
        .put("pass", pass)
        .put("exp", exp)

    companion object {
        /** El servidor corta las cuentas el día del vencimiento a esta hora (EXPIRE_HOUR de zumo-limit). */
        const val HORA_CORTE = 21

        fun fromJson(j: JSONObject): Perfil? = try {
            val c = Config.fromJson(j.getJSONObject("cfg"))
            if (!c.valida()) null else Perfil(
                cfg = c,
                user = j.optString("user", ""),
                pass = j.optString("pass", ""),
                exp = j.optString("exp", ""),
            )
        } catch (e: Exception) {
            null
        }

        fun desdeTexto(t: String): Perfil? = try { fromJson(JSONObject(t.trim())) } catch (e: Exception) { null }

        private val FECHA = Regex("^(\\d{4})-(\\d{2})-(\\d{2})$")

        /** Vencimiento como Calendar (a las HORA_CORTE), o null si no hay fecha válida. */
        private fun corte(exp: String): Calendar? {
            val m = FECHA.find(exp) ?: return null
            return Calendar.getInstance().apply {
                clear(); set(m.groupValues[1].toInt(), m.groupValues[2].toInt() - 1, m.groupValues[3].toInt(), HORA_CORTE, 0, 0)
            }
        }

        fun vencida(exp: String, ahora: Long = System.currentTimeMillis()): Boolean =
            corte(exp)?.let { ahora >= it.timeInMillis } ?: false

        /** Días que faltan para el día del vencimiento (0 = vence hoy), o null sin fecha. */
        fun diasRestantes(exp: String, ahora: Long = System.currentTimeMillis()): Int? {
            val c = corte(exp) ?: return null
            val hoy = Calendar.getInstance().apply { timeInMillis = ahora; set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }
            val dia = (c.clone() as Calendar).apply { set(Calendar.HOUR_OF_DAY, 0) }
            return Math.round((dia.timeInMillis - hoy.timeInMillis) / 86_400_000.0).toInt()
        }

        /** "05/11/2026" */
        fun fechaLinda(exp: String): String =
            FECHA.find(exp)?.let { "${it.groupValues[3]}/${it.groupValues[2]}/${it.groupValues[1]}" } ?: ""
    }
}
