package com.zumo.vpn

import android.content.Context
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Servidores que ve el cliente. Hay dos fuentes:
 *  - la que viene dentro del APK (assets/servidores.bin), horneada al compilar;
 *  - la que la app baja de internet con el botón ↻ (se guarda en servidores-online.bin).
 *
 * Si hay una lista bajada y válida, se usa esa; si no, la del APK. Así, cuando cambiás un
 * servidor o un payload en el bot, el cliente toca ↻ y lo tiene al instante, sin reinstalar.
 *
 * Las dos van cifradas con el mismo formato (ver Zs.descifrarLista) y el mismo SECRETO. La URL de
 * descarga la hornea Gradle en assets/actualizar.url (sale del centro al compilar).
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
    private const val ASSET_URL = "actualizar.url"
    private const val ARCHIVO_ONLINE = "servidores-online.bin"
    private const val MAX_BYTES = 512 * 1024      // una lista de servidores nunca pesa tanto

    enum class Estado { OK, SIN_INTERNET, SIN_URL, VACIA, ERROR }
    data class Resultado(val estado: Estado, val cantidad: Int = 0)

    @Volatile private var cache: List<Config>? = null

    fun lista(ctx: Context): List<Config> {
        cache?.let { return it }
        val l = leerOnline(ctx) ?: leerAsset(ctx) ?: emptyList()
        cache = l
        return l
    }

    private fun leerAsset(ctx: Context): List<Config>? = try {
        val datos = ctx.applicationContext.assets.open(ASSET).use { it.readBytes() }
        Zs.descifrarLista(datos)?.let { parsear(it) }
    } catch (e: Exception) {
        null
    }

    private fun archivoOnline(ctx: Context) = File(ctx.applicationContext.filesDir, ARCHIVO_ONLINE)

    /** Lista bajada de internet, o null si no hay, no se puede abrir o quedó vacía. */
    private fun leerOnline(ctx: Context): List<Config>? {
        val f = archivoOnline(ctx)
        if (!f.isFile) return null
        return try {
            val l = Zs.descifrarLista(f.readBytes())?.let { parsear(it) }
            if (l.isNullOrEmpty()) null else l
        } catch (e: Exception) {
            null
        }
    }

    /** true si el cliente ya bajó una lista alguna vez (para mostrar "actualizada" en la pantalla). */
    fun hayOnline(ctx: Context): Boolean = archivoOnline(ctx).isFile

    /** La URL de descarga que horneó el centro al compilar; vacía si la app se compiló sin centro. */
    fun urlActualizar(ctx: Context): String = try {
        ctx.applicationContext.assets.open(ASSET_URL).use { it.readBytes() }
            .toString(Charsets.UTF_8).trim()
    } catch (e: Exception) {
        ""
    }

    /**
     * Servidores que se prueban con el token, en orden: el último donde se entró primero y el resto como vienen
     * en la lista. Si la app no trae lista, queda solo la cuenta guardada (si sirve).
     */
    fun candidatos(lista: List<Config>, ultimo: String, guardada: Config?): List<Config> {
        if (lista.isEmpty()) return listOfNotNull(guardada?.takeIf { it.valida() })
        val validos = lista.filter { it.valida() }
        // un servidor con varios hosts son varios candidatos con el mismo nombre, uno por host
        val expandidos = validos.flatMap { c -> c.hosts().map { c.con(it) } }
        val primeros = expandidos.filter { it.name == ultimo }
        if (primeros.isEmpty()) return expandidos
        return primeros + expandidos.filter { it.name != ultimo }
    }

    fun buscar(ctx: Context, nombre: String): Config? =
        if (nombre.isBlank()) null else lista(ctx).firstOrNull { it.name == nombre }

    /**
     * Si la cuenta usa un servidor de la lista, vuelve a tomar sus datos de la app. Así, cuando se
     * actualiza la lista (bajada o reinstalada), se usa el nuevo sin que el cliente toque nada.
     */
    fun refrescar(ctx: Context, prefs: Prefs) {
        val s = buscar(ctx, prefs.servidor) ?: return
        if (s != prefs.config) prefs.config = s
    }

    /**
     * Baja la lista de internet y la guarda. No corre en el hilo principal (hace red).
     * Nunca rompe la lista que ya hay: si lo que baja no sirve, se deja la anterior.
     */
    fun descargar(ctx: Context): Resultado {
        val urls = separarUrls(urlActualizar(ctx))
        if (urls.isEmpty()) return Resultado(Estado.SIN_URL)
        // Se prueban en orden (la VPS primero, GitHub de respaldo); la primera que sirva, gana.
        var ultimo = Resultado(Estado.SIN_INTERNET)
        for (u in urls) {
            ultimo = descargarDe(ctx, u, if (urls.size > 1) 6000 else 10000)
            if (ultimo.estado == Estado.OK) return ultimo
        }
        return ultimo
    }

    /** Las direcciones que horneó el centro: separadas por "|", espacios o saltos de línea. Solo http(s). */
    internal fun separarUrls(texto: String): List<String> =
        texto.split('|', '\n', '\r', ' ', '\t').map { it.trim() }.filter { it.startsWith("http://") || it.startsWith("https://") }

    private fun descargarDe(ctx: Context, url: String, timeoutMs: Int): Resultado {
        val datos = try {
            val c = (URL(url).openConnection() as HttpURLConnection).apply {
                connectTimeout = timeoutMs; readTimeout = timeoutMs; useCaches = false
                setRequestProperty("User-Agent", "ZumoVPN")
            }
            try {
                if (c.responseCode != 200) return Resultado(Estado.ERROR)
                c.inputStream.use { it.readBytes(MAX_BYTES) }
            } finally {
                c.disconnect()
            }
        } catch (e: Exception) {
            return Resultado(Estado.SIN_INTERNET)
        }
        val l = Zs.descifrarLista(datos)?.let { parsear(it) }
        if (l.isNullOrEmpty()) return Resultado(Estado.VACIA)
        return try {
            val f = archivoOnline(ctx)
            val tmp = File(f.parentFile, "$ARCHIVO_ONLINE.tmp")
            tmp.writeBytes(datos)
            if (!tmp.renameTo(f)) { tmp.copyTo(f, overwrite = true); tmp.delete() }
            cache = l
            Resultado(Estado.OK, l.size)
        } catch (e: Exception) {
            Resultado(Estado.ERROR)
        }
    }

    private fun java.io.InputStream.readBytes(max: Int): ByteArray {
        val buf = java.io.ByteArrayOutputStream()
        val b = ByteArray(8192)
        var total = 0
        while (true) {
            val n = read(b)
            if (n < 0) break
            total += n
            if (total > max) throw java.io.IOException("lista demasiado grande")
            buf.write(b, 0, n)
        }
        return buf.toByteArray()
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
