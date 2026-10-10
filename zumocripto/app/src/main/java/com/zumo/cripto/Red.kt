package com.zumo.cripto

import java.net.HttpURLConnection
import java.net.URL

/** Lo que vuelve de un pedido HTTP: el código de estado y el cuerpo (null si no se pudo leer o el código no es 2xx). */
class ResultadoPedido(val codigo: Int, val cuerpo: String?)

/** El pedido HTTP de verdad. Las APIs de precios son públicas; las consultas con tu cuenta llevan los encabezados firmados que pide cada exchange. */
object Red {
    fun pedirFirmado(p: PedidoFirmado): ResultadoPedido = pedir(p.url, p.cabeceras)

    fun pedir(url: String, cabeceras: Map<String, String> = emptyMap()): ResultadoPedido {
        var con: HttpURLConnection? = null
        return try {
            con = (URL(url).openConnection() as HttpURLConnection).also {
                it.connectTimeout = 10000
                it.readTimeout = 20000
                it.setRequestProperty("Accept", "application/json")
                it.setRequestProperty("User-Agent", "ZumoCripto/1.0")
                for ((k, v) in cabeceras) it.setRequestProperty(k, v)
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
