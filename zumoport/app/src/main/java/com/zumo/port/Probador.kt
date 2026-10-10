package com.zumo.port

import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Lo que contestó un puerto abierto de un subdominio cuando se lo probó por HTTP/HTTPS (o su saludo, si no habla web). */
class PruebaHttp(
    val ip: String,
    val puerto: Int,
    val tipo: Hallazgo.Tipo?,       // null = ya no contestó
    val http: Int,                  // código HTTP (0 si no habló HTTP)
    val texto: String,              // "HTTP 200 · cloudflare", "HTTPS 301 · nginx → https://…", "TLS", "banner · SSH-2.0…", "sin respuesta"
    val ms: Long,
) {
    /** Respuesta web correcta (2xx o 3xx). */
    val ok: Boolean get() = http in 200..399
    val hablaWeb: Boolean get() = http > 0 || tipo == Hallazgo.Tipo.TLS_SIN_WEB
    val https: Boolean get() = tipo == Hallazgo.Tipo.TLS_WEB || tipo == Hallazgo.Tipo.TLS_SIN_WEB

    /** "443 · HTTPS 200 · cloudflare · 120 ms" */
    fun linea(): String = "$puerto  ·  $texto" + if (tipo != null) "  ·  $ms ms" else ""
}

/**
 * Prueba cada subdominio que tiene puertos abiertos: por cada IP y cada uno de esos puertos pide la página de inicio
 * (HEAD / con el nombre del subdominio en Host y, si es HTTPS, en SNI) y anota el código de respuesta. Es lo que usa el
 * botón «Probar» de la pestaña Subdominios, cuando ya terminó el escaneo.
 */
class ProbadorHttp(
    private val probar: (String, String, Int) -> Hallazgo? = { nombre, ip, puerto -> ESCANER.probar(nombre, ip, puerto) },
) {
    private val cancelado = AtomicBoolean(false)
    fun cancelar() { cancelado.set(true) }

    class Tarea(val sub: Subdominio, val ip: String, val puerto: Int)

    /** Las pruebas por hacer: una por subdominio, IP y puerto abierto (hasta [MAX_PRUEBAS]). */
    fun armar(subs: List<Subdominio>): List<Tarea> {
        val out = ArrayList<Tarea>()
        for (s in subs) for ((ip, puertos) in s.puertosPorIp) for (p in puertos) {
            if (out.size >= MAX_PRUEBAS) return out
            out.add(Tarea(s, ip, p))
        }
        return out
    }

    fun probar(subs: List<Subdominio>, alAvanzar: (Int, Int) -> Unit, alActualizar: () -> Unit) {
        cancelado.set(false)
        val tareas = armar(subs)
        val faltan = ConcurrentHashMap<Subdominio, AtomicInteger>()
        for (t in tareas) faltan.getOrPut(t.sub) { AtomicInteger(0) }.incrementAndGet()
        for (s in subs) { s.pruebas.clear(); s.probando = faltan.containsKey(s) }
        val hechas = AtomicInteger(0)
        alAvanzar(0, tareas.size)
        alActualizar()
        val pool = Hilos.crear(HILOS)
        try {
            for (t in tareas) {
                pool.execute {
                    try {
                        if (!cancelado.get()) {
                            val t0 = System.nanoTime()
                            val h = try { probar(t.sub.nombre, t.ip, t.puerto) } catch (_: Exception) { null }
                            val ms = (System.nanoTime() - t0) / 1_000_000
                            if (!cancelado.get()) {
                                t.sub.pruebas.add(
                                    if (h == null) PruebaHttp(t.ip, t.puerto, null, 0, "sin respuesta", ms)
                                    else PruebaHttp(t.ip, t.puerto, h.tipo, h.http, h.resumen(), ms)
                                )
                                alActualizar()
                            }
                        }
                    } catch (_: Exception) {
                    } finally {
                        if (faltan[t.sub]!!.decrementAndGet() == 0) { t.sub.probando = false; alActualizar() }
                        alAvanzar(hechas.incrementAndGet(), tareas.size)
                    }
                }
            }
        } finally {
            pool.shutdown()
            Hilos.esperar(pool, 20, cancelado)
        }
        for (s in subs) s.probando = false
        alActualizar()
    }

    companion object {
        const val MAX_PRUEBAS = 4000
        const val HILOS = 64
        private val ESCANER by lazy { Escaner() }
    }
}
