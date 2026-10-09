package com.zumo.vpn

import android.content.Context
import java.io.File

/**
 * Guarda el motivo cuando la app se cierra sola por un error, y lo muestra la próxima vez que se abre,
 * para poder arreglarlo sin depender de herramientas de desarrollo. Solo guarda el error técnico
 * (clase, mensaje y primeras líneas de la pila), nunca cuentas ni contraseñas.
 */
object CrashLog {
    private const val ARCHIVO = "ultimo-cierre.txt"
    private const val LINEAS = 18

    fun resumen(t: Throwable): String {
        val sb = StringBuilder()
        var c: Throwable? = t
        var n = 0
        while (c != null && n < 3) {
            if (n > 0) sb.append("Causa: ")
            sb.append(c.javaClass.name).append(": ").append(c.message ?: "").append('\n')
            c.stackTrace.take(if (n == 0) LINEAS else 6).forEach { sb.append("  ").append(it).append('\n') }
            c = c.cause; n++
        }
        return sb.toString().take(4000)
    }

    fun guardar(ctx: Context, t: Throwable) {
        try {
            File(ctx.applicationContext.filesDir, ARCHIVO).writeText(resumen(t))
        } catch (_: Exception) {}
    }

    /** Hace que un cierre por error quede guardado. Se llama al arrancar la app y el servicio. */
    fun instalar(ctx: Context) {
        val app = ctx.applicationContext
        val anterior = Thread.getDefaultUncaughtExceptionHandler()
        if (anterior is Manejador) return
        Thread.setDefaultUncaughtExceptionHandler(Manejador(app, anterior))
    }

    private class Manejador(val ctx: Context, val anterior: Thread.UncaughtExceptionHandler?) : Thread.UncaughtExceptionHandler {
        override fun uncaughtException(t: Thread, e: Throwable) {
            guardar(ctx, e)
            anterior?.uncaughtException(t, e)
        }
    }

    /** El error del último cierre (y lo borra), o null si no hubo. */
    fun leer(ctx: Context): String? = try {
        val f = File(ctx.applicationContext.filesDir, ARCHIVO)
        if (f.isFile) f.readText().also { f.delete() }.ifBlank { null } else null
    } catch (_: Exception) { null }
}
