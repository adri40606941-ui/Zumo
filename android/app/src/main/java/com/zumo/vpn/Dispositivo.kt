package com.zumo.vpn

import android.content.Context
import android.provider.Settings
import com.jcraft.jsch.ChannelDirectTCPIP
import java.io.InputStream

/**
 * Android ID del celular y verificación contra el servicio zumo-id del servidor.
 *
 * Al conectar, ya con el usuario y la contraseña aceptados, la app abre un canal interno del túnel
 * SSH hacia 127.0.0.1:7390 (el servicio zumo-id de la VPS) y manda "ZID1 <android_id>". El servicio
 * anota el ID de ese usuario y, si el administrador lo vinculó a otro celular, contesta DENY y corta
 * la sesión. El cliente no hace nada especial: conecta como siempre con su usuario y contraseña.
 *
 * El ANDROID_ID es el mismo aunque se borre y se vuelva a instalar la app, mientras el APK se firme
 * con la misma clave. Cambia con un reseteo de fábrica, en una app clonada ("Dual apps") o en otro
 * perfil de usuario del celular.
 *
 * Nunca se corta a nadie por un problema de la verificación: si el servidor no tiene el servicio
 * (VPS sin actualizar), no contesta o el celular no da un ID usable, la conexión sigue normal.
 */
object Dispositivo {
    const val PUERTO = 7390

    enum class Resultado { OK, RECHAZADO, SIN_SERVICIO }

    private val HEX = Regex("^[0-9a-f]{8,32}$")

    /** Android ID en minúsculas; vacío si el sistema no entrega uno usable. */
    fun id(ctx: Context): String {
        val v = try {
            Settings.Secure.getString(ctx.applicationContext.contentResolver, Settings.Secure.ANDROID_ID)
        } catch (_: Exception) { null }
        return normalizar(v)
    }

    fun normalizar(v: String?): String {
        val s = (v ?: "").trim().lowercase()
        return if (HEX.matches(s)) s else ""
    }

    /** Lo que se le manda al servicio. */
    fun mensaje(id: String): String = "ZID1 $id\n"

    fun interpretar(linea: String?): Resultado = when (linea?.trim()) {
        "OK" -> Resultado.OK
        "DENY" -> Resultado.RECHAZADO
        else -> Resultado.SIN_SERVICIO
    }

    /** Texto para el cliente cuando el servidor lo rechaza por estar vinculado a otro celular. */
    fun textoRechazo(id: String): String =
        "Esta cuenta está vinculada a otro celular. Pedí que te la pasen a este celular y mandá este código: $id"

    /**
     * Manda el ID por un canal interno del túnel y espera la respuesta. [esperaMs] limita el tiempo
     * de la respuesta (el servicio contesta al instante).
     */
    fun verificar(t: SshTunnel, id: String, puerto: Int = PUERTO, esperaMs: Long = 5000): Resultado {
        if (id.isEmpty()) return Resultado.SIN_SERVICIO
        var ch: ChannelDirectTCPIP? = null
        return try {
            ch = t.abrirCanal("127.0.0.1", puerto) ?: return Resultado.SIN_SERVICIO
            val entrada = ch.inputStream
            val salida = ch.outputStream
            ch.connect(esperaMs.toInt())
            salida.write(mensaje(id).toByteArray(Charsets.US_ASCII))
            salida.flush()
            interpretar(leerLinea(entrada, esperaMs))
        } catch (e: InterruptedException) {
            throw e   // el usuario apagó la VPN: que lo maneje quien llama
        } catch (e: Exception) {
            Resultado.SIN_SERVICIO
        } finally {
            try { ch?.disconnect() } catch (_: Exception) {}
        }
    }

    private fun leerLinea(entrada: InputStream, esperaMs: Long): String? {
        val fin = System.currentTimeMillis() + esperaMs
        val sb = StringBuilder()
        while (System.currentTimeMillis() < fin) {
            if (entrada.available() > 0) {
                val b = entrada.read()
                if (b < 0) break
                if (b == '\n'.code) return sb.toString()
                if (sb.length < 16) sb.append(b.toChar())
            } else {
                Thread.sleep(20)
            }
        }
        return sb.toString().ifEmpty { null }
    }
}
