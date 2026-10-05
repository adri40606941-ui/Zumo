package com.zumo.vpn

import android.annotation.SuppressLint
import android.content.Context
import android.provider.Settings
import java.security.MessageDigest

/** Token propio de este teléfono. Se muestra en la app, se registra en el panel junto al usuario y
 *  la contraseña, y viaja en el payload para que el servidor sepa desde qué dispositivo se conecta. */
object Token {
    /** 12 caracteres (A-F, 0-9), fijos para este teléfono: salen del ANDROID_ID, que no cambia al
     *  reinstalar la app mientras se firme con la misma clave (sí cambia con un restablecimiento de fábrica). */
    @SuppressLint("HardwareIds")
    fun get(ctx: Context): String {
        val id = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ANDROID_ID) ?: "zumo"
        val md = MessageDigest.getInstance("MD5").digest(("zumo-token:" + id).toByteArray())
        return md.joinToString("") { "%02X".format(it) }.take(12)
    }
}
