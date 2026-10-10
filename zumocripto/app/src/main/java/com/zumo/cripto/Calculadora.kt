package com.zumo.cripto

/**
 * Cuánto queda de un margen después de pagar comisiones: se compra al precio barato (con comisión)
 * y se vende al precio caro (con comisión). Funciones puras, sin red ni Android: fáciles de probar.
 */
object Calculadora {
    /** Porcentaje de ganancia neta. Las comisiones son porcentajes por operación (0.1 = 0,1 %). */
    fun netoPct(barato: Double, caro: Double, comisionCompraPct: Double, comisionVentaPct: Double): Double {
        if (barato <= 0) return 0.0
        val costo = barato * (1 + comisionCompraPct / 100.0)
        val cobro = caro * (1 - comisionVentaPct / 100.0)
        return (cobro - costo) / costo * 100.0
    }

    fun netoPct(op: Oportunidad, comisionPct: Double): Double =
        netoPct(op.barato.precioUsd, op.caro.precioUsd, comisionPct, comisionPct)
}
