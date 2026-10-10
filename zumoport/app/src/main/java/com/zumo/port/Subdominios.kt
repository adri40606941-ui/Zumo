package com.zumo.port

import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentSkipListSet
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Un subdominio encontrado, con las IP a las que apunta (vacío = existe en un certificado pero hoy no responde) y de dónde salió. */
class Subdominio(val nombre: String, val ips: List<String>, val fuente: String) {
    val resuelve: Boolean get() = ips.isNotEmpty()

    /** ASN de sus IP (casi siempre uno). Se llena después de la búsqueda; [asnListo] dice si ya se terminó de preguntar. */
    @Volatile var asns: List<InfoAsn> = emptyList()
    @Volatile var asnListo: Boolean = false

    /** Puertos abiertos (de los que se probaron) en sus IP. Se llena después; [puertosListo] dice si ya se terminó de probar. */
    @Volatile var puertos: List<Int> = emptyList()
    @Volatile var puertosListo: Boolean = false

    /** Los puertos abiertos de cada una de sus IP (la unión de todos es [puertos]). */
    @Volatile var puertosPorIp: Map<String, List<Int>> = emptyMap()

    /** Lo que contestó cada puerto abierto cuando se probó por HTTP/HTTPS con el botón «Probar». Vacío si no se probó. */
    val pruebas = CopyOnWriteArrayList<PruebaHttp>()
    @Volatile var probando: Boolean = false
}

/**
 * De dónde salen los nombres. Las de internet ([web]) son fuentes públicas que guardan nombres que alguna vez existieron
 * (certificados, DNS pasivo, páginas archivadas…); la lista de nombres comunes se prueba preguntando al DNS.
 */
enum class Fuente(val chip: String, val nombre: String, val esperaMs: Int, val web: Boolean = true) {
    CRTSH("🔐 crt.sh (certificados)", "crt.sh", 40000),
    HACKERTARGET("🛰 HackerTarget", "HackerTarget", 20000),
    ALIENVAULT("👽 AlienVault OTX", "AlienVault", 20000),
    CERTSPOTTER("📜 CertSpotter", "CertSpotter", 25000),
    URLSCAN("🔎 URLScan", "URLScan", 20000),
    ANUBIS("🐜 Anubis", "Anubis", 20000),
    SUBDOMAINCENTER("🗺 Subdomain Center", "SubdomainCenter", 20000),
    WAYBACK("🕰 Wayback Machine", "Wayback", 40000),
    LISTA("📖 Lista de nombres comunes", "lista", 0, web = false),
    DOMINIO("dominio", "dominio", 0, web = false);

    /** La dirección que hay que pedir para [dominio] (vacía para las que no son de internet). */
    fun url(dominio: String): String {
        val d = URLEncoder.encode(dominio, "UTF-8")
        return when (this) {
            CRTSH -> "https://crt.sh/?q=" + URLEncoder.encode("%.$dominio", "UTF-8") + "&output=json"
            HACKERTARGET -> "https://api.hackertarget.com/hostsearch/?q=$d"
            ALIENVAULT -> "https://otx.alienvault.com/api/v1/indicators/domain/$d/passive_dns"
            CERTSPOTTER -> "https://api.certspotter.com/v1/issuances?domain=$d&include_subdomains=true&expand=dns_names"
            URLSCAN -> "https://urlscan.io/api/v1/search/?q=" + URLEncoder.encode("domain:$dominio", "UTF-8") + "&size=1000"
            ANUBIS -> "https://jldc.me/anubis/subdomains/$d"
            SUBDOMAINCENTER -> "https://api.subdomain.center/?domain=$d"
            WAYBACK -> "https://web.archive.org/cdx/search/cdx?url=*.$d&fl=original&collapse=urlkey&limit=10000"
            else -> ""
        }
    }

    /** Los nombres de [dominio] que trae lo que contestó esta fuente. */
    fun parsear(texto: String, dominio: String): Set<String> = when (this) {
        CRTSH -> BuscadorSubdominios.parsearCrtsh(texto, dominio)
        HACKERTARGET -> BuscadorSubdominios.parsearHackertarget(texto, dominio)
        else -> BuscadorSubdominios.extraerNombres(texto, dominio)
    }

    companion object {
        /** Las que se pueden marcar o desmarcar en pantalla, en este orden. */
        val ELEGIBLES: List<Fuente> = values().filter { it != DOMINIO }
    }
}

/** Qué pasó con una fuente de internet: [ok] si contestó, y cuántos nombres del dominio trajo. */
class ResultadoFuente(val fuente: Fuente, val ok: Boolean, val cantidad: Int)

/** Cuáles fuentes usar para buscar. */
class Metodos(val fuentes: Set<Fuente>) {
    /** Forma corta de las tres de siempre (crt.sh, HackerTarget y la lista). */
    constructor(certificados: Boolean = true, hackertarget: Boolean = true, lista: Boolean = true) : this(
        buildSet {
            if (certificados) add(Fuente.CRTSH)
            if (hackertarget) add(Fuente.HACKERTARGET)
            if (lista) add(Fuente.LISTA)
        }
    )
}

/**
 * Lo que se averigua de cada subdominio que responde, después de encontrarlo: el [asn] al que pertenecen sus IP y cuáles de
 * estos [puertos] tienen abiertos (vacío = no probar puertos).
 */
class Extras(val asn: Boolean = false, val puertos: List<Int> = emptyList())

/**
 * Busca subdominios de un dominio por varios caminos que se suman (ver [Fuente]): fuentes públicas de internet, que se
 * consultan todas a la vez (certificados, DNS pasivo, páginas archivadas…), y una lista de nombres comunes (www, mail, api,
 * cdn...) que se prueba preguntando al DNS. Después se resuelve cada nombre a IP y se descartan los que solo existen por un
 * DNS "comodín" (*.dominio).
 */
class BuscadorSubdominios(
    private val resolver: (String) -> List<String> = { n -> resolverTodas(n) },
    private val bajar: (String, Int) -> String? = { u, ms -> descargar(u, ms) },
    private val asn: BuscadorAsn = BuscadorAsn(),
    private val abierto: (String, Int) -> Boolean = { ip, p -> puertoAbierto(ip, p, ESPERA_PUERTO_MS) },
) {
    private val cancelado = AtomicBoolean(false)
    fun cancelar() { cancelado.set(true) }

    fun buscar(
        dominio: String,
        metodos: Metodos,
        alEstado: (String) -> Unit,
        alHallar: (Subdominio) -> Unit,
        alAvanzar: (Int, Int) -> Unit,
        extras: Extras = Extras(),
        alFuentes: (List<ResultadoFuente>) -> Unit = {},
        alActualizar: () -> Unit = {},
    ) {
        cancelado.set(false)
        val halladas = CopyOnWriteArrayList<Subdominio>()
        val candidatos = HashMap<String, java.util.EnumSet<Fuente>>()   // nombre -> fuentes donde apareció
        fun anotar(n: String, f: Fuente) { candidatos.getOrPut(n) { java.util.EnumSet.noneOf(Fuente::class.java) }.add(f) }
        val web = Fuente.ELEGIBLES.filter { it in metodos.fuentes && it.web }
        if (web.isNotEmpty() && !cancelado.get()) {
            val resultados = consultarFuentes(web, dominio, alEstado) ?: return
            for ((f, nombres) in resultados) nombres?.forEach { anotar(it, f) }
            val resumen = resultados.map { (f, nombres) -> ResultadoFuente(f, nombres != null, nombres?.size ?: 0) }
            for (r in resumen) if (!r.ok) alEstado(r.fuente.nombre + " no respondió" + if (r.fuente == Fuente.CRTSH) " (a veces está saturado)" else "")
            alFuentes(resumen)
        }
        if (Fuente.LISTA in metodos.fuentes && !cancelado.get()) {
            for (p in PALABRAS) anotar("$p.$dominio", Fuente.LISTA)
        }
        anotar(dominio, Fuente.DOMINIO)
        if (cancelado.get()) return

        // DNS comodín: si un nombre inventado también resuelve, esas IP no cuentan como hallazgo
        val comodin = cancelable { resolver("zp-" + System.nanoTime().toString(36) + "." + dominio).toSet() } ?: emptySet()
        if (cancelado.get()) return
        if (comodin.isNotEmpty()) alEstado("Este dominio tiene DNS comodín: se ignoran los nombres que solo apuntan a ${comodin.joinToString(", ")}")

        val nombres = candidatos.keys.sorted()
        val hechos = AtomicInteger(0)
        val pool = hilos(48)
        alEstado("Resolviendo ${nombres.size} nombres…")
        try {
            for (n in nombres) {
                if (cancelado.get()) break
                pool.execute {
                    try {
                        if (!cancelado.get()) {
                            val ips = resolver(n).filter { it !in comodin }
                            val fs = candidatos[n]!!
                            // los de la lista que no existen no se muestran; los que trae alguna fuente de internet sí (se marcan sin IP)
                            if (!cancelado.get() && (ips.isNotEmpty() || fs.any { it.web })) {
                                val sub = Subdominio(n, ips, fs.joinToString(" + ") { it.nombre })
                                halladas.add(sub)
                                alHallar(sub)
                            }
                        }
                    } catch (_: Exception) {
                    } finally {
                        alAvanzar(hechos.incrementAndGet(), nombres.size)
                    }
                }
            }
        } finally {
            pool.shutdown()
            esperar(pool, 30)
        }
        if (!cancelado.get() && (extras.asn || extras.puertos.isNotEmpty())) enriquecer(halladas.filter { it.resuelve }, extras, alEstado, alAvanzar, alActualizar)
    }

    /**
     * Pide todas las fuentes de internet a la vez (no una tras otra) y espera a la última, hasta el tiempo de la más lenta.
     * Devuelve, por fuente, los nombres que trajo (null = no contestó). null si se canceló.
     */
    private fun consultarFuentes(fuentes: List<Fuente>, dominio: String, alEstado: (String) -> Unit): Map<Fuente, Set<String>?>? {
        val resultados = ConcurrentHashMap<Fuente, Set<String>>()
        val pendientes = CountDownLatch(fuentes.size)
        alEstado("Consultando ${fuentes.size} fuentes a la vez…")
        for (f in fuentes) {
            Thread({
                try {
                    val t = bajar(f.url(dominio), f.esperaMs)
                    if (t != null) resultados[f] = f.parsear(t, dominio)
                } catch (_: Exception) {
                } finally {
                    pendientes.countDown()
                }
            }, "zumoport-fuente").also { it.isDaemon = true }.start()
        }
        val limite = System.currentTimeMillis() + fuentes.maxOf { it.esperaMs } + 5000L
        var avisadas = 0
        while (pendientes.count > 0 && System.currentTimeMillis() < limite) {
            if (cancelado.get()) return null
            try { pendientes.await(100, TimeUnit.MILLISECONDS) } catch (_: InterruptedException) { return null }
            val hechas = fuentes.size - pendientes.count.toInt()
            if (hechas != avisadas && pendientes.count > 0) { avisadas = hechas; alEstado("Consultando fuentes: contestaron $hechas de ${fuentes.size}…") }
        }
        if (cancelado.get()) return null
        return fuentes.associateWith { resultados[it] }
    }

    /**
     * Fase 2, con la lista de subdominios ya a la vista: por cada IP distinta (muchos subdominios comparten IP, así que se
     * pregunta una vez) se averigua el ASN y se prueban los puertos, y se va completando cada fila a medida que llega.
     * Las IP de redes locales (192.168…, 10…) no se consultan: no tienen ASN y desde afuera no se alcanzan.
     */
    private fun enriquecer(subs: List<Subdominio>, extras: Extras, alEstado: (String) -> Unit, alAvanzar: (Int, Int) -> Unit, alActualizar: () -> Unit) {
        if (subs.isEmpty()) return
        val publicas = subs.flatMap { it.ips }.distinct().filter { !Objetivos.esLocal(it) }
        if (extras.asn && !cancelado.get()) buscarAsn(subs, publicas.take(MAX_IPS_ASN), alEstado, alAvanzar, alActualizar)
        if (extras.puertos.isNotEmpty() && !cancelado.get()) buscarPuertos(subs, publicas.take(MAX_IPS_PUERTOS), extras.puertos, alEstado, alAvanzar, alActualizar)
    }

    private fun buscarAsn(subs: List<Subdominio>, ips: List<String>, alEstado: (String) -> Unit, alAvanzar: (Int, Int) -> Unit, alActualizar: () -> Unit) {
        val resueltas = ConcurrentHashMap<String, List<InfoAsn>>()
        val hechas = AtomicInteger(0)
        // IP que no se van a consultar (locales o pasadas del tope): cuentan como resueltas y sin ASN
        val sinConsulta = subs.flatMap { it.ips }.distinct().filter { it !in ips }
        for (ip in sinConsulta) resueltas[ip] = emptyList()
        for (s in subs) completarAsn(s, resueltas)
        alActualizar()
        alEstado("Buscando el ASN de ${ips.size} IP…")
        alAvanzar(0, ips.size)
        val pool = hilos(HILOS_ASN)
        try {
            for (ip in ips) {
                pool.execute {
                    try {
                        if (!cancelado.get()) {
                            val r = asn.consultar(ip)
                            if (r != null) {
                                resueltas[ip] = r
                                for (s in subs) if (ip in s.ips) completarAsn(s, resueltas)
                                alActualizar()
                            }
                        }
                    } catch (_: Exception) {
                    } finally {
                        alAvanzar(hechas.incrementAndGet(), ips.size)
                    }
                }
            }
        } finally {
            pool.shutdown()
            esperar(pool, 10)
        }
        if (cancelado.get()) return
        // las IP que no contestaron quedan sin dato: la fila deja de decir "buscando…"
        if (asn.sinServicio) alEstado("No se pudo consultar el ASN (el DNS de Team Cymru no contestó desde esta red)")
        for (s in subs) if (!s.asnListo) s.asnListo = true
        alActualizar()
    }

    /** Una fila queda con su ASN cuando ya se conoce el de todas sus IP. */
    private fun completarAsn(s: Subdominio, resueltas: Map<String, List<InfoAsn>>) {
        if (s.ips.any { it !in resueltas }) return
        s.asns = s.ips.flatMap { resueltas[it].orEmpty() }.distinctBy { it.numero }
        s.asnListo = true
    }

    private fun buscarPuertos(subs: List<Subdominio>, ips: List<String>, puertos: List<Int>, alEstado: (String) -> Unit, alAvanzar: (Int, Int) -> Unit, alActualizar: () -> Unit) {
        val abiertos = ConcurrentHashMap<String, ConcurrentSkipListSet<Int>>()
        val faltan = ConcurrentHashMap<String, AtomicInteger>()
        val hechas = AtomicInteger(0)
        val total = ips.size * puertos.size
        for (ip in ips) { abiertos[ip] = ConcurrentSkipListSet(); faltan[ip] = AtomicInteger(puertos.size) }
        val sinSondeo = subs.flatMap { it.ips }.distinct().filter { !abiertos.containsKey(it) }
        fun completar(s: Subdominio) {
            if (s.ips.any { faltan[it]?.get()?.let { n -> n > 0 } == true }) return
            s.puertos = s.ips.flatMap { abiertos[it].orEmpty() }.distinct().sorted()
            s.puertosPorIp = s.ips.associateWith { abiertos[it]?.toList().orEmpty() }.filterValues { it.isNotEmpty() }
            s.puertosListo = true
        }
        for (s in subs) if (s.ips.all { it in sinSondeo }) completar(s)
        alActualizar()
        alEstado("Probando ${puertos.size} puertos en ${ips.size} IP…")
        alAvanzar(0, total)
        val pool = hilos(HILOS_PUERTOS)
        val cupo = java.util.concurrent.Semaphore(HILOS_PUERTOS * 4)
        try {
            for (ip in ips) {
                for (p in puertos) {
                    var tengo = false
                    while (!cancelado.get() && !tengo) tengo = cupo.tryAcquire(100, TimeUnit.MILLISECONDS)
                    if (!tengo) break
                    pool.execute {
                        try {
                            if (!cancelado.get() && abierto(ip, p)) abiertos[ip]!!.add(p)
                        } catch (_: Exception) {
                        } finally {
                            val quedan = faltan[ip]!!.decrementAndGet()
                            if (quedan == 0 && !cancelado.get()) {
                                for (s in subs) if (ip in s.ips) completar(s)
                                alActualizar()
                            }
                            alAvanzar(hechas.incrementAndGet(), total)
                            cupo.release()
                        }
                    }
                }
                if (cancelado.get()) break
            }
        } finally {
            pool.shutdown()
            esperar(pool, 30)
        }
    }

    private fun hilos(n: Int) = Hilos.crear(n)

    private fun esperar(pool: java.util.concurrent.ExecutorService, minutos: Long) = Hilos.esperar(pool, minutos, cancelado)

    /** Corre [tarea] en otro hilo y espera su resultado, pero vuelve con null apenas se cancela (sin esperar a que termine). */
    private fun <T> cancelable(tarea: () -> T): T? {
        val resultado = java.util.concurrent.atomic.AtomicReference<T?>(null)
        val fin = AtomicBoolean(false)
        val t = Thread({
            try { resultado.set(tarea()) } catch (_: Exception) {} finally { fin.set(true) }
        }, "zumoport-espera")
        t.isDaemon = true
        t.start()
        while (!fin.get()) {
            if (cancelado.get()) return null
            try { Thread.sleep(50) } catch (_: InterruptedException) { return null }
        }
        return resultado.get()
    }

    companion object {
        /** Tope de IP distintas que se consultan: más que eso sería esperar mucho por poco. */
        const val MAX_IPS_ASN = 400
        const val MAX_IPS_PUERTOS = 400
        const val HILOS_ASN = 8
        const val HILOS_PUERTOS = 128
        const val ESPERA_PUERTO_MS = 1200

        /** Puertos que se prueban por defecto en cada subdominio: web (incluidos los de Cloudflare) y unos pocos de servicio. */
        val PUERTOS_POR_DEFECTO = listOf(80, 443, 8080, 8443, 8880, 2052, 2053, 2082, 2083, 2086, 2087, 2095, 2096, 22, 21, 25)
        const val MAX_PUERTOS = 40

        /** true si el puerto de esa IP acepta una conexión TCP. */
        fun puertoAbierto(ip: String, puerto: Int, esperaMs: Int): Boolean {
            val s = Socket()
            return try {
                s.tcpNoDelay = true
                s.connect(InetSocketAddress(ip, puerto), esperaMs)
                true
            } catch (_: Exception) {
                false
            } finally {
                try { s.close() } catch (_: Exception) {}
            }
        }

        private val RE_DOMINIO = Regex("""^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$""")

        /** "https://www.Ejemplo.com/ruta" → "www.ejemplo.com"; null si no es un dominio. */
        fun limpiarDominio(s: String): String? {
            val t = Objetivos.limpiar(s).removePrefix("*.")
            return if (RE_DOMINIO.matches(t)) t else null
        }

        private val RE_NOMBRES = Regex(""""name_value"\s*:\s*"((?:[^"\\]|\\.)*)"""")

        /** crt.sh devuelve una lista JSON; cada elemento trae "name_value" con uno o más nombres separados por \n. */
        fun parsearCrtsh(json: String, dominio: String): Set<String> {
            val out = java.util.TreeSet<String>()
            for (m in RE_NOMBRES.findAll(json)) {
                val v = m.groupValues[1].replace("\\n", "\n").replace("\\/", "/")
                for (n in v.split('\n')) agregarSiEs(out, n, dominio)
            }
            return out
        }

        /** HackerTarget devuelve "nombre,ip" por renglón (o un mensaje de error si pasaste el límite). */
        fun parsearHackertarget(texto: String, dominio: String): Set<String> {
            val out = java.util.TreeSet<String>()
            if (texto.startsWith("error", true) || texto.contains("API count exceeded", true)) return out
            for (l in texto.lineSequence()) agregarSiEs(out, l.substringBefore(","), dominio)
            return out
        }

        private fun agregarSiEs(out: MutableSet<String>, crudo: String, dominio: String) {
            val n = crudo.trim().lowercase().removePrefix("*.")
            if ((n == dominio || n.endsWith(".$dominio")) && RE_DOMINIO.matches(n)) out.add(n)
        }

        /**
         * Los nombres de [dominio] que aparecen en cualquier texto: una lista JSON, líneas con direcciones web, etc.
         * Sirve para todas las fuentes que no tienen un formato propio. No toma "otrodominio.com" ni "dominio.com.otro.net".
         */
        fun extraerNombres(texto: String, dominio: String): Set<String> {
            val re = Regex("(?<![A-Za-z0-9_.*-])((?:[A-Za-z0-9_*-]+\\.)*" + Regex.escape(dominio) + ")(?![A-Za-z0-9-]|\\.[A-Za-z0-9])", RegexOption.IGNORE_CASE)
            val out = java.util.TreeSet<String>()
            for (m in re.findAll(texto)) agregarSiEs(out, m.value, dominio)
            return out
        }

        fun resolverTodas(nombre: String): List<String> = try {
            InetAddress.getAllByName(nombre).mapNotNull { it.hostAddress }.distinct()
        } catch (_: Exception) { emptyList() }

        fun descargar(url: String, esperaMs: Int): String? = try {
            val c = URL(url).openConnection() as HttpURLConnection
            c.connectTimeout = 15000
            c.readTimeout = esperaMs
            c.setRequestProperty("User-Agent", "ZumoPort/1.0")
            if (c.responseCode != 200) null else c.inputStream.bufferedReader().use { leerHasta(it, MAX_RESPUESTA) }
        } catch (_: Exception) { null }

        /** Tope de lo que se lee de una fuente (la de páginas archivadas puede ser enorme). */
        const val MAX_RESPUESTA = 6_000_000

        private fun leerHasta(r: java.io.Reader, max: Int): String {
            val sb = StringBuilder()
            val buf = CharArray(8192)
            while (sb.length < max) {
                val n = r.read(buf)
                if (n < 0) break
                sb.append(buf, 0, n)
            }
            return sb.toString()
        }

        val PALABRAS: List<String> = (
            "www www2 www3 web mail webmail smtp pop pop3 imap mx mx1 mx2 ns ns1 ns2 ns3 dns dns1 dns2 ftp sftp ssh vpn remote " +
            "api api2 apis app apps m mobile dev develop staging stage test testing qa uat demo beta alpha sandbox preprod prod production " +
            "admin administrator panel cpanel whm plesk dashboard portal login sso auth oauth accounts account id identity secure " +
            "cdn static assets img images media files file download downloads upload uploads docs doc help support status blog news shop store " +
            "tienda pay payment payments billing checkout cart git gitlab github bitbucket svn jenkins ci build repo registry docker k8s kube " +
            "grafana kibana prometheus elastic search db database mysql postgres redis mongo sql backup backups old new beta2 intranet internal " +
            "private corp office exchange autodiscover owa lyncdiscover sip meet chat video voip pbx proxy gateway gw edge lb loadbalancer " +
            "monitor monitoring nagios zabbix log logs stats analytics track tracking tag ads ad affiliates partner partners client clients " +
            "customer customers cliente clientes soporte ayuda tienda web1 web2 server server1 server2 srv host host1 node node1 node2 " +
            "cloud s3 storage bucket vps cpanel webdisk calendar cal wiki forum forums community crm erp hr jobs careers events live tv radio " +
            "music play games game wap smtp2 relay mailer newsletter email bounce list lists vpn2 ras citrix rdp ts terminal mdm m2 en es pt fr de"
        ).split(' ').distinct()
    }
}
