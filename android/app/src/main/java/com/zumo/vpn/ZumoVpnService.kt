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
import java.util.concurrent.Executors

/**
 * Servicio de VPN en primer plano. Mantiene el TUN abierto, el túnel SSH y el proxy SOCKS.
 * Si el SSH no conecta o se cae, la app pasa a Desconectado con el motivo; se vuelve a conectar con el mismo botón.
 */
class ZumoVpnService : VpnService() {

    companion object {
        const val ACTION_START = "com.zumo.vpn.START"
        const val ACTION_STOP = "com.zumo.vpn.STOP"
        const val SOCKS_PORT = 10808
        private const val CHANNEL = "zumo_vpn_mudo"
        private const val CANAL_VIEJO = "zumo_vpn"   // sonaba y vibraba; Android no deja cambiar un canal ya creado
        private const val NOTI_ID = 1

        @Volatile var estado: String = "Desconectado"
        @Volatile var conectado: Boolean = false
        @Volatile var conectando: Boolean = false     // intentando conectar (antes de lograrlo)
        @Volatile var ultimoError: String = ""
        @Volatile var corriendo: Boolean = false
        @Volatile var desde: Long = 0L
        @Volatile var etapaActual: String = ""
        @Volatile var velocidad: String = ""
        @Volatile var datosUsados: String = ""

        // Compartir por WiFi (hotspot): proxy HTTP que sale por el túnel. Lo prende y apaga el botón "WiFi".
        const val WIFI_PUERTO = 8118
        @Volatile var wifiActivo: Boolean = false
        @Volatile var wifiPuerto: Int = WIFI_PUERTO
        @Volatile var wifiError: String = ""
        @Volatile private var instancia: ZumoVpnService? = null

        /** Prende o apaga el proxy del hotspot. Devuelve false si no se pudo (VPN apagada o puerto ocupado). */
        fun compartirWifi(prender: Boolean): Boolean {
            val svc = instancia
            if (!prender) { svc?.apagarWifi(); wifiActivo = false; return true }
            if (svc == null || !conectado) { wifiError = "Primero conectá la VPN."; return false }
            return svc.encenderWifi()
        }

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
            // Estado inmediato: la pantalla muestra "Conectando…" apenas se toca, sin esperar al servicio.
            conectando = true; conectado = false; corriendo = true; estado = "Conectando..."; ultimoError = ""
            val i = Intent(ctx, ZumoVpnService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }

        fun detener(ctx: Context) {
            // Corte inmediato en pantalla; el servicio termina de apagar en segundo plano.
            conectando = false; conectado = false; corriendo = false; estado = "Desconectado"
            ctx.startService(Intent(ctx, ZumoVpnService::class.java).setAction(ACTION_STOP))
        }
    }

    private var tun: ParcelFileDescriptor? = null
    private var tproxy: TProxyService? = null
    private var socks: SocksServer? = null
    private var proxyWifi: HttpProxyServer? = null
    @Volatile private var tunel: SshTunnel? = null
    private val enCarrera = java.util.concurrent.CopyOnWriteArrayList<SshTunnel>()   // intentos en paralelo (búsqueda del token)
    @Volatile private var intentoActual: SshTunnel? = null   // conexión en curso, para poder cortarla al instante
    @Volatile private var activo = false
    @Volatile private var reintentarYa = false               // la red volvió: reintentar sin esperar
    @Volatile private var cfgActual: Config? = null
    private var hilo: Thread? = null
    private var monitor: Thread? = null
    private var wake: PowerManager.WakeLock? = null
    private var wifi: WifiManager.WifiLock? = null
    private var cb: ConnectivityManager.NetworkCallback? = null
    // Un solo hilo de trabajo serializa encender y apagar: así el botón nunca bloquea la pantalla
    // (el apagado cierra sockets y el proxy nativo, que puede tardar) y un reconectar no pisa al apagado.
    private val trabajos = Executors.newSingleThreadExecutor()

    override fun onCreate() {
        super.onCreate()
        CrashLog.instalar(this)
        instancia = this
    }

    @Synchronized
    private fun encenderWifi(): Boolean {
        if (proxyWifi != null) return true
        for (p in WIFI_PUERTO until WIFI_PUERTO + 10) {      // si el puerto está ocupado, prueba el siguiente
            val srv = HttpProxyServer(p) { host, port -> abrirPorTunel(host, port) }
            try {
                srv.start()
                proxyWifi = srv; wifiPuerto = p; wifiActivo = true; wifiError = ""
                Registro.add("WiFi compartido: proxy en el puerto $p")
                return true
            } catch (e: Exception) {
                try { srv.stop() } catch (_: Exception) {}
            }
        }
        wifiError = "No se pudo abrir el puerto $WIFI_PUERTO (está ocupado)."
        return false
    }

    @Synchronized
    private fun apagarWifi() {
        try { proxyWifi?.stop() } catch (_: Exception) {}
        if (proxyWifi != null) Registro.add("WiFi compartido apagado")
        proxyWifi = null
        wifiActivo = false
    }

    /** Un canal del túnel SSH hacia host:port, para el proxy del hotspot. null si el túnel no está listo. */
    private fun abrirPorTunel(host: String, port: Int): HttpProxyServer.Salida? {
        val canal = tunel?.abrirCanal(host, port) ?: return null
        val ri = canal.inputStream
        val ro = canal.outputStream
        try {
            canal.connect(12000)
        } catch (e: Exception) {
            try { canal.disconnect() } catch (_: Exception) {}
            return null
        }
        return object : HttpProxyServer.Salida {
            override val entrada = ri
            override val salida = ro
            override fun cerrar() { try { canal.disconnect() } catch (_: Exception) {} }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Prefs(this).wanted = false
            marcarApagado()                       // estado y notificación al instante (hilo principal)
            trabajos.execute { apagarTrabajo() }  // el cierre pesado, aparte
            stopSelf()
            return START_NOT_STICKY
        }
        // START o reinicio del sistema (intent nulo): si el usuario la quiere encendida, se enciende
        val prefs = Prefs(this)
        if (intent == null && !prefs.wanted) { stopSelf(); return START_NOT_STICKY }
        prefs.wanted = true
        // Foreground ya mismo (el sistema lo exige) y el encendido pesado en el hilo de trabajo.
        crearCanal()
        try { startForeground(NOTI_ID, notificacion("Conectando...")) } catch (_: Exception) {}
        if (!activo) {
            activo = true; corriendo = true; conectando = true
            trabajos.execute { try { encenderTrabajo() } catch (e: Throwable) { errorInterno(e) } }
        }
        return START_NOT_STICKY   // si el sistema la mata, no se vuelve a encender sola
    }

    override fun onRevoke() {
        Prefs(this).wanted = false
        marcarApagado()
        trabajos.execute { apagarTrabajo() }
        stopSelf()
    }

    override fun onDestroy() {
        marcarApagado()
        apagarTrabajo()     // el servicio se está destruyendo: cierre directo, lo mejor posible
        if (instancia === this) instancia = null
        super.onDestroy()
    }

    private fun encenderTrabajo() {
        if (!activo) return                 // lo cancelaron antes de arrancar
        val prefs = Prefs(this)
        Servidores.refrescar(this, prefs)   // servidor de la lista de la app: toma su payload actual
        // Con lista de servidores, la app busca sola en cuál está el token (ver Busqueda); si no, la cuenta guardada.
        val candidatos = Servidores.candidatos(Servidores.lista(this), prefs.ultimoServidor, prefs.config, if (Servidores.MOSTRAR_SELECTOR) prefs.servidor else "")
        val cfg = candidatos.firstOrNull()
        // En modo token, el token de este celular hace de usuario y de contraseña.
        val user = if (prefs.modoToken) TokenCel.token(this, prefs) else prefs.user
        val pass = if (prefs.modoToken) user else prefs.pass.ifBlank { prefs.user }
        if (cfg == null || user.isBlank()) {
            fallarEncendido("No hay servidores cargados en la app. Pedí la versión nueva a tu proveedor."); return
        }
        if (Perfil.vencida(prefs.exp)) {
            fallarEncendido("Tu cuenta venció el ${Perfil.fechaLinda(prefs.exp)}. Pedí la renovación."); return
        }
        Registro.add("Iniciando…")
        desde = System.currentTimeMillis()
        tomarBloqueos()
        try {
            abrirTun()
            socks = SocksServer(SOCKS_PORT) { tunel }.also { it.start() }
        } catch (e: Exception) {
            ultimoError = "No se pudo iniciar la VPN: ${e.message}"
            Registro.add("✘ $ultimoError")
            estado = "Error"
            fallarEncendido(null); return
        }
        cfgActual = cfg
        vigilarRed()
        hilo = Thread({ try { bucle(candidatos, user, pass) } catch (e: Throwable) { errorInterno(e) } }, "zumo-ssh").also { it.start() }
        monitor = Thread({ vigilar() }, "zumo-monitor").also { it.start() }
    }

    /** Un error inesperado: se guarda el motivo (se muestra al abrir la app) y se apaga la VPN sin cerrar la app. */
    private fun errorInterno(e: Throwable) {
        CrashLog.guardar(this, e)
        Registro.add("✘ Error interno: ${e.javaClass.simpleName}")
        detenerPorError("Error interno (${e.javaClass.simpleName}). Abrí la app de nuevo y mandá la captura del aviso.")
    }

    private fun fallarEncendido(motivo: String?) {
        if (motivo != null) { ultimoError = motivo; Registro.add("✘ $motivo"); estado = "Error" }
        Prefs(this).wanted = false
        conectando = false; corriendo = false; activo = false
        apagarTrabajo()
        stopSelf()
    }

    /**
     * Corre aparte de la conexión SSH: solo actualiza la velocidad y los datos en la notificación.
     * No reconecta nada: si la sesión se cae, el bucle de conexión desconecta la app.
     */
    private fun vigilar() {
        var txAnt = 0L; var rxAnt = 0L; var t0 = System.currentTimeMillis()
        try {
            while (activo) {
                Thread.sleep(2000)
                if (!activo) break
                val st = try { tproxy?.TProxyGetStats() } catch (_: Throwable) { null }
                if (st != null && st.size >= 4 && conectado) {
                    val t1 = System.currentTimeMillis()
                    val dt = ((t1 - t0).coerceAtLeast(1)) / 1000.0
                    val tx = st[1]; val rx = st[3]
                    val subKBs = (tx - txAnt).coerceAtLeast(0) / dt / 1_000
                    val bajKBs = (rx - rxAnt).coerceAtLeast(0) / dt / 1_000
                    velocidad = "↓ %.0f  ↑ %.0f KB/s".format(bajKBs, subKBs)
                    datosUsados = formatoDatos(tx + rx)
                    txAnt = tx; rxAnt = rx; t0 = t1
                    actualizarNoti()
                }
            }
        } catch (e: InterruptedException) {
            // El hilo se interrumpe a propósito al desconectar (apagarTrabajo() interrumpe el monitor).
            // Si no se captura acá, la excepción sube sin control y tumba toda la app.
            return
        }
    }

    private fun abrirTun() {
        val b = Builder()
            .setSession(Tema.actual(this).nombre)
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
    private val ERROR_SERVIDOR = "Error de servidor. Ningún servidor respondió; probá de nuevo en un rato."

    /** Si el servidor tiene un DNS elegido, se deja anotado en el registro. */
    private fun avisarDns(c: Config) {
        if (c.dnsServidores().isNotEmpty()) Registro.add("DNS: ${Dns.etiqueta(c.dns)}")
    }

    private fun detenerPorError(motivo: String) {
        ultimoError = motivo
        Registro.add("✘ $motivo")
        Prefs(this).wanted = false
        marcarApagado()
        estado = "Error"
        trabajos.execute { apagarTrabajo(); stopSelf() }
    }

    /**
     * Mantiene la conexión: intenta, y si se cae por la red (o se cambió de Wi-Fi a datos) reconecta
     * solo, con esperas crecientes, mientras el usuario la quiera encendida. Solo se rinde con un
     * error que no se arregla reintentando (usuario/clave mal o cuenta vencida).
     */
    private fun bucle(candidatos: List<Config>, user: String, pass: String) {
        var espera = 2000L
        val ordenados = try {
            Hosts.ordenarTodos(candidatos, Prefs(this).ultimoServidor) { c -> Hosts.sondeoTcp(c) { sock -> protect(sock) } }
        } catch (e: Exception) { candidatos }       // si algo falla al ordenar, se usa el orden de la lista
        val busq = Busqueda(ordenados)
        // Con 2 o más servidores se prueban todos a la vez (el primero que deja entrar gana); con uno, el camino de siempre.
        val varios = candidatos.map { it.name }.distinct().size >= 2
        var siguienteYa = false
        var huboConexion = false      // ya se conectó alguna vez en esta sesión: si después se cae, sí se reintenta
        var algunoRechazo = false     // algún servidor respondió pero no conoce el token
        while (activo && Prefs(this).wanted) {
            if (Perfil.vencida(Prefs(this).exp)) {
                detenerPorError("Tu cuenta venció el ${Perfil.fechaLinda(Prefs(this).exp)}. Pedí la renovación."); return
            }
            if (!hayInternet()) {
                estado = "Reconectando…"; conectado = false; conectando = true; actualizarNoti()
                if (!esperarReintento(espera)) break
                espera = (espera * 2).coerceAtMost(20000L); continue
            }
            if (varios) {
                val res = carrera(candidatos, user, pass)
                if (!activo || !Prefs(this).wanted) { res.tunel?.close(); break }
                if (res.cancelado) continue                  // la red volvió: se prueba de nuevo ya
                val g = res.tunel
                val cg = res.cfg
                if (g == null || cg == null) {
                    when (busq.resultadoRonda(res.rechazados, res.caidos)) {
                        Busqueda.Paso.RECHAZADO -> { detenerPorError("Token expirado. Pedí que te lo activen y volvé a intentar."); return }
                        Busqueda.Paso.NINGUNO -> { detenerPorError("Token expirado. Pedí que te lo activen y volvé a intentar."); return }
                        else -> {}
                    }
                    // Nadie respondió y nunca se llegó a conectar: no se reintenta, queda desconectado con el error.
                    if (!huboConexion && res.rechazados.isEmpty()) { detenerPorError(ERROR_SERVIDOR); return }
                    ultimoError = "Ningún servidor respondió"; Registro.add("✘ $ultimoError; reintentando…")
                    estado = "Reconectando…"; conectado = false; conectando = true; actualizarNoti()
                    if (!esperarReintento(espera)) break
                    espera = (espera * 2).coerceAtMost(20000L); continue
                }
                var cayoG = false
                try {
                    cfgActual = cg; tunel = g
                    busq.exitoNombre(cg.name)
                    huboConexion = true
                    Prefs(this).ultimoServidor = cg.name
                    estado = "Conectado"; conectado = true; conectando = false; ultimoError = ""; espera = 2000L; actualizarNoti()
                    Registro.add("✔ Conectado (${cg.name})")
                    avisarDns(cg)
                    while (activo && g.conectado) Thread.sleep(1000)
                    cayoG = activo && Prefs(this).wanted
                } catch (e: InterruptedException) {
                } finally {
                    conectado = false
                    try { g.close() } catch (_: Exception) {}
                    if (tunel === g) tunel = null
                }
                if (!activo || !Prefs(this).wanted) break
                if (cayoG) Registro.add("Se perdió la conexión; reconectando…")
                estado = "Reconectando…"; conectando = true; actualizarNoti()
                if (!esperarReintento(espera)) break
                espera = (espera * 2).coerceAtMost(20000L)
                continue
            }
            val cfg = busq.actual()
            cfgActual = cfg
            val t = SshTunnel(cfg, user, pass, etapa = { etapaActual = it; Registro.add(it) }, proteger = { sock -> protect(sock) })
            intentoActual = t
            var cayo = false
            try {
                estado = "Conectando..."; conectado = false; conectando = true; actualizarNoti()
                t.connect()
                tunel = t
                busq.exito()
                huboConexion = true
                Prefs(this).ultimoServidor = cfg.name          // el primero que se prueba la próxima vez
                estado = "Conectado"; conectado = true; conectando = false; ultimoError = ""; espera = 2000L; actualizarNoti()
                Registro.add("✔ Conectado")
                avisarDns(cfg)
                while (activo && t.conectado) Thread.sleep(1000)
                cayo = activo && Prefs(this).wanted      // se cortó sola, no la cortó el usuario
            } catch (e: InterruptedException) {
                // el usuario tocó Desconectar, o la red volvió y queremos reintentar ya
            } catch (e: Exception) {
                val rechazado = esFalloDeLogin(e)
                if (rechazado) algunoRechazo = true
                if (!rechazado) { ultimoError = mensaje(e); Registro.add("✘ $ultimoError") }
                when (busq.fallo(rechazado)) {
                    Busqueda.Paso.RECHAZADO -> { detenerPorError("Token expirado. Pedí que te lo activen y volvé a intentar."); return }
                    Busqueda.Paso.NINGUNO -> { detenerPorError("Token expirado. Pedí que te lo activen y volvé a intentar."); return }
                    Busqueda.Paso.SIGUIENTE -> { siguienteYa = true; Registro.add("Probando otro servidor…") }
                    // Se probaron todos, ninguno respondió y nunca se llegó a conectar: no se reintenta.
                    Busqueda.Paso.REINTENTAR -> if (!huboConexion && !algunoRechazo) { detenerPorError(ERROR_SERVIDOR); return }
                }
                cayo = activo && Prefs(this).wanted && !siguienteYa
            } finally {
                conectado = false
                if (intentoActual === t) intentoActual = null
                try { t.close() } catch (_: Exception) {}
                if (tunel === t) tunel = null
            }
            if (!activo || !Prefs(this).wanted) break
            if (siguienteYa) { siguienteYa = false; continue }      // el próximo servidor, sin esperar
            if (cayo) { Registro.add("Se perdió la conexión; reconectando…") }
            estado = "Reconectando…"; conectando = true; actualizarNoti()
            if (!esperarReintento(espera)) break
            espera = (espera * 2).coerceAtMost(20000L)
        }
    }

    private class ResultadoCarrera(
        val tunel: SshTunnel?, val cfg: Config?, val rechazados: Set<String>, val caidos: Set<String>, val cancelado: Boolean = false,
    )

    /**
     * Prueba TODOS los servidores a la vez (uno por hilo; dentro de cada uno, sus hosts por orden de respuesta).
     * El primero que deja entrar gana y se cortan los demás. Así la búsqueda tarda lo que tarde el servidor
     * correcto, no la suma de los que fallan o están caídos.
     */
    private fun carrera(candidatos: List<Config>, user: String, pass: String): ResultadoCarrera {
        val porServidor = candidatos.groupBy { it.name }
        val ganador = java.util.concurrent.atomic.AtomicReference<Pair<SshTunnel, Config>?>(null)
        val rechazados = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
        val caidos = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
        val pendientes = java.util.concurrent.CountDownLatch(porServidor.size)
        val fin = java.util.concurrent.atomic.AtomicBoolean(false)
        estado = "Conectando..."; conectado = false; conectando = true; actualizarNoti()
        Registro.add("Probando ${porServidor.size} servidores a la vez…")
        for ((nombre, cands) in porServidor) {
            Thread({
                try { probarServidor(nombre, cands, user, pass, ganador, fin, rechazados, caidos) }
                catch (_: Throwable) { caidos.add(nombre) }
                finally { pendientes.countDown() }
            }, "zumo-carrera").also { it.isDaemon = true }.start()
        }
        var cancelado = false
        try {
            while (ganador.get() == null && !pendientes.await(100, java.util.concurrent.TimeUnit.MILLISECONDS)) {
                if (!activo || !Prefs(this).wanted) { cancelado = true; break }
            }
        } catch (_: InterruptedException) { cancelado = true }
        val g = if (cancelado) null else ganador.get()
        for (t in enCarrera.toList()) if (t !== g?.first) { try { t.close() } catch (_: Exception) {} }
        fin.set(true)       // los que terminen tarde se cierran solos
        if (cancelado) ganador.get()?.first?.let { try { it.close() } catch (_: Exception) {} }
        return if (g != null) ResultadoCarrera(g.first, g.second, emptySet(), emptySet())
        else ResultadoCarrera(null, null, rechazados.toSet(), caidos.toSet(), cancelado)
    }

    private fun probarServidor(
        nombre: String, cands: List<Config>, user: String, pass: String,
        ganador: java.util.concurrent.atomic.AtomicReference<Pair<SshTunnel, Config>?>,
        fin: java.util.concurrent.atomic.AtomicBoolean,
        rechazados: MutableSet<String>, caidos: MutableSet<String>,
    ) {
        val orden = try { Hosts.ordenarTodos(cands, "", 3500) { c -> Hosts.sondeoTcp(c) { sock -> protect(sock) } } } catch (e: Exception) { cands }
        for (cfg in orden) {
            if (ganador.get() != null || fin.get() || !activo) return
            val t = SshTunnel(cfg, user, pass, etapa = { Registro.add("[$nombre] $it") }, proteger = { sock -> protect(sock) })
            enCarrera.add(t)
            try {
                t.connect()
                if (!fin.get() && ganador.compareAndSet(null, Pair(t, cfg))) { enCarrera.remove(t); return }
                try { t.close() } catch (_: Exception) {}       // otro servidor entró primero
                enCarrera.remove(t)
                return
            } catch (e: Exception) {
                try { t.close() } catch (_: Exception) {}
                enCarrera.remove(t)
                if (esFalloDeLogin(e)) { rechazados.add(nombre); return }      // responde pero no conoce el token: no se insiste con sus otros hosts
                Registro.add("✘ [$nombre] ${mensaje(e)}")
            }
        }
        if (ganador.get() == null) caidos.add(nombre)
    }

    /** Espera [ms] antes de reintentar, pero vuelve antes si la red reapareció. false = hay que parar. */
    private fun esperarReintento(ms: Long): Boolean {
        reintentarYa = false
        val fin = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < fin) {
            if (!activo || !Prefs(this).wanted) return false
            if (reintentarYa) { reintentarYa = false; return true }
            try { Thread.sleep(200) } catch (_: InterruptedException) { if (!activo || !Prefs(this).wanted) return false }
        }
        return activo && Prefs(this).wanted
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
            esFalloDeLogin(e) -> "Token expirado"
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
                // Apareció una red (volvió el Wi-Fi/datos o se cambió de una a otra). Si estamos esperando
                // para reintentar, que reintente ya; si estamos conectados, no se toca nada.
                if (activo && !conectado) { reintentarYa = true; hilo?.interrupt() }
            }
            override fun onLost(network: Network) {
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

    /** Estado y notificación al instante (no bloquea): lo pesado lo hace apagarTrabajo() aparte. */
    private fun marcarApagado() {
        activo = false; corriendo = false; conectado = false; conectando = false; estado = "Desconectado"
        velocidad = ""; datosUsados = ""
        try { wake?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { wifi?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { stopForeground(STOP_FOREGROUND_REMOVE) } catch (_: Exception) {}
    }

    /** Cierre pesado (sockets, túnel, proxy nativo). Corre en el hilo de trabajo, nunca en el principal. */
    @Synchronized
    private fun apagarTrabajo() {
        val callback = cb; cb = null
        try { callback?.let { (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager).unregisterNetworkCallback(it) } } catch (_: Exception) {}
        hilo?.interrupt(); hilo = null
        monitor?.interrupt(); monitor = null
        try { enCarrera.forEach { try { it.close() } catch (_: Exception) {} } } catch (_: Exception) {}
        try { intentoActual?.close() } catch (_: Exception) {}   // corta un connect() en curso
        intentoActual = null
        try { tunel?.close() } catch (_: Exception) {}
        tunel = null
        apagarWifi()
        try { socks?.stop() } catch (_: Exception) {}
        socks = null
        try { tproxy?.TProxyStopService() } catch (_: Throwable) {}
        tproxy = null
        try { tun?.close() } catch (_: Exception) {}
        tun = null
    }

    private fun crearCanal() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            // IMPORTANCE_DEFAULT (no LOW): en HiOS (Tecno/Infinix/itel) una notificación "silenciosa"
            // hace que el gestor de batería trate a la app como inactiva y la mate antes.
            // La notificación se actualiza cada 2 s (velocidad): sin sonido ni vibración, si no el teléfono vibra todo el tiempo.
            try { nm.deleteNotificationChannel(CANAL_VIEJO) } catch (_: Exception) {}
            val canal = NotificationChannel(CHANNEL, Tema.actual(this).nombre, NotificationManager.IMPORTANCE_DEFAULT)
            canal.setSound(null, null)
            canal.enableVibration(false)
            canal.enableLights(false)
            canal.setShowBadge(false)
            nm.createNotificationChannel(canal)
        }
    }

    private fun notificacion(txt: String): Notification {
        val abrir = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val parar = PendingIntent.getService(
            this, 1, Intent(this, ZumoVpnService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE
        )
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
        val cuerpo = if (conectado && velocidad.isNotBlank()) {
            "$txt  ·  ${duracionDesde()}  ·  $velocidad  ·  Total ${datosUsados}"
        } else txt
        b.setContentTitle(Tema.actual(this).nombre).setContentText(cuerpo)
            .setSmallIcon(R.drawable.ic_cohete)
            .setContentIntent(abrir).setOngoing(true).setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .addAction(Notification.Action.Builder(null, "Desconectar", parar).build())
        if (Build.VERSION.SDK_INT < 26) @Suppress("DEPRECATION") b.setPriority(Notification.PRIORITY_HIGH)
        return b.build()
    }

    /** Tiempo transcurrido desde que se conectó, en mm:ss (o h:mm:ss si pasa de una hora). */
    private fun duracionDesde(): String {
        if (desde <= 0L) return "--:--"
        val s = (System.currentTimeMillis() - desde) / 1000
        val hh = s / 3600; val mm = (s % 3600) / 60; val ss = s % 60
        return if (hh > 0) "%d:%02d:%02d".format(hh, mm, ss) else "%02d:%02d".format(mm, ss)
    }

    private fun actualizarNoti() {
        try { (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).notify(NOTI_ID, notificacion(estado)) } catch (_: Exception) {}
    }
}
