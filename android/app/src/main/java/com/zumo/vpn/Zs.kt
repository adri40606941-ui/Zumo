package com.zumo.vpn

import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Formato del archivo de cuenta (.zs) que genera el bot de Telegram y abre la app:
 *   "ZS1" + IV (12 bytes) + AES-256-GCM(JSON del perfil) + etiqueta (16 bytes)
 * La clave sale de SHA-256("ZUMO-ZS-1:" + SECRETO). El mismo SECRETO lo usa el bot (ZS_SECRET en
 * /etc/zumo/bot.env). Sirve para que nadie abra el archivo y lea el payload o la clave a simple vista;
 * como el secreto viaja dentro de la app, no es protección contra alguien que desarme el APK.
 */
object Zs {
    const val SECRETO = "f14a3636d2aef23c893604756b861ea2"
    private val MAGIA = "ZS1".toByteArray(Charsets.US_ASCII)
    private val MAGIA_LISTA = "ZL1".toByteArray(Charsets.US_ASCII)

    private fun clave(secreto: String) =
        SecretKeySpec(MessageDigest.getInstance("SHA-256").digest(("ZUMO-ZS-1:" + secreto).toByteArray(Charsets.UTF_8)), "AES")

    fun cifrar(p: Perfil, secreto: String = SECRETO, iv: ByteArray = ByteArray(12).also { SecureRandom().nextBytes(it) }): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, clave(secreto), GCMParameterSpec(128, iv))
        return MAGIA + iv + c.doFinal(p.toJson().toString().toByteArray(Charsets.UTF_8))
    }

    /** Devuelve el perfil, o null si el archivo no es un .zs válido (o fue alterado). */
    fun descifrar(datos: ByteArray, secreto: String = SECRETO): Perfil? = try {
        if (datos.size < MAGIA.size + 12 + 16 || !datos.copyOfRange(0, MAGIA.size).contentEquals(MAGIA)) null
        else {
            val iv = datos.copyOfRange(MAGIA.size, MAGIA.size + 12)
            val c = Cipher.getInstance("AES/GCM/NoPadding")
            c.init(Cipher.DECRYPT_MODE, clave(secreto), GCMParameterSpec(128, iv))
            val json = String(c.doFinal(datos, MAGIA.size + 12, datos.size - MAGIA.size - 12), Charsets.UTF_8)
            Perfil.desdeTexto(json)
        }
    } catch (e: Exception) {
        null
    }

    /**
     * Lista de servidores que viaja dentro del APK (assets/servidores.bin):
     *   "ZL1" + IV (12 bytes) + AES-256-GCM(texto de android/servidores.txt) + etiqueta (16 bytes)
     * La cifra Gradle al compilar (tarea cifrarServidores) con el mismo SECRETO, para que los
     * payloads no se lean a simple vista abriendo el APK. Devuelve el texto, o null si no es válido.
     */
    fun descifrarLista(datos: ByteArray, secreto: String = SECRETO): String? = try {
        if (datos.size < MAGIA_LISTA.size + 12 + 16 || !datos.copyOfRange(0, MAGIA_LISTA.size).contentEquals(MAGIA_LISTA)) null
        else {
            val iv = datos.copyOfRange(MAGIA_LISTA.size, MAGIA_LISTA.size + 12)
            val c = Cipher.getInstance("AES/GCM/NoPadding")
            c.init(Cipher.DECRYPT_MODE, clave(secreto), GCMParameterSpec(128, iv))
            String(c.doFinal(datos, MAGIA_LISTA.size + 12, datos.size - MAGIA_LISTA.size - 12), Charsets.UTF_8)
        }
    } catch (e: Exception) {
        null
    }
}
