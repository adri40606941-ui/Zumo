package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar

class ZsTest {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    @Test
    fun abre_el_archivo_que_arma_el_bot_de_python() {
        val blob = hex(javaClass.getResource("/vector.zs.hex")!!.readText().trim())
        val p = Zs.descifrar(blob, "secreto-de-prueba")
        assertNotNull(p)
        assertEquals("vps.ejemplo.com", p!!.cfg.host)
        assertEquals(80, p.cfg.sshPort)
        assertEquals("Mi VPN", p.cfg.name)
        assertEquals("GET / HTTP/1.1[crlf]Host: x.net[crlf][crlf]", p.cfg.payload)
        assertEquals("cliente1", p.user)
        assertEquals("Clave1", p.pass)
        assertEquals("2026-11-05", p.exp)
    }

    @Test
    fun ida_y_vuelta_y_rechazos() {
        val p = Perfil(Config(name = "N", host = "h.com", sshPort = 443, payload = "x", tls = true, sni = "s"), "u", "c", "2030-01-02")
        val blob = Zs.cifrar(p)
        assertEquals(p, Zs.descifrar(blob))
        assertNull("otro secreto", Zs.descifrar(blob, "otro"))
        assertNull("texto cualquiera", Zs.descifrar("hola".toByteArray()))
        assertNull("json plano", Zs.descifrar(p.toJson().toString().toByteArray()))
        blob[blob.size - 1] = (blob[blob.size - 1].toInt() xor 1).toByte()
        assertNull("alterado", Zs.descifrar(blob))
    }

    private fun ms(y: Int, m: Int, d: Int, h: Int) = Calendar.getInstance().apply { clear(); set(y, m - 1, d, h, 0, 0) }.timeInMillis

    @Test
    fun vencimiento_corta_el_dia_a_las_21() {
        assertFalse(Perfil.vencida("2026-11-05", ms(2026, 11, 5, 20)))
        assertTrue(Perfil.vencida("2026-11-05", ms(2026, 11, 5, 21)))
        assertTrue(Perfil.vencida("2026-11-05", ms(2026, 11, 6, 1)))
        assertFalse(Perfil.vencida("", ms(2030, 1, 1, 1)))
        assertEquals(0, Perfil.diasRestantes("2026-11-05", ms(2026, 11, 5, 9)))
        assertEquals(1, Perfil.diasRestantes("2026-11-05", ms(2026, 11, 4, 23)))
        assertEquals(31, Perfil.diasRestantes("2026-11-05", ms(2026, 10, 5, 12)))
        assertNull(Perfil.diasRestantes("basura"))
        assertEquals("05/11/2026", Perfil.fechaLinda("2026-11-05"))
    }

    @Test
    fun el_registro_no_repite_lineas_iguales() {
        Registro.limpiar()
        Registro.add("Conectando al servidor"); Registro.add("Conectando al servidor"); Registro.add("✔ Conectado")
        assertEquals(2, Registro.texto().lines().size)
    }
}
