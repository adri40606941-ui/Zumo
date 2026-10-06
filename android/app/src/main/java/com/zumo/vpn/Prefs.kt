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

    var autoStart: Boolean
        get() = sp.getBoolean("auto", true)
        set(v) { sp.edit().putBoolean("auto", v).apply() }

    /** El usuario quiere la VPN encendida (se mantiene aunque el sistema mate el servicio). */
    var wanted: Boolean
        get() = sp.getBoolean("wanted", false)
        set(v) { sp.edit().putBoolean("wanted", v).apply() }

    /** Ya se le pidió una vez la exclusión de batería (para no repetir el diálogo cada vez). */
    var pidioBateria: Boolean
        get() = sp.getBoolean("pidio_bateria", false)
        set(v) { sp.edit().putBoolean("pidio_bateria", v).apply() }
}
