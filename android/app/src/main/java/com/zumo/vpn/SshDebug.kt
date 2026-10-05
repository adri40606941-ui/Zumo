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

/**
 * Guarda, en un buffer propio (separado del de SshDebug, que JSch va llenando de líneas y
 * puede "empujar afuera" lo que importa), los primeros bytes crudos recibidos del servidor,
 * tal cual llegan, antes de que el payload o JSch los toquen. Sirve para ver si lo que entra
 * es el protocolo SSH posta o viene envuelto en otra cosa (WebSocket, un error de texto plano,
 * una respuesta HTTP mal cortada, etc.).
 */
object CrudoDebug {
    private const val TOPE = 320
    private val buf = ByteArray(TOPE)
    private var total = 0

    @Synchronized
    fun limpiar() { total = 0 }

    @Synchronized
    fun agregar(b: ByteArray, off: Int, len: Int) {
        if (total >= TOPE || len <= 0) return
        val tomar = minOf(len, TOPE - total)
        System.arraycopy(b, off, buf, total, tomar)
        total += tomar
    }

    @Synchronized
    fun volcado(): String {
        if (total == 0) return ""
        val hex = StringBuilder()
        val txt = StringBuilder()
        for (i in 0 until total) {
            val v = buf[i].toInt() and 0xff
            hex.append("%02x ".format(v))
            txt.append(if (v in 32..126) v.toChar() else '.')
        }
        return "[crudo] (${total}B) hex: $hex\ntexto: $txt"
    }
}
