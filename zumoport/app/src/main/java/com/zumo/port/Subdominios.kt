package com.zumo.port

import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Un subdominio encontrado, con las IP a las que apunta (vacío = existe en un certificado pero hoy no responde) y de dónde salió. */
class Subdominio(val nombre: String, val ips: List<String>, val fuente: String) {
    val resuelve: Boolean get() = ips.isNotEmpty()
}

/** Cuáles métodos usar para buscar. */
class Metodos(val certificados: Boolean = true, val hackertarget: Boolean = true, val lista: Boolean = true)

/**
 * Busca subdominios de un dominio por tres caminos que se suman:
 *  - certificados públicos (crt.sh): los nombres que alguna vez salieron en un certificado HTTPS,
 *  - HackerTarget: su base de nombres conocidos,
 *  - lista de nombres comunes (www, mail, api, cdn...): se prueba cada uno preguntando al DNS.
 * Después se resuelve cada nombre a IP y se descartan los que solo existen por un DNS "comodín" (*.dominio).
 */
class BuscadorSubdominios(
    private val resolver: (String) -> List<String> = { n -> resolverTodas(n) },
    private val bajar: (String, Int) -> String? = { u, ms -> descargar(u, ms) },
) {
    private val cancelado = AtomicBoolean(false)
    fun cancelar() { cancelado.set(true) }

    fun buscar(
        dominio: String,
        metodos: Metodos,
        alEstado: (String) -> Unit,
        alHallar: (Subdominio) -> Unit,
        alAvanzar: (Int, Int) -> Unit,
    ) {
        cancelado.set(false)
        val candidatos = ConcurrentHashMap<String, String>()   // nombre -> fuente
        if (metodos.certificados && !cancelado.get()) {
            alEstado("Buscando en certificados públicos (crt.sh)…")
            val t = bajar("https://crt.sh/?q=" + URLEncoder.encode("%." + dominio, "UTF-8") + "&output=json", 40000)
            if (t == null) alEstado("crt.sh no respondió (a veces está saturado)")
            else parsearCrtsh(t, dominio).forEach { candidatos.putIfAbsent(it, "certificados") }
        }
        if (metodos.hackertarget && !cancelado.get()) {
            alEstado("Buscando en HackerTarget…")
            val t = bajar("https://api.hackertarget.com/hostsearch/?q=" + URLEncoder.encode(dominio, "UTF-8"), 20000)
            if (t == null) alEstado("HackerTarget no respondió")
            else parsearHackertarget(t, dominio).forEach { candidatos.putIfAbsent(it, "HackerTarget") }
        }
        if (metodos.lista && !cancelado.get()) {
            for (p in PALABRAS) candidatos.putIfAbsent("$p.$dominio", "lista")
        }
        candidatos.putIfAbsent(dominio, "dominio")
        if (cancelado.get()) return

        // DNS comodín: si un nombre inventado también resuelve, esas IP no cuentan como hallazgo
        val comodin = resolver("zp-" + System.nanoTime().toString(36) + "." + dominio).toSet()
        if (comodin.isNotEmpty()) alEstado("Este dominio tiene DNS comodín: se ignoran los nombres que solo apuntan a ${comodin.joinToString(", ")}")

        val nombres = candidatos.keys.sorted()
        val hechos = AtomicInteger(0)
        val pool = Executors.newFixedThreadPool(48)
        alEstado("Resolviendo ${nombres.size} nombres…")
        try {
            for (n in nombres) {
                if (cancelado.get()) break
                pool.execute {
                    try {
                        if (!cancelado.get()) {
                            val ips = resolver(n).filter { it !in comodin }
                            val fuente = candidatos[n].orEmpty()
                            // los de la lista que no existen no se muestran; los de certificados sí (se marcan sin IP)
                            if (ips.isNotEmpty() || fuente == "certificados" || fuente == "HackerTarget") alHallar(Subdominio(n, ips, fuente))
                        }
                    } catch (_: Exception) {
                    } finally {
                        alAvanzar(hechos.incrementAndGet(), nombres.size)
                    }
                }
            }
        } finally {
            pool.shutdown()
            if (cancelado.get()) pool.shutdownNow()
            try { pool.awaitTermination(30, TimeUnit.MINUTES) } catch (_: InterruptedException) { pool.shutdownNow() }
        }
    }

    companion object {
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

        fun resolverTodas(nombre: String): List<String> = try {
            InetAddress.getAllByName(nombre).mapNotNull { it.hostAddress }.distinct()
        } catch (_: Exception) { emptyList() }

        fun descargar(url: String, esperaMs: Int): String? = try {
            val c = URL(url).openConnection() as HttpURLConnection
            c.connectTimeout = 15000
            c.readTimeout = esperaMs
            c.setRequestProperty("User-Agent", "ZumoPort/1.0")
            if (c.responseCode != 200) null else c.inputStream.bufferedReader().use { it.readText() }
        } catch (_: Exception) { null }

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
