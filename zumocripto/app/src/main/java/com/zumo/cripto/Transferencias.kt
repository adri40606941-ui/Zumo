package com.zumo.cripto

import org.json.JSONArray
import org.json.JSONObject

/** Una red por la que un exchange deja retirar y/o recibir un activo. [comision] está en el propio activo (null si el exchange no la informa). */
data class RedDe(
    val red: String,
    val puedeRetirar: Boolean,
    val puedeDepositar: Boolean,
    val comision: Double?,
    val requiereTag: Boolean,
)

/** Un exchange que publica sin clave qué redes tiene cada activo. [leer] devuelve activo → redes. */
class FuenteTransferencias(val nombre: String, val url: String, private val leer: (String) -> Map<String, List<RedDe>>) {
    fun parsear(json: String): Map<String, List<RedDe>> = try { leer(json) } catch (_: Exception) { emptyMap() }
}

/** Nombres de red distintos según el exchange ("ERC20", "ETH", "Ethereum") que son la misma red. */
object Redes {
    private val alias = mapOf(
        "ETH" to "ETH", "ERC20" to "ETH", "ETHEREUM" to "ETH",
        "TRX" to "TRX", "TRC20" to "TRX", "TRON" to "TRX",
        "BSC" to "BSC", "BEP20" to "BSC", "BNBSMARTCHAIN" to "BSC",
        "ARBITRUMONE" to "ARB", "ARBITRUM" to "ARB", "ARBEVM" to "ARB", "ARB" to "ARB",
        "OPTIMISM" to "OP", "OP" to "OP",
        "POLYGON" to "MATIC", "MATIC" to "MATIC",
        "SOL" to "SOL", "SOLANA" to "SOL",
        "AVAXC" to "AVAX", "AVAX" to "AVAX",
        "BTC" to "BTC", "BITCOIN" to "BTC",
    )

    /** Nombre común de una red para comparar entre exchanges. Sin coincidencia, devuelve el nombre sin símbolos. */
    fun canonica(nombre: String): String {
        val clave = nombre.uppercase().filter { it.isLetterOrDigit() }
        return alias[clave] ?: clave
    }
}

/** Lo que se sabe de cada exchange sobre envíos. Un exchange sin datos no se puede verificar. */
class Catalogo(private val datos: Map<String, Map<String, List<RedDe>>>) {
    sealed class Estado {
        /** Se puede comprar en [desde] y enviar a [hasta] por [red]; [comision] en el activo, si se conoce. */
        class Posible(val red: String, val comision: Double?, val requiereTag: Boolean) : Estado()
        class NoPosible(val motivo: String) : Estado()
        class NoVerificable(val motivo: String) : Estado()
    }

    fun estado(activo: String, desde: String, hasta: String): Estado {
        val a = datos[desde]
        val b = datos[hasta]
        if (a == null || b == null) {
            val sinDatos = listOfNotNull(if (a == null) desde else null, if (b == null) hasta else null)
            return Estado.NoVerificable("sin datos públicos de ${sinDatos.joinToString(" ni ")} (necesitan clave)")
        }
        val enDesde = a[activo] ?: return Estado.NoPosible("$activo no se opera para retiro en $desde")
        val enHasta = b[activo] ?: return Estado.NoPosible("$activo no está en $hasta")
        val comunes = enDesde.filter { r ->
            r.puedeRetirar && enHasta.any { h -> h.red == r.red && h.puedeDepositar }
        }
        if (comunes.isEmpty()) return Estado.NoPosible("no hay una red en común: $desde no retira o $hasta no recibe")
        val mejor = comunes.minByOrNull { it.comision ?: Double.MAX_VALUE }!!
        return Estado.Posible(mejor.red, mejor.comision, mejor.requiereTag)
    }
}

/** Los tres exchanges que publican envíos sin clave, y el lector de cada uno. Funciones puras. */
object Transferencias {
    private fun comisionDe(o: JSONObject, campo: String): Double? = o.optString(campo).toDoubleOrNull()

    /** GET /api/v3/currencies: data[] con currency y chains[] (chainName, isWithdrawEnabled, isDepositEnabled, withdrawalMinFee). */
    fun kucoin(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONObject(json).getJSONArray("data")
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("currency").uppercase()
            if (activo.isBlank()) continue
            val cadenas = m.optJSONArray("chains") ?: continue
            for (j in 0 until cadenas.length()) {
                val c = cadenas.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(c.optString("chainName")),
                    puedeRetirar = c.optBoolean("isWithdrawEnabled", false),
                    puedeDepositar = c.optBoolean("isDepositEnabled", false),
                    comision = comisionDe(c, "withdrawalMinFee"),
                    requiereTag = c.optString("memoRegex").isNotBlank() || c.optBoolean("needTag", false),
                ))
            }
        }
        return out
    }

    /** GET /api/v4/spot/currencies: [] con currency, delisted y chains[] (name, withdraw_disabled, deposit_disabled). Gate no informa comisión ni memo. */
    fun gate(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONArray(json)
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            if (m.optBoolean("delisted", false)) continue
            val activo = m.optString("currency").uppercase()
            if (activo.isBlank()) continue
            val cadenas = m.optJSONArray("chains") ?: continue
            for (j in 0 until cadenas.length()) {
                val c = cadenas.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(c.optString("name")),
                    puedeRetirar = !c.optBoolean("withdraw_disabled", true),
                    puedeDepositar = !c.optBoolean("deposit_disabled", true),
                    comision = null,
                    requiereTag = false,
                ))
            }
        }
        return out
    }

    /** GET /api/v2/spot/public/coins: data[] con coin y chains[] (chain, withdrawable, rechargeable, withdrawFee, needTag). Valores como texto. */
    fun bitget(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONObject(json).getJSONArray("data")
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("coin").uppercase()
            if (activo.isBlank()) continue
            val cadenas = m.optJSONArray("chains") ?: continue
            for (j in 0 until cadenas.length()) {
                val c = cadenas.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(c.optString("chain")),
                    puedeRetirar = c.optString("withdrawable") == "true",
                    puedeDepositar = c.optString("rechargeable") == "true",
                    comision = comisionDe(c, "withdrawFee"),
                    requiereTag = c.optString("needTag") == "true",
                ))
            }
        }
        return out
    }

    val FUENTES: List<FuenteTransferencias> = listOf(
        FuenteTransferencias("KuCoin", "https://api.kucoin.com/api/v3/currencies") { kucoin(it) },
        FuenteTransferencias("Gate.io", "https://api.gateio.ws/api/v4/spot/currencies") { gate(it) },
        FuenteTransferencias("Bitget", "https://api.bitget.com/api/v2/spot/public/coins") { bitget(it) },
    )
}
