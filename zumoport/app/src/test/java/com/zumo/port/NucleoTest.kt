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
