package com.zumo.port

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.ServerSocket
import java.util.Collections
import java.util.concurrent.CopyOnWriteArrayList

class ObjetivosTest {
    private fun lista(t: String) = Objetivos.analizar(t).equipos().toList()

    @Test fun ip_suelta() = assertEquals(listOf("192.168.1.10"), lista("192.168.1.10"))

    @Test fun rango_corto() {
        val l = lista("192.168.1.250-254")
        assertEquals(listOf("192.168.1.250", "192.168.1.251", "192.168.1.252", "192.168.1.253", "192.168.1.254"), l)
    }

    @Test fun rango_completo_cruza_de_red() {
        assertEquals(listOf("10.0.0.254", "10.0.0.255", "10.0.1.0", "10.0.1.1"), lista("10.0.0.254-10.0.1.1"))
    }

    @Test fun cidr_24_sin_red_ni_difusion() {
        val l = lista("192.168.0.0/24")
        assertEquals(254, l.size)
        assertEquals("192.168.0.1", l.first()); assertEquals("192.168.0.254", l.last())
    }

    @Test fun cidr_30_y_32() {
        assertEquals(listOf("10.0.0.1", "10.0.0.2"), lista("10.0.0.0/30"))
        assertEquals(listOf("10.0.0.7"), lista("10.0.0.7/32"))
        assertEquals(listOf("10.0.0.6", "10.0.0.7"), lista("10.0.0.6/31"))
    }

    @Test fun cidr_se_alinea_a_la_red() = assertEquals("192.168.5.1", lista("192.168.5.77/24").first())

    @Test fun varios_a_la_vez_sin_repetir() {
        assertEquals(listOf("1.1.1.1", "8.8.8.8", "ejemplo.com"), lista("1.1.1.1, 8.8.8.8 1.1.1.1\nejemplo.com"))
    }

    @Test fun dominio_se_limpia() {
        assertEquals(listOf("ejemplo.com"), lista("HTTPS://Ejemplo.com:8443/ruta?x=1"))
    }

    @Test fun cantidad_sin_armar_la_lista() {
        val p = Objetivos.analizar("10.0.0.0/16")
        assertEquals(65534L, p.total)
    }

    @Test fun errores_claros() {
        assertTrue(Objetivos.analizar("300.1.1.1").errores.isNotEmpty())
        assertTrue(Objetivos.analizar("10.0.0.0/8").errores.isNotEmpty())
        assertTrue(Objetivos.analizar("10.0.0.9-10.0.0.1").errores.isNotEmpty())
        assertTrue(Objetivos.analizar("10.0.0.1-300").errores.isNotEmpty())
        assertTrue(Objetivos.analizar("hola que tal").errores.isNotEmpty())
        assertTrue(Objetivos.analizar("").errores.isNotEmpty())
        assertTrue(Objetivos.analizar("10.0.0.0/16 11.0.0.0/16").errores.isNotEmpty())   // juntos pasan el tope
    }

    @Test fun detecta_redes_locales() {
        assertTrue(Objetivos.analizar("192.168.1.1-254").hayLocales)
        assertTrue(Objetivos.analizar("10.0.0.0/24").hayLocales)
        assertTrue(Objetivos.analizar("172.20.1.5").hayLocales)
        assertTrue(Objetivos.analizar("8.8.8.8, 192.168.0.1").hayLocales)
        assertFalse(Objetivos.analizar("172.32.1.5").hayLocales)
        assertFalse(Objetivos.analizar("8.8.8.8").hayLocales)
        assertFalse(Objetivos.analizar("ejemplo.com").hayLocales)
        assertFalse(Objetivos.analizar("100.64.0.1").hayLocales)   // CGNAT de la operadora: sí se alcanza por datos móviles
    }

    @Test fun dominio_con_guion_no_se_toma_por_rango() = assertEquals(listOf("mi-sitio.com"), lista("mi-sitio.com"))
}

class PuertosTest {
    @Test fun lista_y_rangos() {
        assertEquals(listOf(80, 443, 8000, 8001, 8002), Puertos.analizar("443, 80 8000-8002 80").puertos)
    }

    @Test fun errores() {
        assertNotNull(Puertos.analizar("0").error)
        assertNotNull(Puertos.analizar("70000").error)
        assertNotNull(Puertos.analizar("90-80").error)
        assertNotNull(Puertos.analizar("abc").error)
        assertNotNull(Puertos.analizar("").error)
        assertNotNull(Puertos.analizar("1-65535").error)    // demasiados
        assertNull(Puertos.analizar("1-1024").error)
    }
}

class EscanerTest {
    private fun escanear(equipos: List<String>, puertos: List<Int>, opc: Opciones = Opciones(Velocidad.NORMAL)): List<Hallazgo> {
        val out = CopyOnWriteArrayList<Hallazgo>()
        Escaner().escanear(equipos.asSequence(), puertos, opc, equipos.size.toLong(), { out.add(it) }, { _, _ -> })
        return out.sortedBy { it.puerto }
    }

    @Test fun abierto_y_cerrado() {
        val a = ServerSocket(0, 5, java.net.InetAddress.getByName("127.0.0.1"))
        val cerrado = ServerSocket(0).use { it.localPort }     // puerto que acaba de liberarse: nadie escucha
        try {
            val r = escanear(listOf("127.0.0.1"), listOf(a.localPort, cerrado), Opciones(Velocidad.NORMAL, verificarWeb = false, leerBanner = false))
            assertEquals(1, r.size)
            assertEquals(a.localPort, r[0].puerto)
            assertEquals(Hallazgo.Tipo.ABIERTO, r[0].tipo)
        } finally { a.close() }
    }

    /** Abre el primer puerto libre de la lista (Escaner solo trata como web/banner los puertos conocidos). */
    private fun abrirUno(candidatos: List<Int>): ServerSocket {
        for (p in candidatos) try { return ServerSocket(p, 50, java.net.InetAddress.getByName("127.0.0.1")) } catch (_: Exception) {}
        throw AssertionError("ningún puerto libre entre $candidatos")
    }

    @Test fun puerto_web_muestra_codigo_y_servidor() {
        val s = servidorWebEn(listOf(3000, 5000, 8008, 8081, 8088, 9090), "HTTP/1.1 301 Moved Permanently\r\nServer: nginx\r\nLocation: https://ejemplo.com/\r\n\r\n")
        try {
            val r = escanear(listOf("127.0.0.1"), listOf(s.localPort))
            assertEquals(1, r.size)
            assertEquals(Hallazgo.Tipo.WEB, r[0].tipo)
            assertEquals(301, r[0].http)
            assertTrue(r[0].webOk)
            assertEquals("nginx → https://ejemplo.com/", r[0].detalle)
        } finally { s.close() }
    }

    @Test fun web_que_no_contesta_http_queda_como_abierto() {
        val s = servidorWebEn(listOf(3000, 5000, 8008, 8081, 8088, 9090), "esto no es http\r\n")
        try {
            val r = escanear(listOf("127.0.0.1"), listOf(s.localPort))
            assertEquals(Hallazgo.Tipo.ABIERTO, r[0].tipo)
        } finally { s.close() }
    }

    @Test fun sin_verificar_web_no_manda_nada() {
        val s = servidorWebEn(listOf(3000, 5000, 8008, 8081, 8088, 9090), "HTTP/1.1 200 OK\r\n\r\n")
        try {
            val r = escanear(listOf("127.0.0.1"), listOf(s.localPort), Opciones(Velocidad.NORMAL, verificarWeb = false))
            assertEquals(Hallazgo.Tipo.ABIERTO, r[0].tipo)
        } finally { s.close() }
    }

    private fun servidorWebEn(candidatos: List<Int>, respuesta: String): ServerSocket {
        val ss = abrirUno(candidatos)
        Thread {
            try {
                while (true) {
                    val c = ss.accept()
                    Thread {
                        try {
                            val i = c.getInputStream(); var fin = 0
                            while (fin < 4) { val b = i.read(); if (b < 0) break; fin = if ((b == 13 && (fin == 0 || fin == 2)) || (b == 10 && (fin == 1 || fin == 3))) fin + 1 else 0 }
                            c.getOutputStream().write(respuesta.toByteArray()); c.getOutputStream().flush(); c.close()
                        } catch (_: Exception) {}
                    }.start()
                }
            } catch (_: Exception) {}
        }.start()
        return ss
    }

    @Test fun cabecera_que_no_es_http() {
        assertNull(Escaner.leerCabecera("SSH-2.0-OpenSSH_9.6\r\n"))
        assertNull(Escaner.leerCabecera(""))
        assertEquals(200, Escaner.leerCabecera("HTTP/1.0 200 OK\r\n\r\n")!!.codigo)
    }

    @Test fun hallazgo_resume() {
        assertTrue(Hallazgo("a", "1.1.1.1", 443, Hallazgo.Tipo.TLS_WEB, 200, "cloudflare").webOk)
        assertFalse(Hallazgo("a", "1.1.1.1", 80, Hallazgo.Tipo.WEB, 404).webOk)
        assertEquals("HTTPS 200 · cloudflare", Hallazgo("a", "1.1.1.1", 443, Hallazgo.Tipo.TLS_WEB, 200, "cloudflare").resumen())
    }

    @Test fun un_dominio_que_no_resuelve_no_da_nada() {
        val r = Collections.synchronizedList(ArrayList<Hallazgo>())
        Escaner { null }.escanear(sequenceOf("no-existe.invalid"), listOf(80, 443), Opciones(), 1, { r.add(it) }, { _, _ -> })
        assertTrue(r.isEmpty())
    }

    @Test fun cancelar_corta_el_escaneo() {
        val e = Escaner()
        val t = Thread { e.escanear(generateSequence(1) { it + 1 }.take(100000).map { "10.255.${it / 250 % 250}.${it % 250 + 1}" }, listOf(81), Opciones(Velocidad.SUAVE), 100000, {}, { _, _ -> }) }
        t.start(); Thread.sleep(400); e.cancelar(); t.join(15000)
        assertFalse("el escaneo siguió después de cancelar", t.isAlive)
    }

    @Test fun banner_de_un_servicio_que_habla_primero() {
        val ss = abrirUno(listOf(5900, 3306, 143, 110, 465))
        Thread { try { while (true) { val c = ss.accept(); c.getOutputStream().write("SSH-2.0-Prueba_1.0\r\nresto".toByteArray()); c.getOutputStream().flush(); c.close() } } catch (_: Exception) {} }.start()
        try {
            val r = escanear(listOf("127.0.0.1"), listOf(ss.localPort))
            assertEquals(Hallazgo.Tipo.BANNER, r[0].tipo)
            assertEquals("SSH-2.0-Prueba_1.0", r[0].detalle)
        } finally { ss.close() }
    }
}

class ReverseDnsTest {
    @org.junit.Test fun nombre_normal() = assertEquals("srv.ejemplo.com", ReverseDns.resolver("1.2.3.4") { "srv.ejemplo.com." })
    @org.junit.Test fun sin_ptr_devuelve_la_misma_ip() = assertNull(ReverseDns.resolver("1.2.3.4") { it })
    @org.junit.Test fun vacio_es_null() = assertNull(ReverseDns.resolver("1.2.3.4") { "" })
    @org.junit.Test fun error_es_null() = assertNull(ReverseDns.resolver("1.2.3.4") { throw java.net.UnknownHostException() })
}

class SubdominiosTest {
    @Test fun limpia_el_dominio() {
        assertEquals("ejemplo.com", BuscadorSubdominios.limpiarDominio("https://Ejemplo.com/x"))
        assertEquals("ejemplo.com", BuscadorSubdominios.limpiarDominio("*.ejemplo.com"))
        assertNull(BuscadorSubdominios.limpiarDominio("10.0.0.1"))
        assertNull(BuscadorSubdominios.limpiarDominio("hola"))
    }

    @Test fun lee_crtsh() {
        val json = """[{"issuer_ca_id":1,"name_value":"ejemplo.com\nwww.ejemplo.com","id":5},
            {"name_value":"*.api.ejemplo.com","id":6},{"name_value":"otro.com","id":7},{"name_value":"MAIL.Ejemplo.COM"}]"""
        assertEquals(setOf("api.ejemplo.com", "ejemplo.com", "mail.ejemplo.com", "www.ejemplo.com"), BuscadorSubdominios.parsearCrtsh(json, "ejemplo.com"))
    }

    @Test fun lee_hackertarget_y_su_error() {
        assertEquals(setOf("a.ejemplo.com", "b.ejemplo.com"), BuscadorSubdominios.parsearHackertarget("a.ejemplo.com,1.2.3.4\nb.ejemplo.com,5.6.7.8\nx.otro.com,9.9.9.9", "ejemplo.com"))
        assertTrue(BuscadorSubdominios.parsearHackertarget("API count exceeded - Increase Quota with Membership", "ejemplo.com").isEmpty())
    }

    @Test fun busca_resuelve_y_filtra_el_comodin() {
        val dns = mapOf("ejemplo.com" to listOf("1.1.1.1"), "www.ejemplo.com" to listOf("1.1.1.1"), "api.ejemplo.com" to listOf("2.2.2.2", "2.2.2.3"),
            "viejo.ejemplo.com" to emptyList())
        val b = BuscadorSubdominios(
            resolver = { n -> if (n.startsWith("zp-")) listOf("9.9.9.9") else if (n.endsWith(".ejemplo.com") && n !in dns) listOf("9.9.9.9") else dns[n].orEmpty() },
            bajar = { u, _ -> if (u.contains("crt.sh")) """[{"name_value":"viejo.ejemplo.com\napi.ejemplo.com"}]""" else "www.ejemplo.com,1.1.1.1" },
        )
        val out = CopyOnWriteArrayList<Subdominio>()
        val estados = CopyOnWriteArrayList<String>()
        b.buscar("ejemplo.com", Metodos(), { estados.add(it) }, { out.add(it) }, { _, _ -> })
        val por = out.associateBy { it.nombre }
        assertEquals(listOf("2.2.2.2", "2.2.2.3"), por["api.ejemplo.com"]!!.ips)
        assertEquals(listOf("1.1.1.1"), por["www.ejemplo.com"]!!.ips)
        assertTrue("el de certificados sin IP se informa", por["viejo.ejemplo.com"]!!.ips.isEmpty())
        assertEquals("nada de la lista que solo existe por el comodín", setOf("ejemplo.com", "www.ejemplo.com", "api.ejemplo.com", "viejo.ejemplo.com"), por.keys)
        assertTrue(estados.any { it.contains("comodín") })
    }

    @Test fun sin_internet_avisa_y_sigue() {
        val b = BuscadorSubdominios(resolver = { emptyList() }, bajar = { _, _ -> null })
        val estados = CopyOnWriteArrayList<String>()
        b.buscar("ejemplo.com", Metodos(), { estados.add(it) }, {}, { _, _ -> })
        assertTrue(estados.any { it.contains("crt.sh no respondió") })
        assertTrue(estados.any { it.contains("HackerTarget no respondió") })
    }

    @Test fun la_lista_no_tiene_repetidos() = assertEquals(BuscadorSubdominios.PALABRAS.size, BuscadorSubdominios.PALABRAS.toSet().size)
}

/** Respuesta DNS en JSON (como la de Cloudflare o Google) con uno o más registros TXT. */
private fun jsonTxt(vararg filas: String, estado: Int = 0): String =
    "{\"Status\":$estado,\"Answer\":[" + filas.joinToString(",") { "{\"name\":\"x\",\"type\":16,\"TTL\":60,\"data\":\"\\\"$it\\\"\"}" } + "]}"

private const val CLOUDFLARE = "13335 | 104.16.0.0/12 | US | arin | 2014-03-28"
private const val CLOUDFLARE_NOMBRE = "13335 | US | arin | 2010-07-14 | CLOUDFLARENET, US"

class AsnTest {
    @Test fun nombre_dns_de_una_ip() {
        assertEquals("77.0.16.104.origin.asn.cymru.com", BuscadorAsn.nombreOrigen("104.16.0.77"))
        assertEquals("1." + "0.".repeat(23) + "8.b.d.0.1.0.0.2.origin6.asn.cymru.com", BuscadorAsn.nombreOrigen("2001:db8::1"))
        assertNull(BuscadorAsn.nombreOrigen("hola"))
        assertNull(BuscadorAsn.nombreOrigen("300.1.1.1"))
        assertNull(BuscadorAsn.nombreOrigen("ejemplo.com"))
    }

    @Test fun lee_las_filas_de_cymru() {
        val o = BuscadorAsn.parsearOrigen(CLOUDFLARE)!!
        assertEquals(listOf(13335L), o.numeros); assertEquals("104.16.0.0/12", o.prefijo); assertEquals("US", o.pais)
        assertEquals(listOf(13335L, 209242L), BuscadorAsn.parsearOrigen("13335 209242 | 1.2.3.0/24 | US | arin | 2014-03-28")!!.numeros)
        assertNull(BuscadorAsn.parsearOrigen("NA | NA"))
        assertNull(BuscadorAsn.parsearOrigen(""))
        assertEquals("CLOUDFLARENET" to "US", BuscadorAsn.parsearNombre(CLOUDFLARE_NOMBRE))
        assertEquals("ACME Hosting Ltd" to "AR", BuscadorAsn.parsearNombre("65000 | AR | lacnic | 2001-01-01 | ACME Hosting Ltd, AR"))
        assertNull(BuscadorAsn.parsearNombre("13335 | US"))
    }

    @Test fun lee_las_respuestas_dns() {
        assertEquals(listOf(CLOUDFLARE), BuscadorAsn.respuestasTxt(jsonTxt(CLOUDFLARE)))
        assertEquals(0, BuscadorAsn.estadoDns(jsonTxt(CLOUDFLARE)))
        assertEquals(3, BuscadorAsn.estadoDns("{\"Status\":3}"))
        assertEquals(-1, BuscadorAsn.estadoDns("<html>"))
        assertTrue(BuscadorAsn.respuestasTxt("{\"Status\":3}").isEmpty())
    }

    private fun falso(llamadas: java.util.concurrent.atomic.AtomicInteger): (String, Int) -> String? = { u, _ ->
        llamadas.incrementAndGet()
        when {
            "77.0.16.104.origin.asn.cymru.com" in u -> jsonTxt(CLOUDFLARE)
            "AS13335.asn.cymru.com" in u -> jsonTxt(CLOUDFLARE_NOMBRE)
            "9.9.9.9.origin.asn.cymru.com" in u -> "{\"Status\":3}"
            else -> null
        }
    }

    @Test fun averigua_el_asn_y_lo_recuerda() {
        val n = java.util.concurrent.atomic.AtomicInteger(0)
        val b = BuscadorAsn(falso(n))
        val r = b.consultar("104.16.0.77")!!.single()
        assertEquals(13335L, r.numero); assertEquals("CLOUDFLARENET", r.nombre); assertEquals("US", r.pais)
        assertEquals("104.16.0.0/12", r.prefijo)
        assertEquals("AS13335 · CLOUDFLARENET · US", r.etiqueta)
        val usadas = n.get()
        assertEquals(13335L, b.consultar("104.16.0.77")!!.single().numero)
        assertEquals("la segunda vez sale de la memoria", usadas, n.get())
    }

    @Test fun una_ip_sin_asn_no_es_una_falla() {
        val b = BuscadorAsn(falso(java.util.concurrent.atomic.AtomicInteger(0)))
        assertEquals(emptyList<InfoAsn>(), b.consultar("9.9.9.9"))
        assertFalse(b.sinServicio)
    }

    @Test fun las_ip_locales_no_se_consultan() {
        val n = java.util.concurrent.atomic.AtomicInteger(0)
        val b = BuscadorAsn(falso(n))
        assertEquals(emptyList<InfoAsn>(), b.consultar("192.168.1.5"))
        assertEquals(emptyList<InfoAsn>(), b.consultar("10.0.0.1"))
        assertEquals(0, n.get())
    }

    @Test fun si_el_primer_servicio_falla_usa_el_otro() {
        val urls = CopyOnWriteArrayList<String>()
        val b = BuscadorAsn { u, _ ->
            urls.add(u)
            if ("cloudflare-dns.com" in u) null
            else if ("77.0.16.104.origin" in u) jsonTxt(CLOUDFLARE) else jsonTxt(CLOUDFLARE_NOMBRE)
        }
        assertEquals(13335L, b.consultar("104.16.0.77")!!.single().numero)
        assertTrue(urls.any { "dns.google" in it })
    }

    @Test fun sin_servicio_deja_de_insistir() {
        val n = java.util.concurrent.atomic.AtomicInteger(0)
        val b = BuscadorAsn { _, _ -> n.incrementAndGet(); null }
        for (i in 1..BuscadorAsn.MAX_FALLOS) assertNull(b.consultar("8.8.$i.8"))
        assertTrue(b.sinServicio)
        val antes = n.get()
        assertNull(b.consultar("8.8.100.8"))
        assertEquals(antes, n.get())
    }
}

class SubdominiosExtrasTest {
    @Test fun completa_asn_y_puertos_de_cada_subdominio() {
        val dns = mapOf("ejemplo.com" to listOf("1.1.1.1"), "www.ejemplo.com" to listOf("1.1.1.1"), "api.ejemplo.com" to listOf("2.2.2.2"),
            "interno.ejemplo.com" to listOf("10.0.0.5"))
        val asn = BuscadorAsn { u, _ ->
            when {
                "1.1.1.1.origin.asn.cymru.com" in u -> jsonTxt(CLOUDFLARE)
                "2.2.2.2.origin.asn.cymru.com" in u -> jsonTxt("15169 | 2.2.2.0/24 | US | arin | 2000-03-30")
                "AS13335.asn.cymru.com" in u -> jsonTxt(CLOUDFLARE_NOMBRE)
                "AS15169.asn.cymru.com" in u -> jsonTxt("15169 | US | arin | 2000-03-30 | GOOGLE, US")
                else -> null
            }
        }
        val sondeadas = Collections.synchronizedSet(HashSet<String>())
        val b = BuscadorSubdominios(
            resolver = { n -> dns[n].orEmpty() },
            bajar = { _, _ -> "www.ejemplo.com,1.1.1.1\napi.ejemplo.com,2.2.2.2\ninterno.ejemplo.com,10.0.0.5" },
            asn = asn,
            abierto = { ip, p -> sondeadas.add("$ip:$p"); (ip == "1.1.1.1" && (p == 80 || p == 443)) || (ip == "2.2.2.2" && p == 8080) },
        )
        val out = CopyOnWriteArrayList<Subdominio>()
        val actualizaciones = java.util.concurrent.atomic.AtomicInteger(0)
        b.buscar("ejemplo.com", Metodos(certificados = false, hackertarget = true, lista = false), {}, { out.add(it) }, { _, _ -> },
            Extras(asn = true, puertos = listOf(80, 443, 8080, 22))) { actualizaciones.incrementAndGet() }
        val por = out.associateBy { it.nombre }

        val www = por["www.ejemplo.com"]!!
        assertEquals(listOf("AS13335 · CLOUDFLARENET · US"), www.asns.map { it.etiqueta })
        assertEquals(listOf(80, 443), www.puertos)
        assertTrue(www.asnListo && www.puertosListo)
        assertEquals(listOf(13335L), por["ejemplo.com"]!!.asns.map { it.numero })

        val api = por["api.ejemplo.com"]!!
        assertEquals(listOf(15169L), api.asns.map { it.numero })
        assertEquals(listOf(8080), api.puertos)

        val interno = por["interno.ejemplo.com"]!!
        assertTrue("la IP local no tiene ASN pero la fila termina", interno.asns.isEmpty() && interno.asnListo)
        assertTrue(interno.puertos.isEmpty() && interno.puertosListo)
        assertTrue("no se prueban puertos en redes locales", sondeadas.none { it.startsWith("10.0.0.5") })
        assertEquals("cada IP se prueba una sola vez por puerto, aunque la compartan varios nombres", 8, sondeadas.size)
        assertTrue(actualizaciones.get() > 0)
    }

    @Test fun sin_pedir_extras_no_consulta_nada() {
        val b = BuscadorSubdominios(
            resolver = { n -> if (n == "ejemplo.com") listOf("1.1.1.1") else emptyList() },
            bajar = { _, _ -> null },
            asn = BuscadorAsn { _, _ -> throw AssertionError("no debía consultar el ASN") },
            abierto = { _, _ -> throw AssertionError("no debía probar puertos") },
        )
        val out = CopyOnWriteArrayList<Subdominio>()
        b.buscar("ejemplo.com", Metodos(false, false, false), {}, { out.add(it) }, { _, _ -> })
        assertEquals(1, out.size)
        assertFalse(out[0].asnListo); assertFalse(out[0].puertosListo)
    }

    @Test fun si_el_asn_no_responde_la_fila_termina_igual() {
        val b = BuscadorSubdominios(
            resolver = { n -> if (n == "ejemplo.com") listOf("1.1.1.1") else emptyList() },
            bajar = { _, _ -> null },
            asn = BuscadorAsn { _, _ -> null },
        )
        val out = CopyOnWriteArrayList<Subdominio>()
        val estados = CopyOnWriteArrayList<String>()
        b.buscar("ejemplo.com", Metodos(false, false, false), { estados.add(it) }, { out.add(it) }, { _, _ -> }, Extras(asn = true))
        assertTrue(out[0].asns.isEmpty()); assertTrue(out[0].asnListo)
    }
}

/** Detener tiene que volver enseguida aunque haya consultas DNS o descargas colgadas (no se pueden interrumpir). */
class SubdominiosCancelarTest {
    /** Se queda esperando aunque lo interrumpan, como una consulta DNS de Android. */
    private fun colgar(suelta: java.util.concurrent.CountDownLatch) {
        var listo = false
        while (!listo) { try { suelta.await(); listo = true } catch (_: InterruptedException) {} }
    }

    private fun cancelarA(b: BuscadorSubdominios, ms: Long) { Thread { Thread.sleep(ms); b.cancelar() }.also { it.isDaemon = true }.start() }

    @Test fun vuelve_enseguida_si_el_dns_de_los_nombres_se_cuelga() {
        val suelta = java.util.concurrent.CountDownLatch(1)
        val b = BuscadorSubdominios(resolver = { n -> if (!n.startsWith("zp-")) colgar(suelta); emptyList() }, bajar = { _, _ -> null })
        val halladas = CopyOnWriteArrayList<Subdominio>()
        val t0 = System.currentTimeMillis()
        cancelarA(b, 400)
        b.buscar("ejemplo.com", Metodos(false, false, true), {}, { halladas.add(it) }, { _, _ -> })
        val ms = System.currentTimeMillis() - t0
        suelta.countDown()
        assertTrue("tardó $ms ms en volver", ms < 3000)
    }

    @Test fun vuelve_enseguida_si_la_consulta_comodin_se_cuelga() {
        val suelta = java.util.concurrent.CountDownLatch(1)
        val b = BuscadorSubdominios(resolver = { colgar(suelta); emptyList() }, bajar = { _, _ -> null })
        val t0 = System.currentTimeMillis()
        cancelarA(b, 400)
        b.buscar("ejemplo.com", Metodos(false, false, true), {}, {}, { _, _ -> })
        val ms = System.currentTimeMillis() - t0
        suelta.countDown()
        assertTrue("tardó $ms ms en volver", ms < 3000)
    }

    @Test fun vuelve_enseguida_si_la_descarga_se_cuelga() {
        val suelta = java.util.concurrent.CountDownLatch(1)
        val b = BuscadorSubdominios(resolver = { emptyList() }, bajar = { _, _ -> colgar(suelta); null })
        val t0 = System.currentTimeMillis()
        cancelarA(b, 400)
        b.buscar("ejemplo.com", Metodos(true, true, true), {}, {}, { _, _ -> })
        val ms = System.currentTimeMillis() - t0
        suelta.countDown()
        assertTrue("tardó $ms ms en volver", ms < 3000)
    }

    @Test fun vuelve_enseguida_si_se_cuelga_el_asn_o_un_puerto() {
        val suelta = java.util.concurrent.CountDownLatch(1)
        val b = BuscadorSubdominios(
            resolver = { n -> if (n == "ejemplo.com") listOf("1.1.1.1") else emptyList() },
            bajar = { _, _ -> null },
            asn = BuscadorAsn { _, _ -> colgar(suelta); null },
            abierto = { _, _ -> colgar(suelta); false },
        )
        val t0 = System.currentTimeMillis()
        cancelarA(b, 600)
        b.buscar("ejemplo.com", Metodos(false, false, false), {}, {}, { _, _ -> }, Extras(asn = true, puertos = listOf(80, 443)))
        val ms = System.currentTimeMillis() - t0
        suelta.countDown()
        assertTrue("tardó $ms ms en volver", ms < 3000)
    }

    @Test fun lo_que_llega_tarde_de_una_busqueda_cancelada_no_se_agrega() {
        val suelta = java.util.concurrent.CountDownLatch(1)
        val b = BuscadorSubdominios(resolver = { n -> if (n.startsWith("zp-")) emptyList() else { colgar(suelta); listOf("1.2.3.4") } }, bajar = { _, _ -> null })
        val halladas = CopyOnWriteArrayList<Subdominio>()
        cancelarA(b, 300)
        b.buscar("ejemplo.com", Metodos(false, false, true), {}, { halladas.add(it) }, { _, _ -> })
        suelta.countDown()
        Thread.sleep(400)       // los hilos colgados terminan ahora: no tienen que sumar nada
        assertTrue(halladas.isEmpty())
    }
}

class FuentesTest {
    private val dominio = "ejemplo.com"

    /** Lo que contestaría cada fuente (cada una con su formato). */
    private fun respuesta(u: String): String? = when {
        "crt.sh" in u -> "[{\"name_value\":\"ct.ejemplo.com\\nct2.ejemplo.com\"}]"
        "hackertarget" in u -> "ht.ejemplo.com,1.1.1.1\nus.ejemplo.com,1.1.1.1"
        "otx.alienvault.com" in u -> "{\"passive_dns\":[{\"address\":\"1.1.1.1\",\"hostname\":\"otx.ejemplo.com\"}],\"count\":1}"
        "certspotter" in u -> "[{\"id\":\"1\",\"dns_names\":[\"cs.ejemplo.com\",\"*.wild.ejemplo.com\"]}]"
        "urlscan.io" in u -> "{\"results\":[{\"page\":{\"domain\":\"us.ejemplo.com\",\"url\":\"https://us.ejemplo.com/\"}}]}"
        "jldc.me" in u -> "[\"an.ejemplo.com\"]"
        "subdomain.center" in u -> "[\"sc.ejemplo.com\"]"
        "web.archive.org" in u -> "http://wb.ejemplo.com/a\nhttps://wb.ejemplo.com:8443/b\nhttp://otro.net/x"
        else -> null
    }

    private val webs = Fuente.ELEGIBLES.filter { it.web }.toSet()

    @Test fun extrae_nombres_de_cualquier_texto() {
        val t = "[\"a.ejemplo.com\",\"*.b.ejemplo.com\",\"notejemplo.com\",\"ejemplo.com.evil.net\",\"x.ejemplo.com.evil.net\",\"C.EJEMPLO.COM\"]\n" +
            "https://d.ejemplo.com:8443/x\nhttp://user@e.ejemplo.com/y\n{\"u\":\"https:\\/\\/f.ejemplo.com\\/z\"}"
        assertEquals(setOf("a.ejemplo.com", "b.ejemplo.com", "c.ejemplo.com", "d.ejemplo.com", "e.ejemplo.com", "f.ejemplo.com"),
            BuscadorSubdominios.extraerNombres(t, dominio))
        assertTrue(BuscadorSubdominios.extraerNombres("nada de nada", dominio).isEmpty())
    }

    @Test fun las_direcciones_de_cada_fuente_llevan_el_dominio() {
        for (f in Fuente.ELEGIBLES.filter { it.web }) assertTrue(f.nombre, f.url(dominio).startsWith("https://") && "ejemplo.com" in f.url(dominio))
        assertTrue("domain%3Aejemplo.com" in Fuente.URLSCAN.url(dominio))
        assertTrue("url=*.ejemplo.com" in Fuente.WAYBACK.url(dominio))
        assertEquals("", Fuente.LISTA.url(dominio))
    }

    @Test fun la_forma_corta_de_metodos_sigue_igual() {
        assertEquals(setOf(Fuente.CRTSH, Fuente.HACKERTARGET, Fuente.LISTA), Metodos().fuentes)
        assertEquals(setOf(Fuente.LISTA), Metodos(false, false, true).fuentes)
        assertFalse(Fuente.DOMINIO in Fuente.ELEGIBLES)
    }

    private fun buscar(bajar: (String, Int) -> String?, fuentes: Set<Fuente> = webs, estados: MutableList<String> = CopyOnWriteArrayList(),
                       resumen: MutableList<ResultadoFuente> = CopyOnWriteArrayList()): Map<String, Subdominio> {
        val b = BuscadorSubdominios(resolver = { n -> if (n.startsWith("zp-")) emptyList() else listOf("1.1.1.1") }, bajar = bajar)
        val out = CopyOnWriteArrayList<Subdominio>()
        b.buscar(dominio, Metodos(fuentes), { estados.add(it) }, { out.add(it) }, { _, _ -> }, Extras(), { resumen.addAll(it) })
        return out.associateBy { it.nombre }
    }

    @Test fun junta_los_nombres_de_todas_las_fuentes() {
        val resumen = CopyOnWriteArrayList<ResultadoFuente>()
        val por = buscar({ u, _ -> respuesta(u) }, resumen = resumen)
        assertEquals(setOf("ct.ejemplo.com", "ct2.ejemplo.com", "ht.ejemplo.com", "otx.ejemplo.com", "cs.ejemplo.com", "wild.ejemplo.com", "us.ejemplo.com",
            "an.ejemplo.com", "sc.ejemplo.com", "wb.ejemplo.com", "ejemplo.com"), por.keys)
        assertEquals("HackerTarget", por["ht.ejemplo.com"]!!.fuente)
        assertEquals("Wayback", por["wb.ejemplo.com"]!!.fuente)
        assertEquals("un nombre que traen dos fuentes dice las dos", "HackerTarget + URLScan", por["us.ejemplo.com"]!!.fuente)
        assertEquals(webs.size, resumen.size)
        assertTrue(resumen.all { it.ok && it.cantidad >= 1 })
    }

    @Test fun avisa_cual_fuente_no_respondio_y_sigue_con_las_demas() {
        val estados = CopyOnWriteArrayList<String>()
        val resumen = CopyOnWriteArrayList<ResultadoFuente>()
        val por = buscar({ u, _ -> if ("urlscan.io" in u) null else respuesta(u) }, estados = estados, resumen = resumen)
        assertTrue(estados.any { it == "URLScan no respondió" })
        assertFalse(resumen.first { it.fuente == Fuente.URLSCAN }.ok)
        assertTrue(resumen.filter { it.fuente != Fuente.URLSCAN }.all { it.ok })
        assertTrue("ct.ejemplo.com" in por && "sc.ejemplo.com" in por)
        assertEquals("el que solo traía URLScan no aparece, el que también traía HackerTarget sí", "HackerTarget", por["us.ejemplo.com"]!!.fuente)
    }

    @Test fun las_fuentes_se_consultan_a_la_vez() {
        val t0 = System.currentTimeMillis()
        buscar({ _, _ -> Thread.sleep(600); "x" })
        val ms = System.currentTimeMillis() - t0
        assertTrue("tardó $ms ms: ${webs.size} fuentes de 600 ms cada una tienen que ir en paralelo", ms < 2500)
    }

    @Test fun solo_consulta_las_fuentes_elegidas() {
        val pedidas = CopyOnWriteArrayList<String>()
        buscar({ u, _ -> pedidas.add(u); respuesta(u) }, fuentes = setOf(Fuente.ALIENVAULT, Fuente.ANUBIS))
        assertEquals(2, pedidas.size)
        assertTrue(pedidas.any { "alienvault" in it } && pedidas.any { "jldc.me" in it })
    }
}

class ProbadorHttpTest {
    private fun sub(n: String, vararg pares: Pair<String, List<Int>>) = Subdominio(n, pares.map { it.first }, "x").also { it.puertosPorIp = pares.toMap() }

    @Test fun prueba_cada_puerto_abierto_con_el_nombre_del_subdominio() {
        val a = sub("a.ejemplo.com", "1.1.1.1" to listOf(80, 443))
        val b = sub("b.ejemplo.com", "1.1.1.1" to listOf(80))
        val sinPuertos = Subdominio("c.ejemplo.com", listOf("2.2.2.2"), "x")
        val llamadas = Collections.synchronizedList(ArrayList<String>())
        val p = ProbadorHttp { n, ip, puerto ->
            llamadas.add("$n|$ip|$puerto")
            when {
                n == "a.ejemplo.com" && puerto == 80 -> Hallazgo(n, ip, 80, Hallazgo.Tipo.WEB, 301, "cloudflare → https://a.ejemplo.com/")
                n == "a.ejemplo.com" && puerto == 443 -> Hallazgo(n, ip, 443, Hallazgo.Tipo.TLS_WEB, 200, "cloudflare")
                else -> Hallazgo(n, ip, puerto, Hallazgo.Tipo.WEB, 403, "")
            }
        }
        val avances = CopyOnWriteArrayList<Pair<Int, Int>>()
        p.probar(listOf(a, b, sinPuertos), { h, t -> avances.add(h to t) }, {})
        assertEquals(listOf(80, 443), a.pruebas.map { it.puerto }.sorted())
        assertTrue(a.pruebas.all { it.ok })
        assertEquals("HTTPS 200 · cloudflare", a.pruebas.first { it.puerto == 443 }.texto)
        assertTrue(a.pruebas.first { it.puerto == 443 }.https)
        assertEquals(403, b.pruebas.single().http); assertFalse(b.pruebas.single().ok)
        assertTrue(sinPuertos.pruebas.isEmpty())
        assertEquals(3, llamadas.size)
        assertTrue("a.ejemplo.com|1.1.1.1|443" in llamadas)
        assertFalse(a.probando || b.probando || sinPuertos.probando)
        assertEquals(3 to 3, avances.last())
    }

    @Test fun un_puerto_que_ya_no_contesta_se_anota() {
        val a = sub("a.ejemplo.com", "1.1.1.1" to listOf(8080))
        ProbadorHttp { _, _, _ -> null }.probar(listOf(a), { _, _ -> }, {})
        val r = a.pruebas.single()
        assertEquals("sin respuesta", r.texto); assertNull(r.tipo); assertFalse(r.ok)
    }

    @Test fun probar_de_nuevo_borra_lo_anterior() {
        val a = sub("a.ejemplo.com", "1.1.1.1" to listOf(80))
        val p = ProbadorHttp { n, ip, puerto -> Hallazgo(n, ip, puerto, Hallazgo.Tipo.WEB, 200, "") }
        p.probar(listOf(a), { _, _ -> }, {})
        p.probar(listOf(a), { _, _ -> }, {})
        assertEquals(1, a.pruebas.size)
    }

    @Test fun no_arma_mas_pruebas_que_el_tope() {
        val a = sub("a.ejemplo.com", *(1..5).map { "1.1.1.$it" to (1..1000).toList() }.toTypedArray())
        assertEquals(ProbadorHttp.MAX_PRUEBAS, ProbadorHttp().armar(listOf(a)).size)
    }

    @Test fun detener_vuelve_enseguida_aunque_una_conexion_se_cuelgue() {
        val suelta = java.util.concurrent.CountDownLatch(1)
        val a = sub("a.ejemplo.com", "1.1.1.1" to listOf(80, 443))
        val p = ProbadorHttp { _, _, _ ->
            var listo = false
            while (!listo) { try { suelta.await(); listo = true } catch (_: InterruptedException) {} }
            null
        }
        Thread { Thread.sleep(400); p.cancelar() }.also { it.isDaemon = true }.start()
        val t0 = System.currentTimeMillis()
        p.probar(listOf(a), { _, _ -> }, {})
        val ms = System.currentTimeMillis() - t0
        suelta.countDown()
        assertTrue("tardó $ms ms", ms < 3000)
        assertFalse(a.probando)
    }
}

class EscanerProbarTest {
    /** Un servidor de mentira: contesta [respuesta] a cada conexión y la cierra. */
    private fun servidor(respuesta: String, veces: Int = 6): ServerSocket {
        val ss = ServerSocket(0, 50, java.net.InetAddress.getByName("127.0.0.1"))
        Thread {
            repeat(veces) {
                try {
                    ss.accept().use { s ->
                        s.soTimeout = 500
                        try { s.getInputStream().read(ByteArray(2048)) } catch (_: Exception) {}
                        s.getOutputStream().write(respuesta.toByteArray(Charsets.ISO_8859_1))
                        s.getOutputStream().flush()
                    }
                } catch (_: Exception) {}
            }
        }.also { it.isDaemon = true }.start()
        return ss
    }

    @Test fun un_puerto_raro_que_habla_http_se_prueba_como_web() {
        servidor("HTTP/1.1 200 OK\r\nServer: prueba\r\n\r\n").use { ss ->
            val h = Escaner().probar("sub.ejemplo.com", "127.0.0.1", ss.localPort)!!
            assertEquals(Hallazgo.Tipo.WEB, h.tipo); assertEquals(200, h.http); assertEquals("prueba", h.detalle)
        }
    }

    @Test fun un_puerto_que_no_habla_web_no_se_confunde() {
        servidor("hola\r\n").use { ss ->
            val h = Escaner().probar("sub.ejemplo.com", "127.0.0.1", ss.localPort)!!
            assertEquals(0, h.http)
            assertTrue(h.tipo != Hallazgo.Tipo.WEB && h.tipo != Hallazgo.Tipo.TLS_WEB)
        }
    }

    @Test fun un_puerto_cerrado_da_null() {
        val libre = ServerSocket(0).use { it.localPort }
        assertNull(Escaner().probar("sub.ejemplo.com", "127.0.0.1", libre))
    }

    @Test fun los_puertos_de_cloudflare_cuentan_como_web() {
        for (p in listOf(2052, 2082, 2086, 2095, 8880, 2053, 2083, 2087, 2096)) assertTrue("$p", Puertos.esWeb(p))
        for (p in listOf(2053, 2083, 2087, 2096)) assertTrue("$p", Puertos.esTls(p))
        for (p in listOf(2052, 2082, 2086, 2095, 8880)) assertFalse("$p", Puertos.esTls(p))
    }
}
