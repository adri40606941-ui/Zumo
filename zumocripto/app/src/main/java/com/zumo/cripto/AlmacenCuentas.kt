package com.zumo.cripto

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Guarda las claves API en el teléfono, cifradas con una llave AES-GCM que vive en el Android Keystore
 * (no se puede sacar del aparato). Nada sale de acá: solo se usan para firmar los pedidos al propio exchange.
 */
class AlmacenCuentas(ctx: Context) {
    private val prefs = ctx.applicationContext.getSharedPreferences("cuentas", Context.MODE_PRIVATE)

    private fun llave(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").also { it.load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return gen.generateKey()
    }

    fun guardar(c: Credencial) {
        val cifrador = Cipher.getInstance("AES/GCM/NoPadding")
        cifrador.init(Cipher.ENCRYPT_MODE, llave())
        val datos = cifrador.doFinal("${c.clave}\n${c.secreto}\n${c.frase}".toByteArray(Charsets.UTF_8))
        prefs.edit().putString(PREFIJO + c.exchange, Firmas.hex(cifrador.iv) + ":" + Firmas.hex(datos)).apply()
    }

    /** La credencial guardada, o null si no hay o no se pudo descifrar (por ejemplo, si se borró la llave del aparato). */
    fun leer(exchange: String): Credencial? = try {
        val g = prefs.getString(PREFIJO + exchange, null) ?: return null
        val (iv, datos) = g.split(":").let { it[0] to it[1] }
        val cifrador = Cipher.getInstance("AES/GCM/NoPadding")
        cifrador.init(Cipher.DECRYPT_MODE, llave(), GCMParameterSpec(128, Firmas.deHex(iv)))
        val partes = String(cifrador.doFinal(Firmas.deHex(datos)), Charsets.UTF_8).split("\n")
        Credencial(exchange, partes[0], partes[1], partes.getOrElse(2) { "" })
    } catch (_: Exception) { null }

    fun borrar(exchange: String) { prefs.edit().remove(PREFIJO + exchange).apply() }

    fun hay(exchange: String): Boolean = prefs.contains(PREFIJO + exchange)

    fun todas(): Map<String, Credencial> =
        Privadas.FUENTES.mapNotNull { f -> leer(f.nombre)?.let { f.nombre to it } }.toMap()

    private companion object {
        const val ALIAS = "zumo_cripto_cuentas"
        const val PREFIJO = "c_"
    }
}
