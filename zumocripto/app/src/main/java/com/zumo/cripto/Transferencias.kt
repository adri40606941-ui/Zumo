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
        alias[clave]?.let { return it }
        // Cada exchange escribe la red a su manera ("Ethereum(ERC20)", "Binance smart chain"): se reconocen las más comunes.
        return when {
            "ERC20" in clave -> "ETH"
            "TRC20" in clave -> "TRX"
            "BEP20" in clave || "BINANCESMART" in clave || "BNBSMART" in clave -> "BSC"
            "ARBITRUM" in clave -> "ARB"
            "OPTIMISM" in clave -> "OP"
            "POLYGON" in clave -> "MATIC"
            "SOLANA" in clave -> "SOL"
            "AVAXC" in clave -> "AVAX"
            else -> clave
        }
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
            return Estado.NoVerificable("sin datos públicos de ${sinDatos.joinToString(" ni ")} (cargá su clave en Cuentas)")
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

/** Los exchanges que publican envíos sin clave, y el lector de cada uno. Funciones puras. */
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

    /** HTX GET /v2/reference/currencies: data[] con currency y chains[] (displayName, depositStatus/withdrawStatus "allowed", transactFeeWithdraw, addrWithTag/addrDepositTag). */
    fun htx(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONObject(json).getJSONArray("data")
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("currency").uppercase()
            val cadenas = m.optJSONArray("chains") ?: continue
            if (activo.isBlank()) continue
            for (j in 0 until cadenas.length()) {
                val c = cadenas.optJSONObject(j) ?: continue
                val nombre = c.optString("displayName").ifBlank { c.optString("chain") }
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(nombre),
                    puedeRetirar = c.optString("withdrawStatus") == "allowed",
                    puedeDepositar = c.optString("depositStatus") == "allowed",
                    comision = comisionDe(c, "transactFeeWithdraw"),
                    requiereTag = c.optBoolean("addrWithTag", false) || c.optBoolean("addrDepositTag", false),
                ))
            }
        }
        return out
    }

    private fun texto(a: JSONArray?): Set<String> {
        val r = HashSet<String>()
        if (a != null) for (i in 0 until a.length()) r.add(a.optString(i))
        return r
    }

    /** WhiteBIT GET /api/v4/public/assets: objeto por activo con can_withdraw/can_deposit, networks.{deposits,withdraws}[] e is_memo. No informa comisión. */
    fun whitebit(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val o = JSONObject(json)
        val claves = o.keys()
        while (claves.hasNext()) {
            val k = claves.next()
            val m = o.optJSONObject(k) ?: continue
            val redes = m.optJSONObject("networks") ?: continue
            val depositos = texto(redes.optJSONArray("deposits"))
            val retiros = texto(redes.optJSONArray("withdraws"))
            val activo = k.uppercase()
            for (n in depositos + retiros) {
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(n),
                    puedeRetirar = m.optBoolean("can_withdraw", false) && n in retiros,
                    puedeDepositar = m.optBoolean("can_deposit", false) && n in depositos,
                    comision = null,
                    requiereTag = m.optBoolean("is_memo", false),
                ))
            }
        }
        return out
    }

    /** XT.com GET /v4/public/wallet/support/currency: result[] con currency y supportChains[] (chain, depositEnabled, withdrawEnabled, withdrawFeeAmount en withdrawFeeCurrency). */
    fun xt(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONObject(json).getJSONArray("result")
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            val activo = m.optString("currency").uppercase()
            val cadenas = m.optJSONArray("supportChains") ?: continue
            if (activo.isBlank()) continue
            for (j in 0 until cadenas.length()) {
                val c = cadenas.optJSONObject(j) ?: continue
                // La comisión solo vale si se cobra en el propio activo.
                val enElActivo = c.optString("withdrawFeeCurrency").equals(activo, ignoreCase = true)
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(c.optString("chain")),
                    puedeRetirar = c.optBoolean("withdrawEnabled", false),
                    puedeDepositar = c.optBoolean("depositEnabled", false),
                    comision = if (enElActivo) comisionDe(c, "withdrawFeeAmount") else null,
                    requiereTag = false,
                ))
            }
        }
        return out
    }

    /** Poloniex GET /v2/currencies: [] con coin, delisted y networkList[] (name, withdrawalEnable, depositEnable, withdrawFee). */
    fun poloniex(json: String): Map<String, List<RedDe>> {
        val out = HashMap<String, MutableList<RedDe>>()
        val filas = JSONArray(json)
        for (i in 0 until filas.length()) {
            val m = filas.optJSONObject(i) ?: continue
            if (m.optBoolean("delisted", false)) continue
            val activo = m.optString("coin").uppercase()
            val redes = m.optJSONArray("networkList") ?: continue
            if (activo.isBlank()) continue
            for (j in 0 until redes.length()) {
                val r = redes.optJSONObject(j) ?: continue
                out.getOrPut(activo) { ArrayList() }.add(RedDe(
                    red = Redes.canonica(r.optString("name").ifBlank { r.optString("blockchain") }),
                    puedeRetirar = r.optBoolean("withdrawalEnable", false),
                    puedeDepositar = r.optBoolean("depositEnable", false),
                    comision = comisionDe(r, "withdrawFee"),
                    requiereTag = false,
                ))
            }
        }
        return out
    }

    val FUENTES: List<FuenteTransferencias> = listOf(
        FuenteTransferencias("KuCoin", "https://api.kucoin.com/api/v3/currencies") { kucoin(it) },
        FuenteTransferencias("Gate.io", "https://api.gateio.ws/api/v4/spot/currencies") { gate(it) },
        FuenteTransferencias("Bitget", "https://api.bitget.com/api/v2/spot/public/coins") { bitget(it) },
        FuenteTransferencias("HTX", "https://api.huobi.pro/v2/reference/currencies") { htx(it) },
        FuenteTransferencias("WhiteBIT", "https://whitebit.com/api/v4/public/assets") { whitebit(it) },
        FuenteTransferencias("XT.com", "https://sapi.xt.com/v4/public/wallet/support/currency") { xt(it) },
        FuenteTransferencias("Poloniex", "https://api.poloniex.com/v2/currencies") { poloniex(it) },
    )
}
