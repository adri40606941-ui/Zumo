package com.zumo.vpn

import org.apache.sshd.common.forward.DefaultForwarderFactory
import org.apache.sshd.server.SshServer
import org.apache.sshd.server.forward.AcceptAllForwardingFilter
import org.apache.sshd.server.keyprovider.SimpleGeneratorHostKeyProvider
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.nio.file.Files

/** El ID viaja por un canal interno de un SSH real (Apache MINA) hacia un "zumo-id" de mentira. */
class DispositivoTest {
    private lateinit var sshd: SshServer
    private var daemon: ServerSocket? = null
    @Volatile private var recibido = ""

    @Before
    fun arrancar() {
        sshd = SshServer.setUpDefaultServer()
        sshd.port = 0
        sshd.keyPairProvider = SimpleGeneratorHostKeyProvider(Files.createTempFile("hk", ".ser"))
        sshd.setPasswordAuthenticator { u, p, _ -> u == "cliente" && p == "clave1" }
        sshd.forwardingFilter = AcceptAllForwardingFilter.INSTANCE
        sshd.forwarderFactory = DefaultForwarderFactory.INSTANCE
        sshd.start()
    }

    @After
    fun parar() {
        try { sshd.stop(true) } catch (_: Exception) {}
        try { daemon?.close() } catch (_: Exception) {}
    }

    /** Servicio de mentira: lee la línea del cliente y contesta [respuesta] (null = no contesta). */
    private fun servicio(respuesta: String?): Int {
        val ss = ServerSocket(0, 10, InetAddress.getByName("127.0.0.1"))
        daemon = ss
        Thread {
            try {
                val c = ss.accept()
                val i = c.getInputStream()
                val sb = StringBuilder()
                while (true) { val b = i.read(); if (b < 0 || b == '\n'.code) break; sb.append(b.toChar()) }
                recibido = sb.toString()
                if (respuesta != null) { c.getOutputStream().write(respuesta.toByteArray()); c.getOutputStream().flush() }
                else Thread.sleep(3000)
                c.close()
            } catch (_: Exception) {}
        }.start()
        return ss.localPort
    }

    private fun conectar(): SshTunnel =
        SshTunnel(Config(host = "localhost", sshPort = sshd.port), "cliente", "clave1").also { it.connect() }

    @Test
    fun el_servicio_acepta_y_recibe_el_id() {
        val puerto = servicio("OK\n")
        val t = conectar()
        try {
            assertEquals(Dispositivo.Resultado.OK, Dispositivo.verificar(t, "a1b2c3d4e5f60789", puerto))
            assertEquals("ZID1 a1b2c3d4e5f60789", recibido)
            assertTrue("el túnel sigue conectado", t.conectado)
        } finally { t.close() }
    }

    @Test
    fun el_servicio_rechaza_otro_celular() {
        val puerto = servicio("DENY\n")
        val t = conectar()
        try {
            assertEquals(Dispositivo.Resultado.RECHAZADO, Dispositivo.verificar(t, "a1b2c3d4e5f60789", puerto))
        } finally { t.close() }
    }

    @Test
    fun servidor_sin_el_servicio_no_corta_a_nadie() {
        val libre = ServerSocket(0).use { it.localPort }   // puerto cerrado: el canal no se puede abrir
        val t = conectar()
        try {
            assertEquals(Dispositivo.Resultado.SIN_SERVICIO, Dispositivo.verificar(t, "a1b2c3d4e5f60789", libre))
            assertTrue("el túnel sigue conectado", t.conectado)
        } finally { t.close() }
    }

    @Test
    fun servicio_que_no_contesta_no_corta_a_nadie() {
        val puerto = servicio(null)
        val t = conectar()
        try {
            assertEquals(Dispositivo.Resultado.SIN_SERVICIO, Dispositivo.verificar(t, "a1b2c3d4e5f60789", puerto, esperaMs = 700))
        } finally { t.close() }
    }

    @Test
    fun sin_id_usable_no_se_manda_nada() {
        val puerto = servicio("OK\n")
        val t = conectar()
        try {
            assertEquals(Dispositivo.Resultado.SIN_SERVICIO, Dispositivo.verificar(t, "", puerto))
            assertEquals("", recibido)
        } finally { t.close() }
    }

    @Test
    fun formato_del_id_y_de_las_respuestas() {
        assertEquals("a1b2c3d4e5f60789", Dispositivo.normalizar(" A1B2C3D4E5F60789 "))
        assertEquals("", Dispositivo.normalizar(null))
        assertEquals("", Dispositivo.normalizar("no-es-hex"))
        assertEquals("", Dispositivo.normalizar("abc"))   // muy corto
        assertEquals("ZID1 a1b2c3d4e5f60789\n", Dispositivo.mensaje("a1b2c3d4e5f60789"))
        assertEquals(Dispositivo.Resultado.OK, Dispositivo.interpretar("OK\r"))
        assertEquals(Dispositivo.Resultado.RECHAZADO, Dispositivo.interpretar("DENY"))
        assertEquals(Dispositivo.Resultado.SIN_SERVICIO, Dispositivo.interpretar("algo raro"))
        assertEquals(Dispositivo.Resultado.SIN_SERVICIO, Dispositivo.interpretar(null))
        assertTrue(Dispositivo.textoRechazo("a1b2c3d4e5f60789").contains("a1b2c3d4e5f60789"))
    }
}
