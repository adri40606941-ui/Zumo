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
        @Volatile var velocidad: String = ""
        @Volatile var datosUsados: String = ""

        /** Da formato legible (B/KB/MB/GB) a una cantidad de bytes. */
        fun formatoDatos(bytes: Long): String {
            if (bytes < 1024) return "$bytes B"
            var v = bytes.toDouble()
            var i = -1
            val unidades = "KMGT"
            while (v >= 1024 && i < unidades.length - 1) { v /= 1024; i++ }
            return "%.1f %cB".format(v, unidades[i])
        }

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
    @Volatile private var cfgActual: Config? = null
    private var hilo: Thread? = null
    private var monitor: Thread? = null
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
        Watchdog.programar(this)
        try {
            abrirTun()
            socks = SocksServer(SOCKS_PORT) { tunel }.also { it.start() }
        } catch (e: Exception) {
            ultimoError = "No se pudo iniciar la VPN: ${e.message}"
            estado = "Error"; prefs.wanted = false
            apagar(); stopSelf(); return
        }
        cfgActual = cfg
        vigilarRed()
        hilo = Thread({ bucle(cfg, user, pass) }, "zumo-ssh").also { it.start() }
        monitor = Thread({ vigilar(cfg) }, "zumo-monitor").also { it.start() }
    }

    /**
     * Corre aparte de la conexión SSH: actualiza la velocidad en la notificación y, cada 20
     * segundos, prueba si el túnel realmente responde (no solo si "parece" conectado). Esto
     * detecta el caso típico de una red móvil que corta en silencio: la sesión SSH queda sin
     * avisar que murió y la app se queda "conectada" sin pasar datos.
     */
    private fun vigilar(cfg: Config) {
        var txAnt = 0L; var rxAnt = 0L; var t0 = System.currentTimeMillis()
        var tick = 0
        var fallos = 0
        try {
            while (activo) {
                Thread.sleep(2000)
                if (!activo) break
                val st = try { tproxy?.TProxyGetStats() } catch (_: Throwable) { null }
                if (st != null && st.size >= 4 && conectado) {
                    val t1 = System.currentTimeMillis()
                    val dt = ((t1 - t0).coerceAtLeast(1)) / 1000.0
                    val tx = st[1]; val rx = st[3]
                    val subMbps = (tx - txAnt).coerceAtLeast(0) * 8 / dt / 1_000_000
                    val bajMbps = (rx - rxAnt).coerceAtLeast(0) * 8 / dt / 1_000_000
                    velocidad = "↓ %.1f  ↑ %.1f Mbps".format(bajMbps, subMbps)
                    datosUsados = formatoDatos(tx + rx)
                    txAnt = tx; rxAnt = rx; t0 = t1
                    actualizarNoti()
                }
                tick++
                if (tick % 10 == 0 && conectado) {   // cada ~20s
                    val t = tunel
                    val viva = t != null && probarSalud(t, cfg)
                    if (viva) fallos = 0 else {
                        fallos++
                        if (fallos >= 2) { forzarReconexion = true; fallos = 0 }
                    }
                }
            }
        } catch (e: InterruptedException) {
            // El hilo se interrumpe a propósito al desconectar (apagar() llama a monitor?.interrupt()).
            // Si no se captura acá, la excepción sube sin control y tumba toda la app.
            return
        }
    }

    /** Abre un canal de prueba hacia el propio servidor: si no responde, el túnel está muerto aunque parezca activo. */
    private fun probarSalud(t: SshTunnel, cfg: Config): Boolean {
        val ch = try { t.abrirCanal(cfg.host, cfg.sshPort) } catch (_: Exception) { return false } ?: return false
        return try { ch.connect(8000); true } catch (_: Exception) { false } finally { try { ch.disconnect() } catch (_: Exception) {} }
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
        if (!Prefs(this).wanted) Watchdog.cancelar(this)
        try { (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).unregisterNetworkCallback(cb!!) } catch (_: Exception) {}
        cb = null
        hilo?.interrupt(); hilo = null
        monitor?.interrupt(); monitor = null
        velocidad = ""; datosUsados = ""
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
            // IMPORTANCE_DEFAULT (no LOW): en HiOS (Tecno/Infinix/itel) una notificación "silenciosa"
            // hace que el gestor de batería trate a la app como inactiva y la mate antes.
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Zumo VPN", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    private fun notificacion(txt: String): Notification {
        val abrir = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val parar = PendingIntent.getService(
            this, 1, Intent(this, ZumoVpnService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE
        )
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
        val cuerpo = if (conectado && velocidad.isNotBlank()) "$txt  ·  $velocidad" else txt
        b.setContentTitle("Zumo VPN").setContentText(cuerpo)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentIntent(abrir).setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .addAction(Notification.Action.Builder(null, "Desconectar", parar).build())
        if (Build.VERSION.SDK_INT < 26) @Suppress("DEPRECATION") b.setPriority(Notification.PRIORITY_HIGH)
        return b.build()
    }

    private fun actualizarNoti() {
        try { (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(NOTI_ID, notificacion(estado)) } catch (_: Exception) {}
    }
}
