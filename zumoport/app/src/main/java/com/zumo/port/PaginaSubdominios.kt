package com.zumo.port

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Pestaña "Subdominios": busca los nombres de un dominio y a qué IP apunta cada uno. */
class PaginaSubdominios(private val act: Activity, private val ui: Ui, private val red: Red, private val alEscanear: (String) -> Unit) {
    private val principal = Handler(Looper.getMainLooper())
    private val candado = Any()
    private val encontrados = ArrayList<Subdominio>()
    private var version = 0
    private var versionPintada = -1

    private var buscador: BuscadorSubdominios? = null
    private var buscando = false
    private var detenido = false
    private val fuentesActivas: java.util.EnumSet<Fuente> = java.util.EnumSet.copyOf(Fuente.ELEGIBLES)
    private val chipsFuentes = LinkedHashMap<Fuente, TextView>()
    @Volatile private var fuentesResumen: List<ResultadoFuente> = emptyList()
    private var probador: ProbadorHttp? = null
    private var probando = false
    private var soloOk = false
    private var verAsn = true
    private var verPuertos = true
    private var pedidoAsn = false           // lo que se pidió en la búsqueda que se está mostrando (no cambia si se tocan los chips después)
    private var pedidoPuertos = false
    private var mostrarSinIp = false
    @Volatile private var hechos = 0
    @Volatile private var total = 0
    private var hubo = false
    @Volatile private var mensaje = "Listo para buscar"
    private var segundos = 0L
    private var inicio = 0L

    private val campoDominio = ui.campo("Dominio (ejemplo.com)")
    private val campoPuertos = ui.campo("Puertos a probar (80,443,8080…)")
    private val botonPrincipal: TextView
    private val textoEstado = ui.texto("Listo para buscar", 14f, Paleta.APAGADO)
    private val barra = Barra(act, ui)
    private val textoResumen = ui.texto("", 13f, Paleta.APAGADO)
    private val textoFuentes = ui.texto("", 12f, Paleta.APAGADO)
    private val textoPrueba = ui.texto("", 13f, Paleta.APAGADO)
    private val botonProbar: TextView
    private val cajaFiltros = ui.horizontal()
    private val cajaResultados = ui.vertical()
    private lateinit var chipAsn: TextView
    private lateinit var chipPuertos: TextView
    private val chipsSalida = ArrayList<Pair<Salida, TextView>>()
    private var esperandoRed = false
    private var nombreRed = ""

    val vista: ScrollView = ScrollView(act)

    private val refresco = object : Runnable {
        override fun run() {
            pintarEstado()
            if (buscando || probando) principal.postDelayed(this, 300)
        }
    }

    init {
        campoDominio.inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
        campoPuertos.setText(BuscadorSubdominios.PUERTOS_POR_DEFECTO.joinToString(","))
        val col = ui.vertical()
        col.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(24))

        val t1 = ui.tarjeta()
        t1.addView(ui.texto("🌐  Dominio", 16f, Paleta.TEXTO, true))
        t1.addView(campoDominio, ui.params(arriba = 10))
        t1.addView(ui.texto("De dónde sacar los nombres (se suman):", 13f, Paleta.APAGADO), ui.params(arriba = 12))
        var fila: android.widget.LinearLayout? = null
        for ((i, f) in Fuente.ELEGIBLES.withIndex()) {
            if (i % 2 == 0) { fila = ui.horizontal(); t1.addView(fila, ui.params(arriba = 8)) }
            val c = ui.chip(f.chip, f in fuentesActivas) {
                if (f in fuentesActivas) fuentesActivas.remove(f) else fuentesActivas.add(f)
                ui.pintarChip(chipsFuentes[f]!!, f in fuentesActivas)
            }
            chipsFuentes[f] = c
            fila!!.addView(c, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        }
        t1.addView(ui.chip("✅ Marcar todas", false) {
            fuentesActivas.addAll(Fuente.ELEGIBLES)
            for ((f, c) in chipsFuentes) ui.pintarChip(c, f in fuentesActivas)
        }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 8))
        t1.addView(ui.texto("Las de internet se consultan todas a la vez: más fuentes, lista más completa. A veces alguna está saturada o no contesta; al terminar se ve cuáles respondieron. La lista de nombres comunes prueba cada nombre preguntando al DNS.", 12f, Paleta.APAGADO), ui.params(arriba = 10))
        col.addView(t1)

        val tx = ui.tarjeta()
        tx.addView(ui.texto("🔬  De cada subdominio que responde", 16f, Paleta.TEXTO, true))
        chipAsn = ui.chip("🏢 ASN (a qué empresa o red pertenece)", verAsn) { verAsn = !verAsn; ui.pintarChip(chipAsn, verAsn) }
        chipPuertos = ui.chip("🔌 Puertos abiertos", verPuertos) { verPuertos = !verPuertos; ui.pintarChip(chipPuertos, verPuertos) }
        tx.addView(chipAsn, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 10))
        tx.addView(chipPuertos, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 8))
        tx.addView(campoPuertos, ui.params(arriba = 10))
        tx.addView(ui.texto("El ASN se consulta en Team Cymru (por DNS sobre HTTPS). Los puertos se prueban una vez por IP, aunque varios subdominios compartan la misma. Cuantos más puertos, más tarda (máximo ${BuscadorSubdominios.MAX_PUERTOS}).", 12f, Paleta.APAGADO), ui.params(arriba = 10))
        col.addView(tx, ui.params(arriba = 12))

        val tr = ui.tarjeta()
        tr.addView(ui.texto("📶  Por qué red salir", 16f, Paleta.TEXTO, true))
        val filaR = ui.horizontal()
        for (sa in Salida.values()) {
            val c = ui.chip(sa.titulo, sa == Ajustes.salida) { Ajustes.salida = sa; repintarRed() }
            chipsSalida.add(sa to c)
            filaR.addView(c, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        }
        tr.addView(filaR, ui.params(arriba = 10))
        col.addView(tr, ui.params(arriba = 12))

        botonPrincipal = ui.boton("🔎  BUSCAR SUBDOMINIOS") {
            if (buscando) detener()
            else if (probando) toast("Esperá a que termine la prueba, o detenela")
            else empezar()
        }
        col.addView(botonPrincipal, ui.params(arriba = 14))

        val t2 = ui.tarjeta()
        t2.addView(textoEstado)
        t2.addView(barra, ui.params(arriba = 10))
        t2.addView(textoResumen, ui.params(arriba = 8))
        t2.addView(textoPrueba, ui.params(arriba = 4))
        t2.addView(textoFuentes, ui.params(arriba = 8))
        col.addView(t2, ui.params(arriba = 14))

        val t3 = ui.tarjeta()
        val cab = ui.horizontal()
        cab.addView(ui.texto("🧭  Subdominios", 16f, Paleta.TEXTO, true), ui.params(ancho = 0, peso = 1f))
        cab.addView(ui.chip("Copiar", false) { copiar() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        cab.addView(ui.chip("Compartir", false) { compartir() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT))
        t3.addView(cab)
        t3.addView(cajaFiltros, ui.params(arriba = 10))
        t3.addView(cajaResultados, ui.params(arriba = 6))
        col.addView(t3, ui.params(arriba = 14))

        // Abajo del todo, cuando termina el escaneo: prueba cada subdominio por HTTP/HTTPS en sus puertos abiertos.
        botonProbar = ui.boton("🧪  PROBAR HTTP DE CADA SUBDOMINIO", relleno = false) { if (probando) detenerPrueba() else empezarPrueba() }
        botonProbar.visibility = View.GONE
        col.addView(botonProbar, ui.params(arriba = 14))

        vista.addView(col)
        pintarEstado()
    }

    fun repintarRed() { for ((sa, c) in chipsSalida) ui.pintarChip(c, sa == Ajustes.salida) }

    private fun toast(t: String) { Toast.makeText(act, t, Toast.LENGTH_SHORT).show() }

    private fun empezar() {
        val dominio = BuscadorSubdominios.limpiarDominio(campoDominio.text.toString())
        if (dominio == null) { toast("Escribí un dominio, por ejemplo ejemplo.com"); return }
        if (fuentesActivas.isEmpty()) { toast("Elegí al menos una fuente de nombres"); return }
        var puertos = emptyList<Int>()
        if (verPuertos) {
            val pp = Puertos.analizar(campoPuertos.text.toString())
            if (pp.error != null) { toast(pp.error); return }
            if (pp.puertos.size > BuscadorSubdominios.MAX_PUERTOS) { toast("Máximo ${BuscadorSubdominios.MAX_PUERTOS} puertos por subdominio"); return }
            puertos = pp.puertos
        }
        val imm = act.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(vista.windowToken, 0)
        if (esperandoRed) return
        esperandoRed = true
        mensaje = "Conectando por ${Ajustes.salida.nombre}…"
        pintarEstado()
        red.conectar(Ajustes.salida) { ok, texto ->
            esperandoRed = false
            if (!ok) { toast(texto); mensaje = texto; pintarEstado() }
            else { nombreRed = texto; arrancar(dominio, puertos) }
        }
    }

    private fun arrancar(dominio: String, puertos: List<Int>) {
        detenido = false
        pedidoAsn = verAsn
        pedidoPuertos = puertos.isNotEmpty()
        synchronized(candado) { encontrados.clear(); version++ }
        fuentesResumen = emptyList(); soloOk = false
        botonProbar.visibility = View.GONE
        hechos = 0; total = 0; hubo = true; inicio = System.currentTimeMillis(); mensaje = "Buscando por $nombreRed…"
        buscando = true
        botonPrincipal.text = "⏹  DETENER"
        act.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val b = BuscadorSubdominios()
        buscador = b
        val m = Metodos(java.util.EnumSet.copyOf(fuentesActivas))
        val extras = Extras(pedidoAsn, puertos)
        // Solo cuenta lo que llega de ESTA búsqueda: si se tocó Detener, lo que llegue tarde de hilos que siguen terminando se descarta.
        Thread({
            try {
                b.buscar(dominio, m, { if (buscador === b) mensaje = it }, { s -> if (buscador === b) synchronized(candado) { encontrados.add(s); version++ } },
                    { h, t -> if (buscador === b) { hechos = h; total = t } }, extras, { if (buscador === b) fuentesResumen = it }) { if (buscador === b) synchronized(candado) { version++ } }
            } catch (_: Exception) {
                if (buscador === b) mensaje = "Hubo un error al buscar"
            }
            principal.post { if (buscador === b) terminar() }
        }, "zumoport-subdominios").start()
        principal.post(refresco)
    }

    private fun detener() {
        val b = buscador ?: return
        detenido = true
        b.cancelar()
        mensaje = "Deteniendo…"
        // La búsqueda vuelve sola en un instante; si por lo que sea no lo hiciera, a los 3 segundos se cierra igual para que no quede trabado.
        principal.postDelayed({ if (buscador === b && buscando) terminar() }, 3000)
    }

    private fun terminar() {
        if (!buscando) return
        buscador = null
        buscando = false
        red.soltar()
        botonPrincipal.text = "🔎  BUSCAR SUBDOMINIOS"
        act.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        segundos = (System.currentTimeMillis() - inicio) / 1000
        mensaje = if (detenido) "⏹ Detenido a los $segundos s" else "✔ Terminó en $segundos s"
        pintarEstado()
        actualizarBotonProbar()
    }

    fun cerrar() { buscador?.cancelar(); probador?.cancelar() }

    // ---------------------------------------------------------------- probar HTTP (botón de abajo del todo)

    /** Los subdominios que tienen al menos un puerto abierto (los que se pueden probar). */
    private fun probables(): List<Subdominio> = copia().filter { it.puertosPorIp.isNotEmpty() }

    private fun actualizarBotonProbar() {
        val hayResueltos = synchronized(candado) { encontrados.any { it.resuelve } }
        val visible = hubo && !buscando && hayResueltos
        botonProbar.visibility = if (visible) View.VISIBLE else View.GONE
        if (!visible) return
        botonProbar.text = when {
            probando -> "⏹  DETENER LA PRUEBA"
            else -> {
                val n = probables().size
                if (n > 0) "🧪  PROBAR HTTP DE LOS $n SUBDOMINIOS CON PUERTOS ABIERTOS" else "🧪  PROBAR HTTP (buscá con «Puertos abiertos» activado)"
            }
        }
    }

    private fun empezarPrueba() {
        if (buscando || probando || esperandoRed) return
        val subs = probables()
        if (subs.isEmpty()) { toast("No hay puertos abiertos para probar. Buscá con «Puertos abiertos» activado."); return }
        esperandoRed = true
        mensaje = "Conectando por ${Ajustes.salida.nombre}…"
        pintarEstado()
        red.conectar(Ajustes.salida) { ok, texto ->
            esperandoRed = false
            if (!ok) { toast(texto); mensaje = texto; pintarEstado() }
            else { nombreRed = texto; arrancarPrueba(subs) }
        }
    }

    private fun arrancarPrueba(subs: List<Subdominio>) {
        detenido = false
        probando = true
        hechos = 0; total = 0; inicio = System.currentTimeMillis()
        mensaje = "Probando HTTP por $nombreRed…"
        act.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val p = ProbadorHttp()
        probador = p
        actualizarBotonProbar()
        Thread({
            try {
                p.probar(subs, { h, t -> if (probador === p) { hechos = h; total = t } }) { if (probador === p) synchronized(candado) { version++ } }
            } catch (_: Exception) {
                if (probador === p) mensaje = "Hubo un error al probar"
            }
            principal.post { if (probador === p) terminarPrueba() }
        }, "zumoport-prueba-http").start()
        principal.post(refresco)
    }

    private fun detenerPrueba() {
        val p = probador ?: return
        detenido = true
        p.cancelar()
        mensaje = "Deteniendo…"
        principal.postDelayed({ if (probador === p && probando) terminarPrueba() }, 3000)
    }

    private fun terminarPrueba() {
        if (!probando) return
        probador = null
        probando = false
        red.soltar()
        act.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val seg = (System.currentTimeMillis() - inicio) / 1000
        for (s in copia()) s.probando = false
        mensaje = if (detenido) "⏹ Prueba detenida a los $seg s" else "✔ Prueba terminada en $seg s"
        synchronized(candado) { version++ }
        pintarEstado()
        actualizarBotonProbar()
    }

    /** Un resumen corto de lo que se probó: cuántos subdominios responden bien por HTTP y cuántos no. */
    private fun resumenPrueba(): String {
        val probados = copia().filter { it.pruebas.isNotEmpty() }
        if (probados.isEmpty()) return ""
        val bien = probados.count { s -> s.pruebas.any { it.ok } }
        val conError = probados.count { s -> s.pruebas.none { it.ok } && s.pruebas.any { it.http >= 400 } }
        val sinWeb = probados.size - bien - conError
        return "🧪 HTTP: $bien responden bien (2xx/3xx) · $conError con error (4xx/5xx) · $sinWeb sin respuesta web"
    }

    private fun pintarEstado() {
        textoEstado.text = mensaje
        textoEstado.setTextColor(if (buscando || probando) Paleta.CYAN else if (hubo) Paleta.VERDE else Paleta.APAGADO)
        barra.poner(if (total > 0) hechos.toDouble() / total else 0.0)
        val (con, sin) = synchronized(candado) { encontrados.count { it.resuelve } to encontrados.count { !it.resuelve } }
        textoResumen.text = if (!hubo) "" else "$con responden · $sin solo aparecen en fuentes públicas"
        textoPrueba.text = resumenPrueba()
        textoFuentes.text = fuentesResumen.joinToString("  ·  ") { (if (it.ok) "✔ " else "✘ ") + it.fuente.nombre + if (it.ok) " ${it.cantidad}" else "" }
        textoFuentes.visibility = if (fuentesResumen.isEmpty()) View.GONE else View.VISIBLE
        if (versionPintada != version) { versionPintada = version; pintarResultados() }
    }

    private fun copia(): List<Subdominio> = synchronized(candado) { encontrados.sortedBy { it.nombre } }

    private fun pintarResultados() {
        val todos = copia()
        cajaFiltros.removeAllViews()
        cajaResultados.removeAllViews()
        if (todos.isEmpty()) {
            cajaResultados.addView(ui.texto(if (!hubo) "Todavía no buscaste nada." else if (buscando) "Buscando…" else "No se encontró ningún subdominio.", 13f, Paleta.APAGADO))
            return
        }
        cajaFiltros.addView(ui.chip("Mostrar también los que no responden", mostrarSinIp) { mostrarSinIp = !mostrarSinIp; pintarResultados() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        if (todos.any { it.pruebas.isNotEmpty() }) {
            cajaFiltros.addView(ui.chip("✅ Solo HTTP OK", soloOk) { soloOk = !soloOk; pintarResultados() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        var n = 0
        var ocultos = 0
        for (s in todos) {
            if (!s.resuelve && !mostrarSinIp) continue
            if (soloOk && s.pruebas.none { it.ok }) continue
            if (n >= MAX_FILAS) { ocultos++; continue }
            n++
            val fila = ui.vertical()
            fila.background = ui.fondo(Paleta.TARJETA_2, 12)
            fila.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10))
            val l = ui.horizontal()
            l.addView(ui.texto(s.nombre, 14f, if (s.resuelve) Paleta.CYAN else Paleta.APAGADO, true), ui.params(ancho = 0, peso = 1f))
            l.addView(ui.insignia(etiquetaFuente(s.fuente), if (s.resuelve) Paleta.VERDE else Paleta.AMARILLO))
            fila.addView(l)
            fila.addView(ui.texto(if (s.resuelve) s.ips.joinToString("  ") else "sin IP: hoy no responde", 12f, Paleta.APAGADO), ui.params(arriba = 4))
            if (s.resuelve && pedidoAsn) {
                val hay = s.asns.isNotEmpty()
                val t = when {
                    hay -> "🏢 " + s.asns.joinToString("  ·  ") { it.etiqueta }
                    s.asnListo -> "🏢 ASN: sin dato"
                    buscando -> "🏢 ASN: buscando…"
                    else -> "🏢 ASN: sin dato"
                }
                fila.addView(ui.texto(t, 12f, if (hay) Paleta.CYAN else Paleta.APAGADO, hay), ui.params(arriba = 4))
            }
            if (s.resuelve && pedidoPuertos) {
                val hay = s.puertos.isNotEmpty()
                val t = when {
                    hay -> "🔌 Puertos abiertos: " + s.puertos.joinToString("  ")
                    s.puertosListo -> "🔌 Ningún puerto abierto de los probados"
                    buscando -> "🔌 Puertos: probando…"
                    else -> "🔌 Puertos: sin dato"
                }
                fila.addView(ui.texto(t, 12f, if (hay) Paleta.VERDE else Paleta.APAGADO, hay), ui.params(arriba = 4))
            }
            if (s.probando && s.pruebas.isEmpty()) fila.addView(ui.texto("🧪 Probando HTTP…", 12f, Paleta.CYAN), ui.params(arriba = 4))
            for (pr in s.pruebas.sortedWith(compareBy({ it.puerto }, { it.ip }))) {
                val color = when {
                    pr.ok -> Paleta.VERDE
                    pr.http in 400..499 -> Paleta.AMARILLO
                    pr.http >= 500 -> Paleta.NARANJA
                    else -> Paleta.APAGADO
                }
                fila.addView(ui.texto("↳ " + pr.linea(), 12f, color, pr.ok), ui.params(arriba = 3))
            }
            fila.setOnClickListener { acciones(s) }
            cajaResultados.addView(fila, ui.params(abajo = 8))
        }
        if (n == 0) cajaResultados.addView(ui.texto("Ninguno responde. Tocá «Mostrar también los que no responden».", 13f, Paleta.APAGADO))
        if (ocultos > 0) cajaResultados.addView(ui.texto("… y $ocultos más. Usá Copiar o Compartir para llevarte todos.", 12f, Paleta.AMARILLO))
    }

    /** "crt.sh + AlienVault + URLScan" → "crt.sh +2" para que entre en la etiqueta. */
    private fun etiquetaFuente(f: String): String {
        val p = f.split(" + ")
        return if (p.size > 2) p[0] + " +" + (p.size - 1) else f
    }

    private fun acciones(s: Subdominio) {
        val opciones = arrayOf<CharSequence>("🔌 Escanear puertos de este", "📋 Copiar el nombre", "🌍 Abrir en el navegador")
        AlertDialog.Builder(act)
            .setTitle(s.nombre)
            .setItems(opciones) { _, cual ->
                when (cual) {
                    0 -> alEscanear(s.nombre)
                    1 -> {
                        val cm = act.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        cm.setPrimaryClip(ClipData.newPlainText("Zumo Port", s.nombre))
                        toast("Copiado")
                    }
                    else -> try { act.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://" + s.nombre))) } catch (_: Exception) { toast("No se pudo abrir") }
                }
            }
            .show()
    }

    private fun textoPlano(): String =
        copia().filter { it.resuelve || mostrarSinIp }.joinToString("\n") { s ->
            val sb = StringBuilder(s.nombre)
            sb.append(if (s.resuelve) "  " + s.ips.joinToString(",") else "  (sin IP)")
            if (s.asns.isNotEmpty()) sb.append("  ").append(s.asns.joinToString(" / ") { "AS${it.numero} ${it.nombre}".trim() })
            if (s.puertos.isNotEmpty()) sb.append("  puertos ").append(s.puertos.joinToString(","))
            if (s.pruebas.isNotEmpty()) sb.append("  http ").append(s.pruebas.sortedBy { it.puerto }.joinToString(" ") { "${it.puerto}:" + (if (it.http > 0) it.http.toString() else if (it.tipo == null) "x" else "-") })
            sb.toString()
        }

    private fun copiar() {
        val t = textoPlano()
        if (t.isEmpty()) { toast("No hay resultados"); return }
        val cm = act.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("Zumo Port", t))
        toast("Copiado")
    }

    private fun compartir() {
        val t = textoPlano()
        if (t.isEmpty()) { toast("No hay resultados"); return }
        val i = Intent(Intent.ACTION_SEND)
        i.type = "text/plain"
        i.putExtra(Intent.EXTRA_TEXT, t)
        act.startActivity(Intent.createChooser(i, "Compartir subdominios"))
    }

    companion object { const val MAX_FILAS = 300 }
}
