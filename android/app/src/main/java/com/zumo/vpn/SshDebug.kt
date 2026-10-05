package com.zumo.vpn

/** Guarda las últimas líneas que la librería SSH (JSch) va registrando internamente, para poder
 *  mostrarlas en el error de la app cuando algo falla, sin depender de los logs del servidor. */
object SshDebug {
    private val buf = ArrayDeque<String>()

    @Synchronized
    fun add(linea: String) {
        buf.addLast(linea)
        if (buf.size > 12) buf.removeFirst()
    }

    @Synchronized
    fun ultimas(): String = buf.joinToString("\n")

    @Synchronized
    fun limpiar() = buf.clear()
}
