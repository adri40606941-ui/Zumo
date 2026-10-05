package com.zumo.vpn

import android.annotation.SuppressLint
import android.content.Context
import android.provider.Settings
import java.security.MessageDigest

object Hwid {
    /** 32 caracteres hexadecimales, fijos para este teléfono. Es el usuario y la clave del modo HWID. */
    @SuppressLint("HardwareIds")
    fun get(ctx: Context): String {
        val id = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ANDROID_ID) ?: "zumo"
        val md = MessageDigest.getInstance("MD5").digest(("zumo-hwid:" + id).toByteArray())
        return md.joinToString("") { "%02x".format(it) }
    }
}
