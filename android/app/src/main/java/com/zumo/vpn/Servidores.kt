package com.zumo.vpn

import android.content.Context

/**
 * Servidores que vienen dentro de la app. Salen de android/servidores.txt, que al compilar se
 * guarda cifrado en assets/servidores.bin (ver Zs.descifrarLista). El cliente elige uno de la
 * lista y solo pone su usuario y contraseña: el host y el payload no se ven en pantalla.
 *
 * Formato del texto (un bloque por servidor; las líneas que empiezan con # no cuentan):
 *
 *   [Nombre que ve el cliente]
 *   host = dominio.o.ip
 *   puerto = 80
 *   payload = GET / HTTP/1.1[crlf]Host: [host][crlf]Upgrade: websocket[crlf][crlf]
 *   tls = no
 *   sni =
 */
object Servidores {
    private const val ASSET = "servidores.bin"

    @Volatile private var cache: List<Config>? = null

    fun lista(ctx: Context): List<Config> {
        cache?.let { return it }
        val l = try {
            val datos = ctx.applicationContext.assets.open(ASSET).use { it.readBytes() }
            Zs.descifrarLista(datos)?.let { parsear(it) } ?: emptyList()
        } catch (e: Exception) {
            emptyList()
        }
        cache = l
        return l
    }

    fun buscar(ctx: Context, nombre: String): Config? =
        if (nombre.isBlank()) null else lista(ctx).firstOrNull { it.name == nombre }

    /**
     * Si la cuenta usa un servidor de la lista, vuelve a tomar sus datos de la app. Así, cuando se
     * instala una versión con el payload cambiado, se usa el nuevo sin que el cliente toque nada.
     */
    fun refrescar(ctx: Context, prefs: Prefs) {
        val s = buscar(ctx, prefs.servidor) ?: return
        if (s != prefs.config) prefs.config = s
    }

    private val SI = setOf("si", "sí", "s", "yes", "y", "true", "1", "on")

    /** Lee el texto de servidores.txt. Los bloques sin host válido se saltean. */
    fun parsear(texto: String): List<Config> {
        val out = ArrayList<Config>()
        var nombre: String? = null
        var host = ""
        var puerto: Int? = null
        var payload = ""
        var tls = false
        var sni = ""

        fun cerrar() {
            val n = nombre ?: return
            nombre = null
            val c = Config(name = n, host = host, sshPort = puerto ?: if (tls) 443 else 80, payload = payload, tls = tls, sni = sni).limpiar()
            if (!c.valida()) return
            // dos servidores con el mismo nombre: el segundo queda como "Nombre (2)"
            var unico = c.name
            var k = 2
            while (out.any { it.name == unico }) unico = "${c.name} (${k++})"
            out.add(c.copy(name = unico))
        }

        for (cruda in texto.removePrefix("﻿").lines()) {
            val linea = cruda.trim()
            if (linea.isEmpty() || linea.startsWith("#")) continue
            // [Nombre]: empieza un servidor nuevo (un payload nunca va solo en una línea, siempre después de "payload =")
            if (linea.startsWith("[") && linea.endsWith("]") && linea.length > 2) {
                val dentro = linea.substring(1, linea.length - 1).trim()
                if (dentro.isNotEmpty() && dentro.none { it == '[' || it == ']' || it == '=' }) {
                    cerrar()
                    nombre = dentro
                    host = ""; puerto = null; payload = ""; tls = false; sni = ""
                    continue
                }
            }
            if (nombre == null) continue
            val corte = linea.indexOfFirst { it == '=' || it == ':' }
            if (corte <= 0) continue
            val clave = linea.substring(0, corte).trim().lowercase()
            val valor = linea.substring(corte + 1).trim()
            when (clave) {
                "host", "servidor", "dominio", "ip" -> host = valor
                "puerto", "port" -> puerto = valor.toIntOrNull()
                "payload" -> payload = valor
                "tls", "ssl" -> tls = valor.lowercase() in SI
                "sni" -> sni = valor
            }
        }
        cerrar()
        return out
    }
}
