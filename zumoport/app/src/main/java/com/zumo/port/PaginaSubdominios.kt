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
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** Pestaña "Subdominios": busca los nombres de un dominio y a qué IP apunta cada uno. */
class PaginaSubdominios(private val act: Activity, private val ui: Ui, private val alEscanear: (String) -> Unit) {
    private val principal = Handler(Looper.getMainLooper())
    private val candado = Any()
    private val encontrados = ArrayList<Subdominio>()
    private var version = 0
    private var versionPintada = -1

    private var buscador: BuscadorSubdominios? = null
    private var buscando = false
    private var usarCert = true
    private var usarHt = true
    private var usarLista = true
    private var mostrarSinIp = false
    @Volatile private var hechos = 0
    @Volatile private var total = 0
    private var hubo = false
    @Volatile private var mensaje = "Listo para buscar"
    private var segundos = 0L
    private var inicio = 0L

    private val campoDominio = ui.campo("Dominio (ejemplo.com)")
    private val botonPrincipal: TextView
    private val textoEstado = ui.texto("Listo para buscar", 14f, Paleta.APAGADO)
    private val barra = Barra(act, ui)
    private val textoResumen = ui.texto("", 13f, Paleta.APAGADO)
    private val cajaFiltros = ui.horizontal()
    private val cajaResultados = ui.vertical()
    private lateinit var chipCert: TextView
    private lateinit var chipHt: TextView
    private lateinit var chipLista: TextView

    val vista: ScrollView = ScrollView(act)

    private val refresco = object : Runnable {
        override fun run() {
            pintarEstado()
            if (buscando) principal.postDelayed(this, 300)
        }
    }

    init {
        campoDominio.inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
        val col = ui.vertical()
        col.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(24))

        val t1 = ui.tarjeta()
        t1.addView(ui.texto("🌐  Dominio", 16f, Paleta.TEXTO, true))
        t1.addView(campoDominio, ui.params(arriba = 10))
        t1.addView(ui.texto("Cómo buscar (se suman):", 13f, Paleta.APAGADO), ui.params(arriba = 12))
        chipCert = ui.chip("🔐 Certificados públicos (crt.sh)", usarCert) { usarCert = !usarCert; ui.pintarChip(chipCert, usarCert) }
        chipHt = ui.chip("🛰 HackerTarget", usarHt) { usarHt = !usarHt; ui.pintarChip(chipHt, usarHt) }
        chipLista = ui.chip("📖 Lista de nombres comunes (www, mail, api…)", usarLista) { usarLista = !usarLista; ui.pintarChip(chipLista, usarLista) }
        t1.addView(chipCert, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 8))
        t1.addView(chipHt, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 8))
        t1.addView(chipLista, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 8))
        t1.addView(ui.texto("Los dos primeros consultan servicios públicos de internet (a veces están saturados). La lista prueba cada nombre preguntando al DNS.", 12f, Paleta.APAGADO), ui.params(arriba = 10))
        col.addView(t1)

        botonPrincipal = ui.boton("🔎  BUSCAR SUBDOMINIOS") { if (buscando) detener() else empezar() }
        col.addView(botonPrincipal, ui.params(arriba = 14))

        val t2 = ui.tarjeta()
        t2.addView(textoEstado)
        t2.addView(barra, ui.params(arriba = 10))
        t2.addView(textoResumen, ui.params(arriba = 8))
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

        vista.addView(col)
        pintarEstado()
    }

    private fun toast(t: String) { Toast.makeText(act, t, Toast.LENGTH_SHORT).show() }

    private fun empezar() {
        val dominio = BuscadorSubdominios.limpiarDominio(campoDominio.text.toString())
        if (dominio == null) { toast("Escribí un dominio, por ejemplo ejemplo.com"); return }
        if (!usarCert && !usarHt && !usarLista) { toast("Elegí al menos una forma de buscar"); return }
        val imm = act.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(vista.windowToken, 0)
        synchronized(candado) { encontrados.clear(); version++ }
        hechos = 0; total = 0; hubo = true; inicio = System.currentTimeMillis(); mensaje = "Buscando…"
        buscando = true
        botonPrincipal.text = "⏹  DETENER"
        act.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val b = BuscadorSubdominios()
        buscador = b
        val m = Metodos(usarCert, usarHt, usarLista)
        Thread({
            try {
                b.buscar(dominio, m, { mensaje = it }, { s -> synchronized(candado) { encontrados.add(s); version++ } }, { h, t -> hechos = h; total = t })
            } catch (_: Exception) {
                mensaje = "Hubo un error al buscar"
            }
            principal.post { terminar() }
        }, "zumoport-subdominios").start()
        principal.post(refresco)
    }

    private fun detener() { buscador?.cancelar(); mensaje = "Deteniendo…" }

    private fun terminar() {
        buscando = false
        botonPrincipal.text = "🔎  BUSCAR SUBDOMINIOS"
        act.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        segundos = (System.currentTimeMillis() - inicio) / 1000
        mensaje = "✔ Terminó en $segundos s"
        pintarEstado()
    }

    fun cerrar() { buscador?.cancelar() }

    private fun pintarEstado() {
        textoEstado.text = mensaje
        textoEstado.setTextColor(if (buscando) Paleta.CYAN else if (hubo) Paleta.VERDE else Paleta.APAGADO)
        barra.poner(if (total > 0) hechos.toDouble() / total else 0.0)
        val (con, sin) = synchronized(candado) { encontrados.count { it.resuelve } to encontrados.count { !it.resuelve } }
        textoResumen.text = if (!hubo) "" else "$con responden · $sin solo aparecen en certificados"
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
        cajaFiltros.addView(ui.chip("Mostrar también los que no responden", mostrarSinIp) { mostrarSinIp = !mostrarSinIp; pintarResultados() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT))
        var n = 0
        var ocultos = 0
        for (s in todos) {
            if (!s.resuelve && !mostrarSinIp) continue
            if (n >= MAX_FILAS) { ocultos++; continue }
            n++
            val fila = ui.vertical()
            fila.background = ui.fondo(Paleta.TARJETA_2, 12)
            fila.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10))
            val l = ui.horizontal()
            l.addView(ui.texto(s.nombre, 14f, if (s.resuelve) Paleta.CYAN else Paleta.APAGADO, true), ui.params(ancho = 0, peso = 1f))
            l.addView(ui.insignia(s.fuente, if (s.resuelve) Paleta.VERDE else Paleta.AMARILLO))
            fila.addView(l)
            fila.addView(ui.texto(if (s.resuelve) s.ips.joinToString("  ") else "sin IP: hoy no responde", 12f, Paleta.APAGADO), ui.params(arriba = 4))
            fila.setOnClickListener { acciones(s) }
            cajaResultados.addView(fila, ui.params(abajo = 8))
        }
        if (n == 0) cajaResultados.addView(ui.texto("Ninguno responde. Tocá «Mostrar también los que no responden».", 13f, Paleta.APAGADO))
        if (ocultos > 0) cajaResultados.addView(ui.texto("… y $ocultos más. Usá Copiar o Compartir para llevarte todos.", 12f, Paleta.AMARILLO))
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
        copia().filter { it.resuelve || mostrarSinIp }.joinToString("\n") { it.nombre + (if (it.resuelve) "  " + it.ips.joinToString(",") else "  (sin IP)") }

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
