package com.zumo.cripto

import java.net.HttpURLConnection
import java.net.URL

/** Lo que vuelve de un pedido HTTP: el código de estado y el cuerpo (null si no se pudo leer o el código no es 2xx). */
class ResultadoPedido(val codigo: Int, val cuerpo: String?)

/** El pedido HTTP de verdad. Las APIs de los exchanges son públicas: no hace falta ninguna clave. */
object Red {
    fun pedir(url: String): ResultadoPedido {
        var con: HttpURLConnection? = null
        return try {
            con = (URL(url).openConnection() as HttpURLConnection).also {
                it.connectTimeout = 10000
                it.readTimeout = 20000
                it.setRequestProperty("Accept", "application/json")
                it.setRequestProperty("User-Agent", "ZumoCripto/1.0")
            }
            val codigo = con.responseCode
            val flujo = if (codigo in 200..299) con.inputStream else con.errorStream
            val cuerpo = flujo?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
            ResultadoPedido(codigo, if (codigo in 200..299) cuerpo else null)
        } catch (_: Exception) {
            ResultadoPedido(0, null)
        } finally {
            con?.disconnect()
        }
    }
}
