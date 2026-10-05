package com.zumo.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.VpnService
import android.net.wifi.WifiManager
import android.os.Build
import android.os.ParcelFileDescriptor
import android.os.PowerManager
import hev.htproxy.TProxyService
import java.io.File

/**
 * Servicio de VPN en primer plano. Mantiene el TUN abierto, el túnel SSH y el proxy SOCKS.
 * Si el SSH se cae, se reconecta solo con espera creciente y al cambiar de red.
 */
class ZumoVpnService : VpnService() {

    companion object {
        const val ACTION_START = "com.zumo.vpn.START"
        const val ACTION_STOP = "com.zumo.vpn.STOP"
        const val SOCKS_PORT = 10808
        private const val CHANNEL = "zumo_vpn"
        private const val NOTI_ID = 1

        @Volatile var estado: String = "Desconectado"
        @Volatile var conectado: Boolean = false
        @Volatile var ultimoError: String = ""
        @Volatile var corriendo: Boolean = false
        @Volatile var desde: Long = 0L
        @Volatile var etapaActual: String = ""

        fun iniciar(ctx: Context) {
            val i = Intent(ctx, ZumoVpnService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }

        fun detener(ctx: Context) {
            ctx.startService(Intent(ctx, ZumoVpnService::class.java).setAction(ACTION_STOP))
        }
    }

    private var tun: ParcelFileDescriptor? = null
    private var tproxy: TProxyService? = null
    private var socks: SocksServer? = null
    @Volatile private var tunel: SshTunnel? = null
    @Volatile private var activo = false
    @Volatile private var forzarReconexion = false
    private var hilo: Thread? = null
    private var wake: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null
    private var cb: ConnectivityManager.NetworkCallback? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Prefs(this).wanted = false
            apagar()
            stopSelf()
            return START_NOT_STICKY
        }
        // START o reinicio del sistema (intent nulo): si el usuario la quiere encendida, se enciende
        val prefs = Prefs(this)
        if (intent == null && !prefs.wanted) { stopSelf(); return START_NOT_STICKY }
        prefs.wanted = true
        if (!activo) encender()
        return START_STICKY
    }

    override fun onRevoke() {
        Prefs(this).wanted = false
        apagar()
        stopSelf()
    }

    override fun onDestroy() {
        apagar()
        super.onDestroy()
    }

    private fun encender() {
        val prefs = Prefs(this)
        val cfg = prefs.config
        val user = if (prefs.useHwid) Hwid.get(this) else prefs.user
        val pass = if (prefs.useHwid) Hwid.get(this) else prefs.pass
        crearCanal()
        startForeground(NOTI_ID, notificacion("Conectando..."))
        if (cfg == null || !cfg.valida() || user.isBlank() || pass.isBlank()) {
            ultimoError = "Falta la configuración, el usuario o la contraseña"
            estado = "Error"; prefs.wanted = false
            stopSelf(); return
        }
        activo = true; corriendo = true; desde = System.currentTimeMillis()
        tomarBloqueos()
        try {
            abrirTun()
            socks = SocksServer(SOCKS_PORT) { tunel }.also { it.start() }
        } catch (e: Exception) {
            ultimoError = "No se pudo iniciar la VPN: ${e.message}"
            estado = "Error"; prefs.wanted = false
            apagar(); stopSelf(); return
        }
        vigilarRed()
        hilo = Thread({ bucle(cfg, user, pass) }, "zumo-ssh").also { it.start() }
    }

    private fun abrirTun() {
        val b = Builder()
            .setSession("Zumo VPN")
            .setMtu(1500)
            .addAddress("198.18.0.1", 32)
            .addRoute("0.0.0.0", 0)
            .addDnsServer("198.18.0.2")
            .setBlocking(false)
        try { b.addDisallowedApplication(packageName) } catch (_: Exception) {}
        tun = b.establish() ?: throw IllegalStateException("el sistema negó la VPN")
        val yml = File(filesDir, "tproxy.yml")
        yml.writeText(
            """
            tunnel:
              mtu: 1500
              ipv4: 198.18.0.1
            socks5:
              port: $SOCKS_PORT
              address: 127.0.0.1
              udp: 'udp'
            mapdns:
              address: 198.18.0.2
              port: 53
              network: 100.64.0.0
              netmask: 255.192.0.0
              cache-size: 10000
            misc:
              tcp-read-write-timeout: 300000
              connect-timeout: 10000
              log-level: warn
            """.trimIndent() + "\n"
        )
        tproxy = TProxyService().also { it.TProxyStartService(yml.absolutePath, tun!!.fd) }
    }

    private fun bucle(cfg: Config, user: String, pass: String) {
        var espera = 2000L
        while (activo) {
            val t = SshTunnel(cfg, user, pass, etapa = { etapaActual = it }, proteger = { sock -> protect(sock) })
            try {
                estado = "Conectando..."; conectado = false; actualizarNoti()
                t.connect()
                tunel = t
                estado = "Conectado"; conectado = true; ultimoError = ""; actualizarNoti()
                espera = 2000L
                while (activo && t.conectado && !forzarReconexion) Thread.sleep(1000)
                forzarReconexion = false
                if (activo) ultimoError = "Conexión perdida, reconectando..."
            } catch (e: InterruptedException) {
                break
            } catch (e: Exception) {
                ultimoError = mensaje(e)
            } finally {
                conectado = false
                t.close()
                if (tunel === t) tunel = null
            }
            if (!activo) break
            estado = "Reconectando..."; actualizarNoti()
            var w = 0L
            while (activo && w < espera && !forzarReconexion) { Thread.sleep(500); w += 500 }
            forzarReconexion = false
            espera = minOf(espera * 2, 20000L)
        }
    }

    private fun mensaje(e: Exception): String {
        val m = e.message ?: e.javaClass.simpleName
        val causa = when {
            e is java.net.UnknownHostException -> "No se encontró el dominio \"$m\" (revisa el nombre o tu internet)"
            e is java.net.ConnectException -> "No se pudo conectar (puerto cerrado o bloqueado)"
            e is java.net.SocketTimeoutException || m.contains("timeout", true) || m.contains("timed out", true) ->
                "Tiempo agotado: el servidor no respondió"
            m.contains("Auth fail", true) -> "Usuario o contraseña incorrectos (o vencido)"
            m.contains("Connection reset", true) || m.contains("EOF", true) -> "El servidor cortó la conexión"
            else -> m
        }
        return if (etapaActual.isBlank()) causa else "$etapaActual → $causa"
    }

    private fun vigilarRed() {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val req = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
            .build()
        val c = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                if (!conectado) forzarReconexion = true
            }
            override fun onLost(network: Network) {
                forzarReconexion = true
            }
        }
        cb = c
        try { cm.registerNetworkCallback(req, c) } catch (_: Exception) {}
    }

    private fun tomarBloqueos() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "zumo:vpn").apply { setReferenceCounted(false); acquire() }
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        wifi = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "zumo:wifi").apply { setReferenceCounted(false); acquire() }
    }

    private fun apagar() {
        activo = false; corriendo = false; conectado = false; estado = "Desconectado"
        try { (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).unregisterNetworkCallback(cb!!) } catch (_: Exception) {}
        cb = null
        hilo?.interrupt(); hilo = null
        try { tunel?.close() } catch (_: Exception) {}
        tunel = null
        try { socks?.stop() } catch (_: Exception) {}
        socks = null
        try { tproxy?.TProxyStopService() } catch (_: Throwable) {}
        tproxy = null
        try { tun?.close() } catch (_: Exception) {}
        tun = null
        try { wake?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { wifi?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { stopForeground(STOP_FOREGROUND_REMOVE) } catch (_: Exception) {}
    }

    private fun crearCanal() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Zumo VPN", NotificationManager.IMPORTANCE_LOW))
        }
    }

    private fun notificacion(txt: String): Notification {
        val abrir = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val parar = PendingIntent.getService(
            this, 1, Intent(this, ZumoVpnService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE
        )
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
        return b.setContentTitle("Zumo VPN").setContentText(txt)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentIntent(abrir).setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Desconectar", parar).build())
            .build()
    }

    private fun actualizarNoti() {
        try { (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(NOTI_ID, notificacion(estado)) } catch (_: Exception) {}
    }
}
