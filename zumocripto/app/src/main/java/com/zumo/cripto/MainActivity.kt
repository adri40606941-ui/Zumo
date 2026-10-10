package com.zumo.cripto

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

/** Zumo Cripto: compara el precio de cada cripto entre varios exchanges (datos de CoinGecko). Todo en código: cabecera, dos pestañas abajo y sus páginas. */
class MainActivity : Activity() {
    private lateinit var ui: Ui
    private lateinit var oportunidades: PaginaOportunidades
    private lateinit var info: ScrollView
    private val paginas = ArrayList<View>()
    private val botones = ArrayList<TextView>()
    private val iconos = ArrayList<TextView>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Paleta.FONDO
        window.navigationBarColor = Paleta.TARJETA
        ui = Ui(this)

        oportunidades = PaginaOportunidades(this)
        info = paginaInfo()

        val raiz = LinearLayout(this)
        raiz.orientation = LinearLayout.VERTICAL
        raiz.setBackgroundColor(Paleta.FONDO)
        raiz.addView(cabecera())

        val contenido = FrameLayout(this)
        for (p in listOf<View>(oportunidades.vista, info)) {
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
        fila.addView(ui.texto("💹", 30f), ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 12))
        val tit = ui.vertical()
        val nombre = ui.texto("ZUMO CRIPTO", 22f, Color.WHITE, true)
        nombre.letterSpacing = 0.12f
        tit.addView(nombre)
        tit.addView(ui.texto("Compará precios entre exchanges", 12f, Paleta.CYAN))
        fila.addView(tit, ui.params(ancho = 0, peso = 1f))
        c.addView(fila)
        return c
    }

    private fun barraInferior(): View {
        val b = LinearLayout(this)
        b.orientation = LinearLayout.HORIZONTAL
        b.setBackgroundColor(Paleta.TARJETA)
        b.setPadding(ui.dp(6), ui.dp(6), ui.dp(6), ui.dp(6))
        val items = listOf("💹" to "Oportunidades", "ℹ️" to "Info")
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

        fun seccion(titulo: String, cuerpo: String) {
            val t = ui.tarjeta()
            t.addView(ui.texto(titulo, 16f, Paleta.TEXTO, true))
            t.addView(ui.texto(cuerpo, 13.5f, Paleta.APAGADO), ui.params(arriba = 8))
            col.addView(t, ui.params(abajo = 12))
        }

        seccion("¿Qué hace Zumo Cripto?",
            "• Recorre el ranking de criptomonedas de CoinGecko (podés elegir cuántas, de las 50 más grandes hasta las 500).\n" +
            "• Para cada una, compara su precio en varios exchanges y calcula el margen entre el más barato y el más caro.\n" +
            "• Te muestra solo las que superan el margen mínimo que pusiste, ordenadas de mayor a menor diferencia.\n" +
            "• Tocando una, ves el precio en cada exchange y un enlace para operar si CoinGecko lo tiene.")

        seccion("Sobre los datos",
            "Los precios vienen de la API pública de CoinGecko. Es gratuita pero tiene un límite de pedidos por minuto compartido por todos los que la usan sin clave; por eso la búsqueda pide de a una cripto y espera un poco entre cada una, y puede tardar varios minutos si elegís muchas.")

        seccion("Antes de operar con esto",
            "• Esto no es consejo financiero: es solo información para que decidas vos.\n" +
            "• El margen mostrado es antes de comisiones, retiros y de mover la cripto entre exchanges, que pueden achicarlo o anularlo del todo.\n" +
            "• Los precios pueden cambiar en los segundos que tarda el pedido, y hay exchanges con poco volumen donde el precio no es confiable.\n" +
            "• Revisá siempre el precio real en cada exchange antes de mover dinero.")

        seccion("Atribución", "Datos de precios y mercados de CoinGecko (www.coingecko.com).")

        sv.addView(col)
        return sv
    }

    override fun onDestroy() {
        oportunidades.cerrar()
        super.onDestroy()
    }
}
