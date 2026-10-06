package com.zumo.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class TemaTest {

    /** El tema del repo (lo escribe bot/tema.py) es exactamente el que la app trae por defecto. */
    @Test
    fun el_archivo_del_repo_es_el_tema_por_defecto() {
        val texto = File("../marca/tema.json").readText(Charsets.UTF_8)
        assertEquals(Tema(), Tema.desdeJson(texto))
    }

    @Test
    fun lee_un_tema_personalizado() {
        val t = Tema.desdeJson(
            """{"nombre":"NetFree","lema":"","logo":"🚀","logo_imagen":true,"titulo_mayus":false,
               "fondo":"#071A2C","fondo2":"#0b3b5a","velo":70,"tarjeta":"#FFFFFF","texto":"#101216",
               "acento":"#4FC3F7","opacidad":70,"radio":8,"fuente":"serif","escala":112,
               "ver_vencimiento":false,"ver_conexion":false,"ver_telefono":true,"ver_importar":false,
               "enlaces":[{"texto":"Soporte","url":"https://wa.me/5491100000000"},
                          {"texto":"Sin link","url":""},{"texto":"Raro","url":"javascript:alert(1)"},
                          {"texto":"Canal","url":"tg://resolve?domain=zumo"}]}"""
        )
        assertEquals("NetFree", t.nombre)
        assertEquals("NetFree", t.titulo)
        assertEquals("", t.lema)
        assertEquals("🚀", t.logo)
        assertTrue(t.logoImagen)
        assertEquals(0xFF071A2C.toInt(), t.fondo)
        assertEquals(0xFF0B3B5A.toInt(), t.fondo2)
        assertEquals(0xFFFFFFFF.toInt(), t.tarjeta)
        assertEquals(0xFF4FC3F7.toInt(), t.acento)
        assertEquals(listOf(70, 70, 8, 112), listOf(t.velo, t.opacidad, t.radio, t.escala))
        assertEquals("serif", t.fuente)
        assertFalse(t.verVencimiento); assertFalse(t.verConexion); assertTrue(t.verTelefono); assertFalse(t.verImportar)
        // solo quedan los botones con texto y un enlace que se puede abrir
        assertEquals(listOf(Enlace("Soporte", "https://wa.me/5491100000000"), Enlace("Canal", "tg://resolve?domain=zumo")), t.enlaces)
        // lo que el tema no nombra queda como siempre
        assertEquals(Tema().conectar, t.conectar)
    }

    @Test
    fun un_tema_roto_no_rompe_la_app() {
        assertEquals(Tema(), Tema.desdeJson("esto no es json"))
        assertEquals(Tema(), Tema.desdeJson(""))
        val t = Tema.desdeJson("""{"nombre":"   ","fondo":"rojo","fondo2":"#12","radio":999,"escala":-5,"opacidad":0,"fuente":"comic","enlaces":"no"}""")
        assertEquals("Zumo VPN", t.nombre)
        assertEquals("ZUMO VPN", t.titulo)
        assertEquals(Tema().fondo, t.fondo)
        assertNull(t.fondo2)
        assertEquals(listOf(32, 85, 40), listOf(t.radio, t.escala, t.opacidad))
        assertEquals("sans-serif", t.fuente)
        assertTrue(t.enlaces.isEmpty())
    }

    @Test
    fun colores_claros_y_oscuros() {
        assertEquals(0xFFB388FF.toInt(), Tema.color("#b388ff"))
        assertEquals(0xFFB388FF.toInt(), Tema.color("B388FF"))
        assertNull(Tema.color("#FFF"))
        assertNull(Tema.color("#GGGGGG"))
        assertNull(Tema.color(null))
        assertTrue(Tema.esClaro(0xFFF3F5FA.toInt()))
        assertFalse(Tema.esClaro(0xFF14102B.toInt()))
    }
}
