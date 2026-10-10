package com.zumo.port

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Zumo Port: escáner de IP, rangos, puertos y subdominios. Todo en código: cabecera, tres pestañas abajo y sus páginas. */
class MainActivity : Activity() {
    private lateinit var ui: Ui
    private lateinit var escanear: PaginaEscanear
    private lateinit var subdominios: PaginaSubdominios
    private lateinit var info: ScrollView
    private val paginas = ArrayList<View>()
    private val botones = ArrayList<TextView>()
    private val iconos = ArrayList<TextView>()
    private var actual = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Paleta.FONDO
        window.navigationBarColor = Paleta.TARJETA
        ui = Ui(this)

        val red = Red(this)
        escanear = PaginaEscanear(this, ui, red)
        subdominios = PaginaSubdominios(this, ui, red) { nombre -> escanear.ponerObjetivo(nombre); mostrar(0) }
        info = paginaInfo()

        val raiz = LinearLayout(this)
        raiz.orientation = LinearLayout.VERTICAL
        raiz.setBackgroundColor(Paleta.FONDO)
        raiz.addView(cabecera())

        val contenido = FrameLayout(this)
        for (p in listOf<View>(escanear.vista, subdominios.vista, info)) {
            paginas.add(p)
            contenido.addView(p, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
        raiz.addView(contenido, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        raiz.addView(barraInferior())
        setContentView(raiz)
        mostrar(0)
    }

    private fun cabecera(): View {
        val c = LinearLayout(this)
        c.orientation = LinearLayout.VERTICAL
        c.background = ui.degradado(Color.parseColor("#1B1B5E"), Color.parseColor("#0E5F7A"), 0)
        c.setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(14))
        val fila = ui.horizontal()
        fila.addView(ui.texto("📡", 30f), ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 12))
        val tit = ui.vertical()
        val nombre = ui.texto("ZUMO PORT", 22f, Color.WHITE, true)
        nombre.letterSpacing = 0.12f
        tit.addView(nombre)
        tit.addView(ui.texto("Escáner de IP, puertos y subdominios", 12f, Paleta.CYAN))
        fila.addView(tit, ui.params(ancho = 0, peso = 1f))
        c.addView(fila)
        return c
    }

    private fun barraInferior(): View {
        val b = LinearLayout(this)
        b.orientation = LinearLayout.HORIZONTAL
        b.setBackgroundColor(Paleta.TARJETA)
        b.setPadding(ui.dp(6), ui.dp(6), ui.dp(6), ui.dp(6))
        val items = listOf("📡" to "Escanear", "🧭" to "Subdominios", "ℹ️" to "Info")
        for ((i, par) in items.withIndex()) {
            val col = LinearLayout(this)
            col.orientation = LinearLayout.VERTICAL
            col.gravity = Gravity.CENTER
            col.setPadding(0, ui.dp(6), 0, ui.dp(6))
            val ic = ui.texto(par.first, 20f)
            ic.gravity = Gravity.CENTER
            val et = ui.texto(par.second, 12f, Paleta.APAGADO, true)
            et.gravity = Gravity.CENTER
            col.addView(ic, ui.params(ancho = ViewGroup.LayoutParams.MATCH_PARENT))
            col.addView(et, ui.params(ancho = ViewGroup.LayoutParams.MATCH_PARENT))
            col.setOnClickListener { mostrar(i) }
            iconos.add(ic)
            botones.add(et)
            b.addView(col, ui.params(ancho = 0, peso = 1f))
        }
        return b
    }

    private fun mostrar(i: Int) {
        actual = i
        if (::escanear.isInitialized) { escanear.repintarRed(); subdominios.repintarRed() }
        for ((k, p) in paginas.withIndex()) p.visibility = if (k == i) View.VISIBLE else View.GONE
        for ((k, t) in botones.withIndex()) {
            val activo = k == i
            t.setTextColor(if (activo) Paleta.CYAN else Paleta.APAGADO)
            (t.parent as View).background = if (activo) ui.fondo(Paleta.TARJETA_2, 14) else null
            iconos[k].alpha = if (activo) 1f else 0.55f
        }
    }

    private fun paginaInfo(): ScrollView {
        val sv = ScrollView(this)
        val col = ui.vertical()
        col.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(24))

        fun seccion(titulo: String, cuerpo: String, extra: View? = null) {
            val t = ui.tarjeta()
            t.addView(ui.texto(titulo, 16f, Paleta.TEXTO, true))
            t.addView(ui.texto(cuerpo, 13.5f, Paleta.APAGADO), ui.params(arriba = 8))
            if (extra != null) t.addView(extra, ui.params(arriba = 10))
            col.addView(t, ui.params(abajo = 12))
        }

        seccion("¿Qué hace Zumo Port?",
            "• Escanea una IP, un rango (192.168.1.1-254) o un bloque (10.0.0.0/24) y te muestra qué puertos tiene abiertos.\n" +
            "• Para los puertos web (80, 443, 8080…) te dice si el sitio contesta bien (HTTP/HTTPS 200, redirecciones, errores) y qué servidor es.\n" +
            "• Busca los subdominios de un dominio en certificados públicos, en HackerTarget y probando nombres comunes, y te dice a qué IP apunta cada uno.\n" +
            "• Desde un subdominio, un toque y escaneás sus puertos.")

        val colores = ui.vertical()
        val filas = listOf(
            Paleta.VERDE to "Verde: el puerto contesta bien (HTTP 2xx o 3xx).",
            Paleta.AMARILLO to "Amarillo: contesta pero con error del cliente (404, 403…).",
            Paleta.NARANJA to "Naranja: contesta con error del servidor (500…).",
            Paleta.CYAN to "Celeste: puerto abierto (puede tener banner, TLS o no hablar web).",
        )
        for ((c, t) in filas) {
            val f = ui.horizontal()
            f.addView(ui.insignia("●", c), ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 8))
            f.addView(ui.texto(t, 13f, Paleta.TEXTO), ui.params(ancho = 0, peso = 1f))
            colores.addView(f, ui.params(abajo = 6))
        }
        seccion("Cómo leer los colores", "Cada puerto abierto lleva una etiqueta con su número:", colores)

        seccion("Consejos",
            "• Para una red de tu casa, tocá «Mi red» y escaneá con velocidad Normal.\n" +
            "• Para un dominio, buscá primero los subdominios y después escaneá los que respondan.\n" +
            "• Con datos móviles, algunas operadoras limitan o cortan los escaneos grandes: probá con WiFi.\n" +
            "• Dejá la pantalla encendida mientras escanea: la app la mantiene prendida sola.")

        seccion("Uso responsable",
            "Escaneá solo equipos, redes y dominios tuyos o con permiso de su dueño. Escanear los de otras personas sin autorización puede ser ilegal y los administradores lo detectan.")

        sv.addView(col)
        return sv
    }

    override fun onDestroy() {
        escanear.cerrar()
        subdominios.cerrar()
        super.onDestroy()
    }
}
