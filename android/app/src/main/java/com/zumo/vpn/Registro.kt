package com.zumo.vpn

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Registro de la conexión que ve el usuario: qué está pasando y por qué falla (sin datos del servidor). */
object Registro {
    private const val MAX = 80
    private const val ANCHO_HORA = 9              // "HH:mm:ss "
    private val lineas = ArrayDeque<String>()
    private val fmt = SimpleDateFormat("HH:mm:ss", Locale.US)

    /** Anota una línea; si es igual a la última no se repite. */
    @Synchronized
    fun add(msg: String) {
        if (msg.isBlank()) return
        if (lineas.lastOrNull()?.drop(ANCHO_HORA) == msg) return
        lineas.addLast(fmt.format(Date()) + " " + msg)
        while (lineas.size > MAX) lineas.removeFirst()
    }

    @Synchronized
    fun texto(n: Int = 12): String = lineas.toList().takeLast(n).joinToString("\n")

    /** Detalle técnico del último error (no se muestra en pantalla). */
    @Volatile var detalle: String = ""

    @Synchronized
    fun limpiar() = lineas.clear()
}
