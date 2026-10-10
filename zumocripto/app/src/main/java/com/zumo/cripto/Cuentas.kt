package com.zumo.cripto

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Firmas HMAC-SHA256 que piden los exchanges para las consultas con clave. Funciones puras. */
object Firmas {
    private const val ABC = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

    fun hmac(secreto: String, texto: String): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secreto.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return mac.doFinal(texto.toByteArray(Charsets.UTF_8))
    }

    fun hex(b: ByteArray): String {
        val sb = StringBuilder(b.size * 2)
        for (x in b) { val v = x.toInt() and 255; sb.append("0123456789abcdef"[v shr 4]).append("0123456789abcdef"[v and 15]) }
        return sb.toString()
    }

    fun deHex(s: String): ByteArray {
        require(s.length % 2 == 0)
        return ByteArray(s.length / 2) { ((Character.digit(s[it * 2], 16) shl 4) or Character.digit(s[it * 2 + 1], 16)).toByte() }
    }

    fun base64(b: ByteArray): String {
        val sb = StringBuilder()
        var i = 0
        while (i < b.size) {
            val b0 = b[i].toInt() and 255
            val b1 = if (i + 1 < b.size) b[i + 1].toInt() and 255 else 0
            val b2 = if (i + 2 < b.size) b[i + 2].toInt() and 255 else 0
            val n = (b0 shl 16) or (b1 shl 8) or b2
            sb.append(ABC[(n shr 18) and 63]).append(ABC[(n shr 12) and 63])
            sb.append(if (i + 1 < b.size) ABC[(n shr 6) and 63] else '=')
            sb.append(if (i + 2 < b.size) ABC[n and 63] else '=')
            i += 3
        }
        return sb.toString()
    }

    fun hmacHex(secreto: String, texto: String): String = hex(hmac(secreto, texto))
    fun hmacBase64(secreto: String, texto: String): String = base64(hmac(secreto, texto))
}

/** La clave API de solo lectura de un exchange. [frase] solo la usa OKX. No se imprime nunca: toString no muestra los datos. */
class Credencial(val exchange: String, val clave: String, val secreto: String, val frase: String = "") {
    override fun toString(): String = "Credencial($exchange)"
}

/** Un pedido ya firmado: la URL completa y los encabezados que el exchange exige. */
class PedidoFirmado(val url: String, val cabeceras: Map<String, String>)

/** Un exchange que, con una clave de solo lectura, informa las redes de retiro y depósito de cada activo. */
class FuentePrivada(
    val nombre: String,
    val pideFrase: Boolean,
    private val firmar: (Credencial, Long) -> PedidoFirmado,
    private val leer: (String) -> Map<String, List<RedDe>>,
) {
    fun pedido(c: Credencial, ahora: Long): PedidoFirmado = firmar(c, ahora)
    fun parsear(json: String): Map<String, List<RedDe>> = try { leer(json) } catch (_: Exception) { emptyMap() }
}

/** Los exchanges que se leen con clave de solo lectura. Cada uno firma distinto; los lectores son funciones puras. */
object Privadas {
    private const val VENTANA_MS = 10000

    private fun decimal(o: JSONObject, campo: String): Double? = o.optString(campo).toDoubleOrNull()

    /** GET /sapi/v1/capital/config/getall: [] con coin y networkList[] (network, withdrawEnable, depositEnable, withdrawFee, memoRegex). */
    fun binance(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONArray(json)
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("coin").uppercase()
            val redes = m.optJSONArray("networkList") ?: continue
            if (activo.isBlank()) continue
            for (j in 0 until redes.length()) {
                val r = redes.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(r.optString("network")),
                    puedeRetirar = r.optBoolean("withdrawEnable", false),
                    puedeDepositar = r.optBoolean("depositEnable", false),
                    comision = decimal(r, "withdrawFee"),
                    requiereTag = r.optString("memoRegex").isNotBlank(),
                ))
            }
        }
        return out
    }

    /** GET /v5/asset/coin/query-info: result.rows[] con coin y chains[] (chain, withdrawFee, chainDeposit "1"/"0", chainWithdraw "1"/"0"). */
    fun bybit(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONObject(json).getJSONObject("result").getJSONArray("rows")
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("coin").uppercase()
            val redes = m.optJSONArray("chains") ?: continue
            if (activo.isBlank()) continue
            for (j in 0 until redes.length()) {
                val r = redes.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(r.optString("chain")),
                    puedeRetirar = r.optString("chainWithdraw") == "1",
                    puedeDepositar = r.optString("chainDeposit") == "1",
                    comision = decimal(r, "withdrawFee"),
                    requiereTag = false,
                ))
            }
        }
        return out
    }

    /** GET /api/v5/asset/currencies: data[] con ccy, chain ("USDT-TRC20"), canDep, canWd y minFee. La red es lo que sigue al primer guion. */
    fun okx(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONObject(json).getJSONArray("data")
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("ccy").uppercase()
            if (activo.isBlank()) continue
            val cadena = m.optString("chain")
            out.getOrPut(activo) { ArrayList() }.add(RedDe(
                red = Redes.canonica(cadena.substringAfter("-", cadena)),
                puedeRetirar = m.optBoolean("canWd", false),
                puedeDepositar = m.optBoolean("canDep", false),
                comision = decimal(m, "minFee") ?: decimal(m, "fee"),
                requiereTag = false,
            ))
        }
        return out
    }

    /** MEXC GET /api/v3/capital/config/getall: [] con coin y networkList[] (netWork/network, withdrawEnable, depositEnable, withdrawFee, sameAddress = lleva memo). */
    fun mexc(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONArray(json)
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("coin").uppercase()
            val redes = m.optJSONArray("networkList") ?: continue
            if (activo.isBlank()) continue
            for (j in 0 until redes.length()) {
                val r = redes.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(r.optString("netWork").ifBlank { r.optString("network") }),
                    puedeRetirar = r.optBoolean("withdrawEnable", false),
                    puedeDepositar = r.optBoolean("depositEnable", false),
                    comision = decimal(r, "withdrawFee"),
                    requiereTag = r.optBoolean("sameAddress", false),
                ))
            }
        }
        return out
    }

    /** BingX GET /openApi/wallets/v1/capital/config/getall: data[] con coin y networkList[] (network, withdrawEnable, depositEnable, withdrawFee). */
    fun bingx(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONObject(json).getJSONArray("data")
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("coin").uppercase()
            val redes = m.optJSONArray("networkList") ?: continue
            if (activo.isBlank()) continue
            for (j in 0 until redes.length()) {
                val r = redes.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(r.optString("network").ifBlank { r.optString("name") }),
                    puedeRetirar = r.optBoolean("withdrawEnable", false),
                    puedeDepositar = r.optBoolean("depositEnable", false),
                    comision = decimal(r, "withdrawFee"),
                    requiereTag = false,
                ))
            }
        }
        return out
    }

    fun horaOkx(ms: Long): String {
        val f = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        f.timeZone = TimeZone.getTimeZone("UTC")
        return f.format(Date(ms))
    }

    val BINANCE = FuentePrivada("Binance", false, { c, ahora ->
        val q = "timestamp=$ahora&recvWindow=$VENTANA_MS"
        PedidoFirmado(
            "https://api.binance.com/sapi/v1/capital/config/getall?$q&signature=${Firmas.hmacHex(c.secreto, q)}",
            mapOf("X-MBX-APIKEY" to c.clave),
        )
    }) { binance(it) }

    val BYBIT = FuentePrivada("Bybit", false, { c, ahora ->
        val firma = Firmas.hmacHex(c.secreto, "$ahora${c.clave}$VENTANA_MS")
        PedidoFirmado(
            "https://api.bybit.com/v5/asset/coin/query-info",
            mapOf(
                "X-BAPI-API-KEY" to c.clave, "X-BAPI-TIMESTAMP" to ahora.toString(),
                "X-BAPI-RECV-WINDOW" to VENTANA_MS.toString(), "X-BAPI-SIGN" to firma,
            ),
        )
    }) { bybit(it) }

    val OKX = FuentePrivada("OKX", true, { c, ahora ->
        val hora = horaOkx(ahora)
        val ruta = "/api/v5/asset/currencies"
        PedidoFirmado(
            "https://www.okx.com$ruta",
            mapOf(
                "OK-ACCESS-KEY" to c.clave, "OK-ACCESS-SIGN" to Firmas.hmacBase64(c.secreto, "${hora}GET$ruta"),
                "OK-ACCESS-TIMESTAMP" to hora, "OK-ACCESS-PASSPHRASE" to c.frase,
            ),
        )
    }) { okx(it) }

    val MEXC = FuentePrivada("MEXC", false, { c, ahora ->
        val q = "timestamp=$ahora&recvWindow=$VENTANA_MS"
        PedidoFirmado(
            "https://api.mexc.com/api/v3/capital/config/getall?$q&signature=${Firmas.hmacHex(c.secreto, q)}",
            mapOf("X-MEXC-APIKEY" to c.clave),
        )
    }) { mexc(it) }

    val BINGX = FuentePrivada("BingX", false, { c, ahora ->
        val q = "recvWindow=$VENTANA_MS&timestamp=$ahora"
        PedidoFirmado(
            "https://open-api.bingx.com/openApi/wallets/v1/capital/config/getall?$q&signature=${Firmas.hmacHex(c.secreto, q)}",
            mapOf("X-BX-APIKEY" to c.clave),
        )
    }) { bingx(it) }

    val FUENTES: List<FuentePrivada> = listOf(BINANCE, BYBIT, OKX, MEXC, BINGX)
}
