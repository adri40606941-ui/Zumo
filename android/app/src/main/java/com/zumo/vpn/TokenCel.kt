package com.zumo.vpn

import android.content.Context
import android.provider.Settings

/**
 * Token único de este celular. Hace de usuario y de contraseña: el cliente se lo pasa al proveedor,
 * el proveedor lo registra en la VPS (como una cuenta HWID) y recién ahí la app conecta. Si el
 * proveedor lo borra de la VPS, deja de conectar.
 *
 * Sale del ANDROID_ID del teléfono, que es el mismo aunque se borre y se reinstale la app, siempre
 * que el APK esté firmado con la misma clave (por eso la clave de firma fija). Cambia con un reseteo
 * de fábrica, en una app clonada ("Dual apps") o en otro perfil del teléfono. Si el sistema no da un
 * ANDROID_ID usable, se genera uno al azar y se guarda (ese no sobrevive a desinstalar la app).
 */
object TokenCel {
    private val HEX = Regex("^[0-9a-f]{8,32}$")

    fun token(ctx: Context, prefs: Prefs): String {
        val a = try {
            Settings.Secure.getString(ctx.applicationContext.contentResolver, Settings.Secure.ANDROID_ID)
        } catch (_: Exception) { null }
        val norm = (a ?: "").trim().lowercase()
        if (HEX.matches(norm)) return norm
        var f = prefs.tokenFallback
        if (f.isBlank()) { f = aleatorio(); prefs.tokenFallback = f }
        return f
    }

    private fun aleatorio(): String {
        val hex = "0123456789abcdef"
        val r = java.security.SecureRandom()
        return (1..16).map { hex[r.nextInt(16)] }.joinToString("")
    }
}
