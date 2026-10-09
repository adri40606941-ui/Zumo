package com.zumo.vpn

import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Un servidor puede tener varios dominios o IP. Antes de conectar se prueban todos a la vez (solo un
 * TCP, sin iniciar sesión, así no cuenta como intento fallido) y se ordenan: el que contesta primero
 * va adelante y los que no responden quedan al final. Así se conecta aunque algunos estén caídos.
 */
object Hosts {
    /** Ordena los hosts: primero los que respondieron (por rapidez), después los que no. No espera de más. */
    fun ordenar(hosts: List<String>, esperaMs: Long = 4000, sondear: (String) -> Boolean): List<String> {
        if (hosts.size < 2) return hosts
        val vivos = CopyOnWriteArrayList<String>()
        val primero = CountDownLatch(1)
        val todos = CountDownLatch(hosts.size)
        for (h in hosts) Thread({
            try { if (sondear(h)) { vivos.add(h); primero.countDown() } } catch (_: Exception) {} finally { todos.countDown() }
        }, "zumo-host").also { it.isDaemon = true }.start()
        // en cuanto uno responde se sigue; si ninguno responde se espera hasta que todos terminen o se agote el tiempo
        val inicio = System.currentTimeMillis()
        while (vivos.isEmpty() && todos.count > 0 && System.currentTimeMillis() - inicio < esperaMs) {
            primero.await(100, TimeUnit.MILLISECONDS)
        }
        val orden = vivos.toList()
        return orden + hosts.filter { it !in orden }
    }

    /** Tiempo de conexión que se le da a un host que ya no contestó al sondeo (si volvió, igual entra). */
    const val TIMEOUT_CAIDO = 5000

    /**
     * Sondea TODOS los candidatos a la vez (un TCP a cada host, sin iniciar sesión) y los ordena: primero el último
     * servidor donde se entró (si contesta), después los que contestan, por rapidez; al final los que no
     * contestaron, con poco tiempo de espera. Así la búsqueda del token no pierde tiempo en servidores caídos.
     * Espera como máximo [esperaMs] en total, sin importar cuántos servidores haya.
     */
    fun ordenarTodos(cands: List<Config>, ultimo: String, esperaMs: Long = 3500, sondear: (Config) -> Boolean): List<Config> {
        if (cands.size < 2) return cands
        val vivos = CopyOnWriteArrayList<Config>()
        val pendientes = CountDownLatch(cands.size)
        for (c in cands) Thread({
            try { if (sondear(c)) vivos.add(c) } catch (_: Exception) {} finally { pendientes.countDown() }
        }, "zumo-sonda").also { it.isDaemon = true }.start()
        pendientes.await(esperaMs, TimeUnit.MILLISECONDS)
        val vivosAhora = vivos.toList()
        val muertos = cands.filter { c -> vivosAhora.none { it === c } }.map { it.copy(conTimeout = TIMEOUT_CAIDO) }
        val primeros = vivosAhora.filter { it.name == ultimo }
        return primeros + vivosAhora.filter { it.name != ultimo } + muertos
    }

    /**
     * Como [ordenarTodos], pero si el último host donde se conectó ([nombre] + [host]) sigue contestando, sale ya con ese
     * primero, sin esperar a sondear los demás. Si no contesta en 2 segundos, se ordena como siempre.
     */
    fun ordenarConUltimo(cands: List<Config>, nombre: String, host: String, esperaMs: Long = 3500, sondear: (Config) -> Boolean): List<Config> {
        val rec = if (host.isBlank()) null else cands.firstOrNull { it.name == nombre && it.host == host }
        if (rec != null && cands.size > 1) {
            val vivo = java.util.concurrent.atomic.AtomicBoolean(false)
            val listo = CountDownLatch(1)
            Thread({
                try { if (sondear(rec)) vivo.set(true) } catch (_: Exception) {} finally { listo.countDown() }
            }, "zumo-sonda-ultimo").also { it.isDaemon = true }.start()
            listo.await(2000, TimeUnit.MILLISECONDS)
            if (vivo.get()) return listOf(rec) + cands.filter { it !== rec }
        }
        return ordenarTodos(cands, nombre, esperaMs, sondear)
    }

    /** Un TCP al host (ya sacado de la VPN con [proteger]); true si conectó. */
    fun sondeoTcp(c: Config, proteger: (Socket) -> Unit): Boolean = try {
        Socket().use { s -> proteger(s); s.connect(InetSocketAddress(c.host, c.sshPort), 3000); true }
    } catch (_: Exception) { false }
}
