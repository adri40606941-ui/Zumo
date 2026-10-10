package com.zumo.cripto

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.util.Locale

/** Pestaña "Oportunidades": lee los precios de varios exchanges y muestra dónde una misma cripto está más barata que en otro. */
class PaginaOportunidades(private val act: Activity) {
    private val ui = Ui(act)
    private val principal = Handler(Looper.getMainLooper())
    private val candado = Any()
    private var resultados: List<Oportunidad> = emptyList()
    private var version = 0
    private var versionPintada = -1

    private var buscador: BuscadorExchanges? = null
    private var buscando = false
    private var hubo = false
    @Volatile private var mensaje = "Listo para leer los exchanges"
    @Volatile private var informe = ""
    private var comision = 0.1

    private val campoMargen = ui.campo("Margen mínimo %, por ejemplo 1.0")
    private val campoComision = ui.campo("Comisión por operación %, por ejemplo 0.1")
    private val campoFiltro = ui.campo("Filtrar por cripto: BTC, ETH…")
    private val botonPrincipal: TextView
    private val textoEstado = ui.texto("Listo para leer los exchanges", 14f, Paleta.APAGADO)
    private val textoResumen = ui.texto("", 13f, Paleta.APAGADO)
    private val cajaResultados = ui.vertical()

    val vista: ScrollView = ScrollView(act)

    private val refresco = object : Runnable {
        override fun run() {
            pintarEstado()
            if (buscando) principal.postDelayed(this, 300)
        }
    }

    init {
        campoMargen.setText("1.0")
        campoComision.setText("0.1")
        campoMargen.inputType = android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL
        campoComision.inputType = android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL
        val col = ui.vertical()
        col.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(24))

        // --- Botón principal y estado
        botonPrincipal = ui.boton("🔍  LEER EXCHANGES Y COMPARAR") { if (buscando) detener() else empezar() }
        col.addView(botonPrincipal)
        val tEstado = ui.tarjeta()
        tEstado.addView(textoEstado)
        tEstado.addView(textoResumen, ui.params(arriba = 8))
        col.addView(tEstado, ui.params(arriba = 12))

        // --- Filtros (se aplican sobre lo leído, sin volver a pedir nada)
        val t1 = ui.tarjeta()
        t1.addView(ui.texto("🎛  Filtros", 16f, Paleta.TEXTO, true))
        t1.addView(campoMargen, ui.params(arriba = 10))
        t1.addView(campoFiltro, ui.params(arriba = 10))
        t1.addView(campoComision, ui.params(arriba = 10))
        t1.addView(ui.texto("La comisión se cobra en la compra y en la venta: cada fila muestra cuánto te queda neto con ese valor.", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        t1.addView(ui.chip("✔ Aplicar filtros", false) { versionPintada = -1; pintarEstado() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 10))
        col.addView(t1, ui.params(arriba = 12))

        // --- Resultados
        val t2 = ui.tarjeta()
        t2.addView(ui.texto("💹  Oportunidades", 16f, Paleta.TEXTO, true))
        t2.addView(cajaResultados, ui.params(arriba = 10))
        col.addView(t2, ui.params(arriba = 12))

        vista.addView(col)
        pintarEstado()
    }

    private fun toast(t: String) { Toast.makeText(act, t, Toast.LENGTH_SHORT).show() }

    private fun parsePct(texto: String): Double? =
        texto.trim().replace(",", ".").toDoubleOrNull()?.takeIf { it >= 0 && it < 100 }

    private fun ocultarTeclado() {
        val imm = act.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(vista.windowToken, 0)
    }

    private fun empezar() {
        val com = parsePct(campoComision.text.toString())
        if (com == null) { toast("Poné una comisión válida, por ejemplo 0.1"); return }
        comision = com
        ocultarTeclado()
        synchronized(candado) { resultados = emptyList(); version++ }
        hubo = true
        buscando = true
        botonPrincipal.text = "⏹  DETENER"
        val b = BuscadorExchanges({ url -> Red.pedir(url) })
        buscador = b
        Thread({
            try {
                val r = b.buscar { mensaje = it }
                synchronized(candado) { resultados = r.oportunidades; version++ }
                informe = armarInforme(r)
                mensaje = if (r.cancelado) "Detenido" else "✔ Listo"
            } catch (_: Exception) {
                mensaje = "Hubo un error al leer los exchanges"
            }
            principal.post { terminar() }
        }, "zumocripto-exchanges").start()
        principal.post(refresco)
    }

    private fun armarInforme(r: BuscadorExchanges.Resultado): String {
        val base = "Leí ${r.leidos} de ${Exchanges.TODAS.size} exchanges"
        return if (r.fallaron.isEmpty()) base
        else "$base. No pude leer: ${r.fallaron.joinToString(", ")}. Puede ser que no estén disponibles en tu país."
    }

    private fun detener() { buscador?.cancelar(); mensaje = "Deteniendo…" }

    private fun terminar() {
        buscando = false
        botonPrincipal.text = "🔍  LEER EXCHANGES Y COMPARAR"
        pintarEstado()
    }

    fun cerrar() { buscador?.cancelar() }

    /** Lo que se ve según los filtros: margen mínimo y nombre de cripto, ordenado de mayor a menor margen. */
    private fun visibles(): List<Oportunidad> {
        val margen = parsePct(campoMargen.text.toString()) ?: 0.0
        val filtro = campoFiltro.text.toString().trim().lowercase()
        val todas = synchronized(candado) { resultados }
        return todas
            .filter { it.margenPct >= margen }
            .filter { filtro.isEmpty() || it.moneda.simbolo.lowercase().contains(filtro) }
            .sortedByDescending { it.margenPct }
    }

    private fun pintarEstado() {
        textoEstado.text = mensaje
        textoEstado.setTextColor(if (buscando) Paleta.CYAN else if (hubo) Paleta.VERDE else Paleta.APAGADO)
        val total = synchronized(candado) { resultados.size }
        textoResumen.text = when {
            !hubo -> ""
            buscando -> ""
            else -> "${visibles().size} de $total activos en varios exchanges. $informe"
        }
        if (versionPintada != version) { versionPintada = version; pintarResultados() }
    }

    private fun pintarResultados() {
        val lista = visibles()
        cajaResultados.removeAllViews()
        if (lista.isEmpty()) {
            val texto = when {
                !hubo -> "Todavía no leíste los exchanges."
                buscando -> "Leyendo…"
                synchronized(candado) { resultados.isEmpty() } -> "No se encontró ningún activo en dos exchanges."
                else -> "Ninguna con esos filtros. Bajá el margen mínimo o quitá el nombre de la cripto."
            }
            cajaResultados.addView(ui.texto(texto, 13f, Paleta.APAGADO))
            return
        }
        for (op in lista.take(MAX_FILAS)) {
            val fila = ui.vertical()
            fila.background = ui.fondo(Paleta.TARJETA_2, 12)
            fila.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10))
            val cab = ui.horizontal()
            cab.addView(ui.texto(op.moneda.simbolo, 15f, Paleta.CYAN, true), ui.params(ancho = 0, peso = 1f))
            cab.addView(ui.insignia("+" + formatoPct(op.margenPct) + "%", colorMargen(op.margenPct)))
            fila.addView(cab)
            fila.addView(ui.texto("${op.barato.exchange}: \$${formatoUsd(op.barato.precioUsd)}  →  ${op.caro.exchange}: \$${formatoUsd(op.caro.precioUsd)}", 13f, Paleta.TEXTO), ui.params(arriba = 6))
            val neto = Calculadora.netoPct(op, comision)
            fila.addView(ui.texto("Neto con comisiones: ${formatoPct(neto)}%", 12f, if (neto > 0) Paleta.VERDE else Paleta.ROJO, true), ui.params(arriba = 4))
            fila.addView(ui.texto("${op.exchanges} exchanges", 12f, Paleta.APAGADO), ui.params(arriba = 2))
            fila.setOnClickListener { detalle(op) }
            cajaResultados.addView(fila, ui.params(abajo = 8))
        }
        if (lista.size > MAX_FILAS) cajaResultados.addView(ui.texto("… y ${lista.size - MAX_FILAS} más. Usá el filtro para achicar la lista.", 12f, Paleta.AMARILLO))
    }

    private fun colorMargen(pct: Double): Int = when {
        pct >= 5.0 -> Paleta.VERDE
        pct >= 2.0 -> Paleta.AMARILLO
        else -> Paleta.CYAN
    }

    private fun formatoPct(v: Double): String = String.format(Locale.US, "%.2f", v)
    private fun formatoUsd(v: Double): String = if (v >= 1.0) String.format(Locale.US, "%,.2f", v) else String.format(Locale.US, "%.6f", v)

    /** Un detalle con la tabla de exchanges, de más barato a más caro, con el enlace para operar en cada uno. */
    private fun detalle(op: Oportunidad) {
        val cuerpo = LinearLayout(act)
        cuerpo.orientation = LinearLayout.VERTICAL
        cuerpo.setPadding(ui.dp(18), ui.dp(10), ui.dp(18), ui.dp(4))
        val neto = Calculadora.netoPct(op, comision)
        cuerpo.addView(ui.texto("Neto con comisiones de ${formatoPct(comision)}% por operación: ${formatoPct(neto)}%", 13f, if (neto > 0) Paleta.VERDE else Paleta.ROJO, true))
        for (t in op.tickers) {
            val f = ui.horizontal()
            f.addView(ui.texto("${t.exchange}  (${t.par})", 14f, Paleta.TEXTO, true), ui.params(ancho = 0, peso = 1f))
            f.addView(ui.texto("\$${formatoUsd(t.precioUsd)}", 14f, Paleta.CYAN))
            cuerpo.addView(f, ui.params(arriba = 8))
            if (t.urlOperar.isNotBlank()) {
                val link = ui.texto("Abrir en ${t.exchange}", 12f, Paleta.VIOLETA)
                link.setOnClickListener {
                    try { act.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(t.urlOperar))) } catch (_: Exception) { toast("No se pudo abrir") }
                }
                cuerpo.addView(link, ui.params(arriba = 2))
            }
        }
        AlertDialog.Builder(act)
            .setTitle("${op.moneda.simbolo} — margen ${formatoPct(op.margenPct)}%")
            .setView(ScrollView(act).also { it.addView(cuerpo) })
            .setPositiveButton("Copiar resumen") { _, _ ->
                val r = op.tickers.joinToString("\n") { "${it.exchange} (${it.par}): \$${formatoUsd(it.precioUsd)}" }
                val cm = act.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("Zumo Cripto", "${op.moneda.simbolo}\n$r\nNeto con comisiones: ${formatoPct(neto)}%"))
                toast("Copiado")
            }
            .setNegativeButton("Cerrar", null)
            .show()
    }

    companion object {
        const val MAX_FILAS = 200
    }
}
