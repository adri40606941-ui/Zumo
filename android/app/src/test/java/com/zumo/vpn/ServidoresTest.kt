package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServidoresTest {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    @Test
    fun abre_la_lista_que_cifra_gradle_al_compilar() {
        // vector.lista.hex = salida de la tarea cifrarServidores con el secreto "secreto-de-prueba"
        val blob = hex(javaClass.getResource("/vector.lista.hex")!!.readText().trim())
        val texto = Zs.descifrarLista(blob, "secreto-de-prueba")
        val l = Servidores.parsear(texto!!)
        assertEquals(listOf(Config("Mi VPN", "vps.ejemplo.com", 80, "GET / HTTP/1.1[crlf]Host: x.net[crlf][crlf]")), l)
        assertNull("otro secreto", Zs.descifrarLista(blob, "otro"))
        assertNull("un .zs no es una lista", Zs.descifrarLista("ZS1".toByteArray() + ByteArray(40)))
        blob[blob.size - 1] = (blob[blob.size - 1].toInt() xor 1).toByte()
        assertNull("alterado", Zs.descifrarLista(blob, "secreto-de-prueba"))
    }

    @Test
    fun lee_varios_servidores_cada_uno_con_su_payload() {
        val l = Servidores.parsear(
            """
            # comentario
            host = suelto.com

            [APP 02]
              host = d111.cloudfront.net
            puerto = 80
            payload = GET / HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf][crlf]
            tls = no
            sni =

            [APP 05]
            HOST = https://otro.ejemplo.com/
            TLS = Si
            sni = cdn.x.com
            payload = GET / HTTP/1.1[crlf][crlf]

            [APP 02]
            host = 1.2.3.4:8080
            # [Comentado]
            [Roto sin host]
            puerto = 80
            payload = x
            [Con dos puntos]
            host: a.b.c
            puerto: 22
            payload: CONNECT [host_port] HTTP/1.1[crlf]X: a=b[crlf][crlf][split]GET / HTTP/1.1[crlf][crlf]
            """.trimIndent()
        )
        assertEquals(listOf("APP 02", "APP 05", "APP 02 (2)", "Con dos puntos"), l.map { it.name })
        assertEquals(Config("APP 02", "d111.cloudfront.net", 80, "GET / HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf][crlf]"), l[0])
        // tls sin puerto usa el 443; el host se limpia igual que en un .zs
        assertEquals(Config("APP 05", "otro.ejemplo.com", 443, "GET / HTTP/1.1[crlf][crlf]", true, "cdn.x.com"), l[1])
        assertEquals("1.2.3.4", l[2].host); assertEquals(8080, l[2].sshPort)
        assertEquals("CONNECT [host_port] HTTP/1.1[crlf]X: a=b[crlf][crlf][split]GET / HTTP/1.1[crlf][crlf]", l[3].payload)
        assertEquals(22, l[3].sshPort)
    }

    @Test
    fun sin_servidores_la_lista_queda_vacia() {
        assertTrue(Servidores.parsear("").isEmpty())
        assertTrue(Servidores.parsear("# solo comentarios\n#  [APP 02]\n#  host = x.com\n").isEmpty())
        assertTrue("sin [Nombre] no es un servidor", Servidores.parsear("host = x.com\npayload = y").isEmpty())
    }
}
