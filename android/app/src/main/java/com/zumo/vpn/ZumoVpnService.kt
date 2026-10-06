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
 * Si el SSH se cae, reintenta cada 2 segundos y al cambiar de red.
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
        Servidores.refrescar(this, prefs)   // servidor de la lista de la app: toma su payload actual
        val cfg = prefs.config
        val user = prefs.user
        val pass = prefs.pass
        crearCanal()
        startForeground(NOTI_ID, notificacion("Conectando..."))
        if (cfg == null || !cfg.valida() || user.isBlank() || pass.isBlank()) {
            ultimoError = "Falta la cuenta: elegí un servidor y poné tu usuario y contraseña, o abrí tu archivo .zs."
            Registro.add("✘ $ultimoError")
            estado = "Error"; prefs.wanted = false
            stopSelf(); return
        }
        if (Perfil.vencida(prefs.exp)) {
            ultimoError = "Tu cuenta venció el ${Perfil.fechaLinda(prefs.exp)}. Pedí la renovación."
            Registro.add("✘ $ultimoError")
            estado = "Error"; prefs.wanted = false
            stopSelf(); return
        }
        Registro.add("Iniciando…")
        activo = true; corriendo = true; desde = System.currentTimeMillis()
        tomarBloqueos()
        Watchdog.programar(this)
        try {
            abrirTun()
            socks = SocksServer(SOCKS_PORT) { tunel }.also { it.start() }
        } catch (e: Exception) {
            ultimoError = "No se pudo iniciar la VPN: ${e.message}"
            Registro.add("✘ $ultimoError")
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

    /** Hay alguna red (datos móviles o Wi-Fi) con salida a internet, sin contar la propia VPN. */
    private fun hayInternet(): Boolean {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        return try {
            cm.allNetworks.any { n ->
                val c = cm.getNetworkCapabilities(n)
                c != null && c.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) && !c.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
            }
        } catch (_: Exception) { true }
    }

    /** Corta la VPN para siempre (hasta que el usuario toque Conectar) y deja el motivo en pantalla. */
    private fun detenerPorError(motivo: String) {
        ultimoError = motivo
        Registro.add("✘ $motivo")
        activo = false
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            Prefs(this).wanted = false
            apagar()
            estado = "Error"
            stopSelf()
        }
    }

    private fun bucle(cfg: Config, user: String, pass: String) {
        var sinInternet = false
        var fallosAuth = 0
        while (activo) {
            if (!hayInternet()) {
                estado = "Sin internet"; conectado = false
                ultimoError = "Sin internet: encendé los datos móviles o el Wi-Fi"
                Registro.add("✘ $ultimoError")
                sinInternet = true; actualizarNoti()
                try { Thread.sleep(1500) } catch (e: InterruptedException) { break }
                continue
            }
            if (sinInternet) { Registro.add("Internet disponible"); sinInternet = false; ultimoError = "" }
            if (Perfil.vencida(Prefs(this).exp)) {
                detenerPorError("Tu cuenta venció el ${Perfil.fechaLinda(Prefs(this).exp)}. Pedí la renovación."); break
            }
            val t = SshTunnel(cfg, user, pass, etapa = { etapaActual = it; Registro.add(it) }, proteger = { sock -> protect(sock) })
            try {
                estado = "Conectando..."; conectado = false; actualizarNoti()
                t.connect()
                // Android ID: se le avisa al servidor qué celular es. Si el administrador vinculó la cuenta a
                // otro celular, el servidor corta y acá se avisa con un mensaje claro (sin reintentar en bucle).
                val idDisp = Dispositivo.id(this)
                if (Dispositivo.verificar(t, idDisp) == Dispositivo.Resultado.RECHAZADO) {
                    t.close()
                    detenerPorError(Dispositivo.textoRechazo(idDisp)); break
                }
                tunel = t
                estado = "Conectado"; conectado = true; ultimoError = ""; actualizarNoti()
                Registro.add("✔ Conectado")
                fallosAuth = 0
                while (activo && t.conectado && !forzarReconexion) Thread.sleep(1000)
                forzarReconexion = false
                if (activo) { ultimoError = "Conexión perdida, reconectando..."; Registro.add("Conexión perdida, reconectando…") }
            } catch (e: InterruptedException) {
                break
            } catch (e: Exception) {
                ultimoError = mensaje(e)
                Registro.add("✘ $ultimoError")
                if (esFalloDeLogin(e)) {
                    fallosAuth++
                    if (fallosAuth >= 3) {
                        t.close()
                        detenerPorError("Usuario o contraseña incorrectos. Pedí tu cuenta de nuevo."); break
                    }
                } else fallosAuth = 0
            } finally {
                conectado = false
                t.close()
                if (tunel === t) tunel = null
            }
            if (!activo) break
            estado = "Reconectando..."; actualizarNoti()
            // Reintento fijo cada 2 segundos: así vuelve rápido cuando el sistema corta la red un
            // momento. Si antes aparece red de nuevo (vigilarRed -> forzarReconexion), reintenta ya.
            try {
                var w = 0L
                while (activo && w < 2000L && !forzarReconexion) { Thread.sleep(250); w += 250 }
            } catch (e: InterruptedException) {
                // Pasa si el usuario presiona Desconectar justo durante la espera entre reintentos
                // (apagar() interrumpe este hilo). Si no se captura acá, tumba toda la app.
                break
            }
            forzarReconexion = false
        }
    }

    private fun esFalloDeLogin(e: Exception): Boolean {
        val m = e.message ?: ""
        return m.contains("Auth fail", true) || m.contains("authentication failures", true) || m.contains("Auth cancel", true)
    }

    private fun mensaje(e: Exception): String {
        val m = e.message ?: e.javaClass.simpleName
        val causa = when {
            e is java.net.UnknownHostException -> "No se encontró el servidor (revisá tu internet)"
            e is java.net.ConnectException -> "No se pudo conectar (puerto cerrado o bloqueado)"
            e is java.net.SocketTimeoutException || m.contains("timeout", true) || m.contains("timed out", true) ->
                "Tiempo agotado: el servidor no respondió"
            esFalloDeLogin(e) -> "Usuario o contraseña incorrectos (o cuenta vencida)"
            m.contains("Connection reset", true) || m.contains("EOF", true) -> "El servidor cortó la conexión"
            else -> if (m.startsWith("El servidor ")) m else "Error de conexión (${e.javaClass.simpleName})"
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
                Registro.add("Se perdió la red")
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
