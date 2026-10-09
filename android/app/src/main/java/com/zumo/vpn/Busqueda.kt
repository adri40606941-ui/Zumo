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
    private val caidos = HashSet<String>()      // servidores (por nombre) que no respondieron en esta vuelta
    private val rechazados = HashSet<String>()  // servidores que respondieron pero no conocen el token

    init { require(candidatos.isNotEmpty()) }

    val cantidad: Int get() = orden.size
    fun actual(): Config = orden[pos]

    /** Entró: se recuerda este servidor y es el primero que se prueba la próxima vez. */
    fun exito() {
        val c = orden[pos]
        orden = listOf(c) + orden.filter { it !== c }
        pos = 0; caidos.clear(); rechazados.clear()
    }

    /**
     * [tokenRechazado] = el servidor respondió pero no acepta el token (no es lo mismo que estar caído).
     * Un servidor con varios hosts son varios candidatos con el mismo nombre: si uno cae se prueba el
     * otro host; si el servidor rechaza el token, no se insiste con sus otros hosts.
     */
    fun fallo(tokenRechazado: Boolean): Paso {
        val nombre = orden[pos].name
        if (tokenRechazado) rechazados.add(nombre) else caidos.add(nombre)
        var n = pos + 1
        while (n < orden.size && orden[n].name in rechazados) n++
        if (n < orden.size) { pos = n; return Paso.SIGUIENTE }
        // se terminó la vuelta
        val algunoCaido = caidos.any { it !in rechazados }
        val servidores = orden.map { it.name }.distinct().size
        pos = 0; caidos.clear(); rechazados.clear()
        return when {
            algunoCaido -> Paso.REINTENTAR
            servidores == 1 -> Paso.RECHAZADO
            else -> Paso.NINGUNO
        }
    }
}
