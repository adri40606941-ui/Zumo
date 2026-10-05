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

    /** Vuelca bytes crudos (hex + texto) recibidos del servidor, para ver si el transporte
     *  (payload/TLS) está entregando el protocolo SSH tal cual o algo distinto (p.ej. frames
     *  de WebSocket sin "desenvolver", o una respuesta HTTP mal cortada). */
    @Synchronized
    fun addRaw(etiqueta: String, b: ByteArray, off: Int, len: Int) {
        if (len <= 0) return
        val hex = StringBuilder()
        val txt = StringBuilder()
        for (i in off until off + len) {
            val v = b[i].toInt() and 0xff
            hex.append("%02x ".format(v))
            txt.append(if (v in 32..126) v.toChar() else '.')
        }
        add("$etiqueta (${len}B) hex: $hex texto: $txt")
    }
}
