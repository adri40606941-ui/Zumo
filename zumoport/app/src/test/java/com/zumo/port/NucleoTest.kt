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
