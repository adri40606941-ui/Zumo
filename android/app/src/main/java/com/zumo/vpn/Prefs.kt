package com.zumo.vpn

import android.content.Context
import org.json.JSONObject

/** Datos guardados en el teléfono (configuración, usuario, opciones). */
class Prefs(ctx: Context) {
    private val sp = ctx.applicationContext.getSharedPreferences("zumo", Context.MODE_PRIVATE)

    var config: Config?
        get() = sp.getString("config", null)?.let { try { Config.fromJson(JSONObject(it)) } catch (e: Exception) { null } }
        set(v) { sp.edit().putString("config", v?.toJson()?.toString()).apply() }

    var user: String
        get() = sp.getString("user", "") ?: ""
        set(v) { sp.edit().putString("user", v).apply() }

    var pass: String
        get() = sp.getString("pass", "") ?: ""
        set(v) { sp.edit().putString("pass", v).apply() }

    /** Nombre del servidor elegido de la lista que trae la app; vacío si la cuenta vino de un .zs. */
    var servidor: String
        get() = sp.getString("servidor", "") ?: ""
        set(v) { sp.edit().putString("servidor", v).apply() }

    /** Vencimiento de la cuenta (AAAA-MM-DD), o vacío. */
    var exp: String
        get() = sp.getString("exp", "") ?: ""
        set(v) { sp.edit().putString("exp", v).apply() }

    /** El usuario tocó Conectar y no Desconectar (sirve para no mostrar el permiso de VPN de más). */
    var wanted: Boolean
        get() = sp.getBoolean("wanted", false)
        set(v) { sp.edit().putBoolean("wanted", v).apply() }

    /** Ya se le pidió una vez la exclusión de batería (para no repetir el diálogo cada vez). */
    var pidioBateria: Boolean
        get() = sp.getBoolean("pidio_bateria", false)
        set(v) { sp.edit().putBoolean("pidio_bateria", v).apply() }

    /** Cuándo (epoch ms) se buscó por última vez una lista de servidores nueva en segundo plano. */
    var ultimoChequeoLista: Long
        get() = sp.getLong("ultimo_chequeo_lista", 0)
        set(v) { sp.edit().putLong("ultimo_chequeo_lista", v).apply() }

    /** El cliente entra siempre con el token de este celular (se quitó la opción de usuario y contraseña). */
    val modoToken: Boolean get() = true

    /** Token al azar, solo si el teléfono no da un ANDROID_ID usable (no sobrevive a desinstalar). */
    var tokenFallback: String
        get() = sp.getString("token_fallback", "") ?: ""
        set(v) { sp.edit().putString("token_fallback", v).apply() }
}
