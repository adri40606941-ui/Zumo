package com.zumo.port

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/** Colores y piezas de pantalla que comparten todas las pestañas (todo se arma en código, sin XML). */
object Paleta {
    val FONDO = Color.parseColor("#0A0F24")
    val TARJETA = Color.parseColor("#141B3A")
    val TARJETA_2 = Color.parseColor("#1C2552")
    val BORDE = Color.parseColor("#2A3570")
    val CYAN = Color.parseColor("#22D3EE")
    val VIOLETA = Color.parseColor("#8B5CF6")
    val VERDE = Color.parseColor("#22C55E")
    val AMARILLO = Color.parseColor("#FACC15")
    val NARANJA = Color.parseColor("#FB923C")
    val ROJO = Color.parseColor("#F87171")
    val TEXTO = Color.parseColor("#E8ECFA")
    val APAGADO = Color.parseColor("#8E99C4")
}

class Ui(private val ctx: Context) {
    private val densidad = ctx.resources.displayMetrics.density

    fun dp(v: Int): Int = (v * densidad + 0.5f).toInt()

    fun fondo(color: Int, radio: Int, borde: Int = 0): GradientDrawable {
        val g = GradientDrawable()
        g.setColor(color)
        g.cornerRadius = dp(radio).toFloat()
        if (borde != 0) g.setStroke(dp(1), borde)
        return g
    }

    fun degradado(a: Int, b: Int, radio: Int): GradientDrawable {
        val g = GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, intArrayOf(a, b))
        g.cornerRadius = dp(radio).toFloat()
        return g
    }

    fun texto(t: String, tam: Float = 14f, color: Int = Paleta.TEXTO, negrita: Boolean = false): TextView {
        val v = TextView(ctx)
        v.text = t
        v.textSize = tam
        v.setTextColor(color)
        if (negrita) v.setTypeface(Typeface.DEFAULT, Typeface.BOLD)
        return v
    }

    fun vertical(): LinearLayout {
        val l = LinearLayout(ctx)
        l.orientation = LinearLayout.VERTICAL
        return l
    }

    fun horizontal(): LinearLayout {
        val l = LinearLayout(ctx)
        l.orientation = LinearLayout.HORIZONTAL
        l.gravity = Gravity.CENTER_VERTICAL
        return l
    }

    fun tarjeta(): LinearLayout {
        val l = vertical()
        l.background = fondo(Paleta.TARJETA, 18, Paleta.BORDE)
        l.setPadding(dp(14), dp(14), dp(14), dp(14))
        return l
    }

    fun params(ancho: Int = ViewGroup.LayoutParams.MATCH_PARENT, alto: Int = ViewGroup.LayoutParams.WRAP_CONTENT,
               izq: Int = 0, arriba: Int = 0, der: Int = 0, abajo: Int = 0, peso: Float = 0f): LinearLayout.LayoutParams {
        val p = LinearLayout.LayoutParams(ancho, alto, peso)
        p.setMargins(dp(izq), dp(arriba), dp(der), dp(abajo))
        return p
    }

    /** Un cuadro de texto de una línea con el estilo de la app. */
    fun campo(pista: String): EditText {
        val e = EditText(ctx)
        e.hint = pista
        e.setHintTextColor(Paleta.APAGADO)
        e.setTextColor(Paleta.TEXTO)
        e.textSize = 15f
        e.setSingleLine(true)
        e.background = fondo(Paleta.TARJETA_2, 12, Paleta.BORDE)
        e.setPadding(dp(14), dp(12), dp(14), dp(12))
        return e
    }

    /** Botón redondeado; [relleno] lo pinta con degradado, si no es un botón de borde. */
    fun boton(t: String, relleno: Boolean = true, alTocar: () -> Unit): TextView {
        val b = texto(t, 15f, if (relleno) Color.parseColor("#06101F") else Paleta.CYAN, true)
        b.gravity = Gravity.CENTER
        b.setPadding(dp(16), dp(13), dp(16), dp(13))
        b.background = if (relleno) degradado(Paleta.CYAN, Paleta.VIOLETA, 14) else fondo(Color.TRANSPARENT, 14, Paleta.CYAN)
        b.setOnClickListener { alTocar() }
        return b
    }

    /** Etiqueta que se puede marcar (los puertos elegidos, los filtros). */
    fun chip(t: String, marcado: Boolean, alTocar: () -> Unit): TextView {
        val c = texto(t, 13f, Paleta.TEXTO)
        c.gravity = Gravity.CENTER
        c.setPadding(dp(12), dp(8), dp(12), dp(8))
        pintarChip(c, marcado)
        c.setOnClickListener { alTocar() }
        return c
    }

    fun pintarChip(c: TextView, marcado: Boolean) {
        c.background = if (marcado) degradado(Paleta.VIOLETA, Paleta.CYAN, 20) else fondo(Paleta.TARJETA_2, 20, Paleta.BORDE)
        c.setTextColor(if (marcado) Color.parseColor("#06101F") else Paleta.TEXTO)
        c.setTypeface(Typeface.DEFAULT, if (marcado) Typeface.BOLD else Typeface.NORMAL)
    }

    /** Etiquetita de color para el estado de un puerto: verde si responde bien, amarillo/naranja si no. */
    fun insignia(t: String, color: Int): TextView {
        val v = texto(t, 12f, Color.parseColor("#06101F"), true)
        v.setPadding(dp(9), dp(4), dp(9), dp(4))
        v.background = fondo(color, 10)
        return v
    }

    fun separador(): View {
        val v = View(ctx)
        v.setBackgroundColor(Paleta.BORDE)
        v.layoutParams = params(ViewGroup.LayoutParams.MATCH_PARENT, 1, 0, 10, 0, 10)
        return v
    }
}

/** Barra de progreso hecha con dos Views (la de relleno y la que falta), así no depende de estilos del sistema. */
class Barra(ctx: Context, private val ui: Ui) : LinearLayout(ctx) {
    private val lleno = View(ctx)
    private val vacio = View(ctx)

    init {
        orientation = LinearLayout.HORIZONTAL
        background = ui.fondo(Paleta.TARJETA_2, 6)
        lleno.background = ui.degradado(Paleta.CYAN, Paleta.VIOLETA, 6)
        vacio.setBackgroundColor(Color.TRANSPARENT)
        addView(lleno, LinearLayout.LayoutParams(0, ui.dp(8), 0f))
        addView(vacio, LinearLayout.LayoutParams(0, ui.dp(8), 1f))
    }

    /** [fraccion] entre 0 y 1. */
    fun poner(fraccion: Double) {
        val f = fraccion.coerceIn(0.0, 1.0).toFloat()
        (lleno.layoutParams as LinearLayout.LayoutParams).weight = f
        (vacio.layoutParams as LinearLayout.LayoutParams).weight = 1f - f
        lleno.requestLayout()
        vacio.requestLayout()
    }
}
