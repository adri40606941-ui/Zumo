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

    /** Reordena los candidatos: dentro de cada servidor con varios hosts, el más rápido primero. El orden entre servidores no cambia. */
    fun ordenarCandidatos(cands: List<Config>, sondear: (Config) -> Boolean): List<Config> {
        val nombres = cands.map { it.name }.distinct()
        return nombres.flatMap { n ->
            val g = cands.filter { it.name == n }
            if (g.size < 2) g else {
                val porHost = g.associateBy { it.host }
                ordenar(g.map { it.host }) { h -> sondear(porHost.getValue(h)) }.map { porHost.getValue(it) }
            }
        }
    }

    /** Un TCP al host (ya sacado de la VPN con [proteger]); true si conectó. */
    fun sondeoTcp(c: Config, proteger: (Socket) -> Unit): Boolean = try {
        Socket().use { s -> proteger(s); s.connect(InetSocketAddress(c.host, c.sshPort), 3500); true }
    } catch (_: Exception) { false }
}
