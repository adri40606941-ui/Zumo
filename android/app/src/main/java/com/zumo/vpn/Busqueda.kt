package com.zumo.vpn

/**
 * Búsqueda automática del servidor donde está el token de este celular. La app no le muestra los servidores
 * al cliente: prueba con el token en cada uno de la lista, empezando por el último donde entró, y se queda
 * con el que lo acepte. Esta clase solo decide el orden y qué hacer después de cada fallo (sin red, para poder probarla).
 */
class Busqueda(candidatos: List<Config>) {
    enum class Paso {
        SIGUIENTE,          // probar ya con el próximo servidor
        REINTENTAR,         // se probaron todos y alguno no respondió: esperar y volver a empezar
        NINGUNO,            // todos respondieron y ninguno conoce el token
        RECHAZADO,          // hay un solo servidor y no acepta el token
    }

    private var orden: List<Config> = candidatos
    private var pos = 0
    private var intentos = 0     // intentos fallidos en la vuelta actual
    private var sinRespuesta = 0 // de esos, los que no respondieron (no fue un "token desconocido")

    init { require(candidatos.isNotEmpty()) }

    val cantidad: Int get() = orden.size
    fun actual(): Config = orden[pos]

    /** Entró: se recuerda este servidor y es el primero que se prueba la próxima vez. */
    fun exito() {
        val c = orden[pos]
        orden = listOf(c) + orden.filter { it !== c }
        pos = 0; intentos = 0; sinRespuesta = 0
    }

    /** [tokenRechazado] = el servidor respondió pero no acepta el token (no es lo mismo que estar caído). */
    fun fallo(tokenRechazado: Boolean): Paso {
        if (orden.size == 1) return if (tokenRechazado) Paso.RECHAZADO else Paso.REINTENTAR
        if (!tokenRechazado) sinRespuesta++
        intentos++
        if (intentos >= orden.size) {
            val ninguno = sinRespuesta == 0
            intentos = 0; sinRespuesta = 0; pos = 0
            return if (ninguno) Paso.NINGUNO else Paso.REINTENTAR
        }
        pos++
        return Paso.SIGUIENTE
    }
}
