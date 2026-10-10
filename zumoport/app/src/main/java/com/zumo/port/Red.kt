package com.zumo.port

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Handler
import android.os.Looper

/** Por qué red salen los escaneos. */
enum class Salida(val titulo: String, val nombre: String) {
    MOVIL("📶 Datos móviles", "datos móviles"),
    WIFI("WiFi", "WiFi"),
    AUTO("Automática", "la red del sistema"),
}

/** Lo que se elige en pantalla; lo comparten las pestañas Escanear y Subdominios. */
object Ajustes {
    var salida = Salida.MOVIL
}

/**
 * Hace que todo lo que abre la app (conexiones y consultas DNS) salga por la red elegida, aunque haya otra conectada
 * (por ejemplo datos móviles con el WiFi prendido). Se cuenta cuántos escaneos la usan: la red se suelta cuando termina el último.
 */
class Red(ctx: Context) {
    private val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    private val principal = Handler(Looper.getMainLooper())
    private var callback: ConnectivityManager.NetworkCallback? = null
    private var enUso = 0
    private var salidaActual: Salida? = null

    /** Pide la red y avisa con [listo] (true y un texto, o false y qué hacer). Hay que llamar a [soltar] cuando se termina. */
    fun conectar(salida: Salida, listo: (Boolean, String) -> Unit) {
        if (salida == Salida.AUTO) { enUso++; salidaActual = salida; listo(true, salida.nombre); return }
        if (enUso > 0 && salidaActual == salida) { enUso++; listo(true, salida.nombre); return }
        if (enUso > 0) { listo(false, "Hay otro escaneo en curso por ${salidaActual?.nombre}. Esperá a que termine."); return }
        val transporte = if (salida == Salida.MOVIL) NetworkCapabilities.TRANSPORT_CELLULAR else NetworkCapabilities.TRANSPORT_WIFI
        val pedido = NetworkRequest.Builder()
            .addTransportType(transporte)
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        val sinRed = if (salida == Salida.MOVIL) "No hay datos móviles disponibles. Activá los datos móviles y probá de nuevo."
        else "No hay una conexión WiFi con internet."
        var respondido = false
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                principal.post {
                    if (!respondido && callback === this) {
                        respondido = true
                        val ok = try { cm.bindProcessToNetwork(network) } catch (_: Exception) { false }
                        if (ok) { enUso = 1; salidaActual = salida; listo(true, salida.nombre) }
                        else { soltarCallback(); listo(false, "No se pudo usar ${salida.nombre}.") }
                    }
                }
            }
            override fun onUnavailable() {
                principal.post {
                    if (!respondido && callback === this) { respondido = true; soltarCallback(); listo(false, sinRed) }
                }
            }
        }
        callback = cb
        try {
            cm.requestNetwork(pedido, cb)
        } catch (_: Exception) {
            callback = null
            listo(false, "No se pudo pedir ${salida.nombre} (falta el permiso de red).")
            return
        }
        // si el sistema no contesta, se rinde a los 10 segundos
        principal.postDelayed({
            if (!respondido && callback === cb) { respondido = true; soltarCallback(); listo(false, sinRed) }
        }, 10000)
    }

    /** Un escaneo dejó de usar la red; cuando no queda ninguno, todo vuelve a salir como siempre. */
    fun soltar() {
        if (enUso > 0) enUso--
        if (enUso == 0) { salidaActual = null; soltarCallback() }
    }

    private fun soltarCallback() {
        try { cm.bindProcessToNetwork(null) } catch (_: Exception) {}
        val c = callback
        callback = null
        if (c != null) try { cm.unregisterNetworkCallback(c) } catch (_: Exception) {}
    }
}
