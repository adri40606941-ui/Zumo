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

/** Pestaña "Oportunidades": busca criptos con margen entre exchanges (en el ranking o una sola), y calcula el neto con comisiones. */
class PaginaOportunidades(private val act: Activity) {
    private val ui = Ui(act)
    private val principal = Handler(Looper.getMainLooper())
    private val candado = Any()
    private val hallados = ArrayList<Oportunidad>()
    private var version = 0
    private var versionPintada = -1

    private var escaneo: Escaneo? = null
    private var buscando = false
    private var hubo = false
    @Volatile private var mensaje = "Listo para buscar"
    private var inicio = 0L
    private var cantidad = 100
    private var comision = 0.1

    private val campoMargen = ui.campo("Margen mínimo %, por ejemplo 1.0")
    private val campoComision = ui.campo("Comisión por operación %, por ejemplo 0.1")
    private val campoBuscar = ui.campo("BTC, ETH, solana…")
    private val campoClave = ui.campo("Clave de CoinGecko (opcional)")
    private var clave = ""
    private val botonPrincipal: TextView
    private val botonUna: TextView
    private val textoEstado = ui.texto("Listo para buscar", 14f, Paleta.APAGADO)
    private val barra = Barra(act, ui)
    private val textoResumen = ui.texto("", 13f, Paleta.APAGADO)
    private val cajaResultados = ui.vertical()
    private val chipsCantidad = ArrayList<Pair<Int, TextView>>()

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

        // --- Buscar una cripto puntual
        val tUna = ui.tarjeta()
        tUna.addView(ui.texto("🔎  Buscar una cripto", 16f, Paleta.TEXTO, true))
        tUna.addView(campoBuscar, ui.params(arriba = 10))
        tUna.addView(ui.texto("Mira su precio en todos los exchanges sin escanear el ranking. Busca entre las 250 primeras.", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        botonUna = ui.boton("BUSCAR ESTA", relleno = false) { if (buscando) detener() else empezarUna() }
        tUna.addView(botonUna, ui.params(arriba = 10))
        col.addView(tUna)

        // --- Cuántas monedas
        val t1 = ui.tarjeta()
        t1.addView(ui.texto("📊  Cuántas criptos revisar", 16f, Paleta.TEXTO, true))
        val filaC = ui.horizontal()
        for (c in CANTIDADES) {
            val chip = ui.chip("Top $c", c == cantidad) { cantidad = c; pintarCantidad() }
            chipsCantidad.add(c to chip)
            filaC.addView(chip, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        }
        val scrollC = android.widget.HorizontalScrollView(act)
        scrollC.isHorizontalScrollBarEnabled = false
        scrollC.addView(filaC)
        t1.addView(scrollC, ui.params(arriba = 10))
        t1.addView(ui.texto("Del ranking de CoinGecko por capitalización. Cuantas más, más tarda (pide de a una para no chocar con el límite de pedidos).", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        t1.addView(campoClave, ui.params(arriba = 12))
        t1.addView(ui.texto("Opcional. Una clave gratuita de CoinGecko (demo) sube el límite de pedidos y la búsqueda va más rápido. No se guarda: pegala cada vez que abras la app.", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        col.addView(t1, ui.params(arriba = 12))

        // --- Margen mínimo y comisiones
        val t2 = ui.tarjeta()
        t2.addView(ui.texto("💰  Margen y comisiones", 16f, Paleta.TEXTO, true))
        t2.addView(campoMargen, ui.params(arriba = 10))
        t2.addView(ui.texto("Solo se muestran las criptos donde la diferencia entre el exchange más caro y el más barato supera este porcentaje (antes de comisiones).", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        t2.addView(campoComision, ui.params(arriba = 12))
        t2.addView(ui.texto("Se cobra en la compra y en la venta. Cada fila muestra cuánto te queda neto con esta comisión.", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        col.addView(t2, ui.params(arriba = 12))

        // --- Botón del ranking y progreso
        botonPrincipal = ui.boton("🔍  BUSCAR OPORTUNIDADES") { if (buscando) detener() else empezar() }
        col.addView(botonPrincipal, ui.params(arriba = 14))
        val t3 = ui.tarjeta()
        t3.addView(textoEstado)
        t3.addView(barra, ui.params(arriba = 10))
        t3.addView(textoResumen, ui.params(arriba = 8))
        col.addView(t3, ui.params(arriba = 14))

        // --- Resultados
        val t4 = ui.tarjeta()
        t4.addView(ui.texto("💹  Oportunidades", 16f, Paleta.TEXTO, true))
        t4.addView(cajaResultados, ui.params(arriba = 10))
        col.addView(t4, ui.params(arriba = 14))

        vista.addView(col)
        pintarCantidad()
        pintarEstado()
    }

    private fun pintarCantidad() { for ((c, chip) in chipsCantidad) ui.pintarChip(chip, c == cantidad) }

    private fun toast(t: String) { Toast.makeText(act, t, Toast.LENGTH_SHORT).show() }

    private fun parsePct(texto: String): Double? =
        texto.trim().replace(",", ".").toDoubleOrNull()?.takeIf { it >= 0 && it < 100 }

    private fun ocultarTeclado() {
        val imm = act.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(vista.windowToken, 0)
    }

    private fun empezar() {
        val margen = parsePct(campoMargen.text.toString())
        if (margen == null) { toast("Poné un margen mínimo válido, por ejemplo 1.0"); return }
        val com = parsePct(campoComision.text.toString())
        if (com == null) { toast("Poné una comisión válida, por ejemplo 0.1"); return }
        comision = com
        ocultarTeclado()
        val n = cantidad
        clave = campoClave.text.toString().trim()
        iniciar { e ->
            e.buscar(
                cantidadMonedas = n,
                margenMinimoPct = margen,
                pausaMs = PAUSA_MS,
                alEstado = { mensaje = it },
                alHallar = { agregar(it) },
                alAvanzar = { _, _ -> },
            )
        }
    }

    private fun empezarUna() {
        val consulta = campoBuscar.text.toString()
        if (consulta.isBlank()) { toast("Escribí una cripto, por ejemplo BTC"); return }
        val com = parsePct(campoComision.text.toString())
        if (com == null) { toast("Poné una comisión válida, por ejemplo 0.1"); return }
        comision = com
        ocultarTeclado()
        clave = campoClave.text.toString().trim()
        iniciar { e ->
            val op = e.buscarUna(consulta, { mensaje = it })
            if (op != null) {
                agregar(op)
                mensaje = "✔ Listo: ${op.moneda.nombre}"
            }
        }
    }

    /** Arranca una búsqueda (del ranking o de una sola cripto) en un hilo aparte y pinta los resultados a medida que llegan. */
    private fun iniciar(tarea: (Escaneo) -> Unit) {
        synchronized(candado) { hallados.clear(); version++ }
        hubo = true; inicio = System.currentTimeMillis()
        buscando = true
        botonPrincipal.text = "⏹  DETENER"
        botonUna.text = "⏹  DETENER"
        val llave = clave
        val e = Escaneo({ url -> Red.pedir(url, llave) })
        escaneo = e
        Thread({
            try {
                tarea(e)
            } catch (_: Exception) {
                mensaje = "Hubo un error al buscar"
            }
            principal.post { terminar() }
        }, "zumocripto-busqueda").start()
        principal.post(refresco)
    }

    private fun agregar(op: Oportunidad) { synchronized(candado) { hallados.add(op); version++ } }

    private fun detener() { escaneo?.cancelar(); mensaje = "Deteniendo…" }

    private fun terminar() {
        buscando = false
        botonPrincipal.text = "🔍  BUSCAR OPORTUNIDADES"
        botonUna.text = "BUSCAR ESTA"
        pintarEstado()
    }

    fun cerrar() { escaneo?.cancelar() }

    private fun pintarEstado() {
        textoEstado.text = mensaje
        textoEstado.setTextColor(if (buscando) Paleta.CYAN else if (hubo) Paleta.VERDE else Paleta.APAGADO)
        val n = synchronized(candado) { hallados.size }
        textoResumen.text = if (!hubo) "" else "$n oportunidad(es) encontradas"
        if (versionPintada != version) { versionPintada = version; pintarResultados() }
    }

    private fun copia(): List<Oportunidad> = synchronized(candado) { hallados.sortedByDescending { it.margenPct } }

    private fun pintarResultados() {
        val todos = copia()
        cajaResultados.removeAllViews()
        if (todos.isEmpty()) {
            cajaResultados.addView(ui.texto(if (!hubo) "Todavía no buscaste nada." else if (buscando) "Buscando…" else "No se encontró ninguna con ese margen mínimo.", 13f, Paleta.APAGADO))
            return
        }
        for (op in todos.take(MAX_FILAS)) {
            val fila = ui.vertical()
            fila.background = ui.fondo(Paleta.TARJETA_2, 12)
            fila.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10))
            val cab = ui.horizontal()
            cab.addView(ui.texto("${op.moneda.nombre} (${op.moneda.simbolo})", 15f, Paleta.CYAN, true), ui.params(ancho = 0, peso = 1f))
            cab.addView(ui.insignia("+" + formatoPct(op.margenPct) + "%", colorMargen(op.margenPct)))
            fila.addView(cab)
            fila.addView(ui.texto("${op.barato.exchange}: \$${formatoUsd(op.barato.precioUsd)}  →  ${op.caro.exchange}: \$${formatoUsd(op.caro.precioUsd)}", 13f, Paleta.TEXTO), ui.params(arriba = 6))
            val neto = Calculadora.netoPct(op, comision)
            fila.addView(ui.texto("Neto con comisiones: ${formatoPct(neto)}%", 12f, if (neto > 0) Paleta.VERDE else Paleta.ROJO, true), ui.params(arriba = 4))
            fila.addView(ui.texto("${op.exchanges} exchanges comparados", 12f, Paleta.APAGADO), ui.params(arriba = 2))
            fila.setOnClickListener { detalle(op) }
            cajaResultados.addView(fila, ui.params(abajo = 8))
        }
        if (todos.size > MAX_FILAS) cajaResultados.addView(ui.texto("… y ${todos.size - MAX_FILAS} más.", 12f, Paleta.AMARILLO))
    }

    private fun colorMargen(pct: Double): Int = when {
        pct >= 5.0 -> Paleta.VERDE
        pct >= 2.0 -> Paleta.AMARILLO
        else -> Paleta.CYAN
    }

    private fun formatoPct(v: Double): String = String.format(Locale.US, "%.2f", v)
    private fun formatoUsd(v: Double): String = if (v >= 1.0) String.format(Locale.US, "%,.2f", v) else String.format(Locale.US, "%.6f", v)

    /** Un detalle con la tabla completa de exchanges, de más barato a más caro, con el enlace para operar si CoinGecko lo informa. */
    private fun detalle(op: Oportunidad) {
        val cuerpo = LinearLayout(act)
        cuerpo.orientation = LinearLayout.VERTICAL
        cuerpo.setPadding(ui.dp(18), ui.dp(10), ui.dp(18), ui.dp(4))
        val neto = Calculadora.netoPct(op, comision)
        cuerpo.addView(ui.texto("Neto con comisiones de ${formatoPct(comision)}% por operación: ${formatoPct(neto)}%", 13f, if (neto > 0) Paleta.VERDE else Paleta.ROJO, true))
        for (t in op.tickers) {
            val f = ui.horizontal()
            f.addView(ui.texto(t.exchange, 14f, Paleta.TEXTO, true), ui.params(ancho = 0, peso = 1f))
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
            .setTitle("${op.moneda.nombre} (${op.moneda.simbolo}) — margen ${formatoPct(op.margenPct)}%")
            .setView(ScrollView(act).also { it.addView(cuerpo) })
            .setPositiveButton("Copiar resumen") { _, _ ->
                val r = op.tickers.joinToString("\n") { "${it.exchange}: \$${formatoUsd(it.precioUsd)}" }
                val cm = act.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("Zumo Cripto", "${op.moneda.nombre}\n$r\nNeto con comisiones: ${formatoPct(neto)}%"))
                toast("Copiado")
            }
            .setNegativeButton("Cerrar", null)
            .show()
    }

    companion object {
        const val MAX_FILAS = 200
        const val PAUSA_MS = 700L
        val CANTIDADES = listOf(50, 100, 200, 500)
    }
}
