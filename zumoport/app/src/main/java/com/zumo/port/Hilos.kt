package com.zumo.port

import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Lo que comparten los trabajos en paralelo de la app (subdominios, pruebas HTTP) para que Detener funcione siempre. */
internal object Hilos {
    /** Hilos que no impiden cerrar la app: si una consulta DNS o una conexión se cuelga, nadie la espera. */
    fun crear(n: Int): ExecutorService = Executors.newFixedThreadPool(n) { r -> Thread(r, "zumoport-hilo").also { it.isDaemon = true } }

    /**
     * Espera a que el grupo de hilos termine, pero vuelve ya si se canceló. Una consulta DNS o una conexión en curso no se
     * puede interrumpir desde afuera (puede tardar decenas de segundos): esos hilos quedan terminando solos y sus resultados
     * se descartan, pero quien pidió detener no espera.
     */
    fun esperar(pool: ExecutorService, minutos: Long, cancelado: AtomicBoolean) {
        val limite = System.currentTimeMillis() + minutos * 60_000
        while (!pool.isTerminated && System.currentTimeMillis() < limite) {
            if (cancelado.get()) { pool.shutdownNow(); return }
            try { pool.awaitTermination(150, TimeUnit.MILLISECONDS) } catch (_: InterruptedException) { pool.shutdownNow(); return }
        }
        if (!pool.isTerminated) pool.shutdownNow()
    }
}
