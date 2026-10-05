package com.zumo.vpn

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*

/**
 * Pantalla mínima: conectar, vencimiento de la cuenta y un registro de lo que pasa al conectar.
 * El servidor, el payload y el usuario no se ven ni se editan: vienen dentro del archivo .zs que
 * genera el bot de Telegram y que se abre con esta app.
 */
class MainActivity : Activity() {

    private lateinit var prefs: Prefs
    private val h = Handler(Looper.getMainLooper())
    private lateinit var tvEstado: TextView
    private lateinit var tvError: TextView
    private lateinit var btn: Button
    private lateinit var puntoEstado: View
    private lateinit var tvVelocidad: TextView
    private lateinit var tvTiempo: TextView
    private lateinit var tvDatos: TextView
    private lateinit var cSinCuenta: LinearLayout
    private lateinit var tvVence: TextView
    private lateinit var tvVenceDetalle: TextView
    private lateinit var tvRegistro: TextView

    private val BG = Color.parseColor("#14102B")
    private val CARD = Color.parseColor("#201A3D")
    private val BORDE = Color.parseColor("#36305E")
    private val ACENTO = Color.parseColor("#B388FF")
    private val VERDE = Color.parseColor("#4CE0A8")
    private val ROJO = Color.parseColor("#FF6E6E")
    private val NARANJA = Color.parseColor("#FFB74D")
    private val TEXTO_SUAVE = Color.parseColor("#9D96C4")

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        prefs = Prefs(this)
        armarUi()
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 7)
        importarDesdeIntent(intent)
    }

    override fun onNewIntent(i: Intent) {
        super.onNewIntent(i)
        importarDesdeIntent(i)
    }

    override fun onResume() {
        super.onResume()
        refrescar()
        h.post(object : Runnable {
            override fun run() { refrescar(); h.postDelayed(this, 1000) }
        })
    }

    override fun onPause() {
        super.onPause()
        h.removeCallbacksAndMessages(null)
    }

    // ---------- interfaz ----------
    private fun texto(t: String, size: Float = 15f, color: Int = Color.WHITE, bold: Boolean = false): TextView =
        TextView(this).apply {
            text = t; textSize = size; setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    private fun redondo(c: Int, radio: Int = 16, trazo: Int = 0, colorTrazo: Int = BORDE) =
        android.graphics.drawable.GradientDrawable().apply {
            setColor(c); cornerRadius = dp(radio).toFloat()
            if (trazo > 0) setStroke(dp(trazo), colorTrazo)
        }

    /** Botón principal: fondo sólido, texto oscuro, bien visible. */
    private fun botonPrimario(t: String, color: Int, onClick: () -> Unit): Button =
        Button(this).apply {
            text = t; isAllCaps = false; setTextColor(Color.parseColor("#0F0B21")); textSize = 16f
            setTypeface(typeface, Typeface.BOLD)
            stateListAnimator = null
            background = redondo(color, 16)
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52)).apply { topMargin = dp(10) }
        }

    /** Botón secundario: solo borde, fondo transparente. Menos protagonismo que el principal. */
    private fun botonSecundario(t: String, color: Int = ACENTO, onClick: () -> Unit): Button =
        Button(this).apply {
            text = t; isAllCaps = false; setTextColor(color); textSize = 14.5f
            stateListAnimator = null
            background = redondo(Color.TRANSPARENT, 14, trazo = 1, colorTrazo = color)
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46)).apply { topMargin = dp(8) }
        }

    private fun tarjeta(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = redondo(CARD, 20, trazo = 1)
        setPadding(dp(18), dp(16), dp(18), dp(16))
        elevation = dp(2).toFloat()
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(14) }
    }

    /** Encabezado de sección dentro de una tarjeta: ícono + título. */
    private fun seccion(cont: LinearLayout, icono: String, titulo: String) {
        val fila = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        fila.addView(texto(icono, 17f))
        fila.addView(texto(titulo, 15.5f, ACENTO, true).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(8) }
        })
        cont.addView(fila)
        cont.addView(View(this).apply { layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(12)) })
    }

    private fun armarUi() {
        val root = ScrollView(this).apply { setBackgroundColor(BG); isFillViewport = true }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(40), dp(18), dp(28))
        }
        root.addView(col)

        // encabezado: título centrado + botón de menú (☰) en la esquina
        val filaCab = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val espaciador = View(this).apply { layoutParams = LinearLayout.LayoutParams(dp(44), dp(1)) }
        val cab = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        cab.addView(texto("🛡", 34f).apply { gravity = Gravity.CENTER })
        cab.addView(texto("ZUMO VPN", 25f, Color.WHITE, true).apply { gravity = Gravity.CENTER; letterSpacing = 0.03f })
        cab.addView(texto("Conexión privada y estable", 13f, TEXTO_SUAVE).apply {
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(2) }
        })
        val btnMenu = TextView(this).apply {
            text = "☰"; textSize = 20f; setTextColor(ACENTO); gravity = Gravity.CENTER
            background = redondo(CARD, 14, trazo = 1)
            layoutParams = LinearLayout.LayoutParams(dp(44), dp(44))
            setOnClickListener { abrirMenu() }
        }
        filaCab.addView(espaciador); filaCab.addView(cab); filaCab.addView(btnMenu)
        col.addView(filaCab)

        // sin cuenta cargada: solo se pide abrir el .zs
        cSinCuenta = tarjeta()
        seccion(cSinCuenta, "📥", "Falta tu cuenta")
        cSinCuenta.addView(texto("Abrí con esta app el archivo .zs que te pasaron, o elegilo desde acá.", 13.5f, TEXTO_SUAVE))
        cSinCuenta.addView(botonPrimario("Importar archivo .zs", ACENTO) { elegirArchivo() })
        col.addView(cSinCuenta)

        // estado de la conexión
        val cEstado = tarjeta().apply { gravity = Gravity.CENTER_HORIZONTAL }
        puntoEstado = View(this).apply {
            layoutParams = LinearLayout.LayoutParams(dp(10), dp(10))
            background = redondo(ROJO, 10)
        }
        val filaEstado = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        filaEstado.addView(puntoEstado)
        tvEstado = texto("Desconectado", 20f, Color.WHITE, true).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(10) }
        }
        filaEstado.addView(tvEstado)
        cEstado.addView(filaEstado)
        tvError = texto("", 12.5f, NARANJA).apply {
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) }
        }
        cEstado.addView(tvError)
        btn = botonPrimario("Conectar", VERDE) { alternar() }
        cEstado.addView(btn)
        col.addView(cEstado)

        // vencimiento de la cuenta
        val cVence = tarjeta()
        seccion(cVence, "📅", "Tu cuenta")
        tvVence = texto("--", 17f, Color.WHITE, true)
        tvVenceDetalle = texto("", 13f, TEXTO_SUAVE).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(2) }
        }
        cVence.addView(tvVence); cVence.addView(tvVenceDetalle)
        col.addView(cVence)

        // velocidad, tiempo conectado y datos usados
        val cStats = tarjeta()
        seccion(cStats, "📊", "Conexión")
        val filaStats = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        fun columnaStat(titulo: String): TextView {
            val colStat = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            colStat.addView(texto(titulo, 11.5f, TEXTO_SUAVE).apply { gravity = Gravity.CENTER })
            val valor = texto("--", 15.5f, Color.WHITE, true).apply {
                gravity = Gravity.CENTER
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(4) }
            }
            colStat.addView(valor)
            filaStats.addView(colStat)
            return valor
        }
        tvVelocidad = columnaStat("Velocidad")
        tvTiempo = columnaStat("Conectado hace")
        tvDatos = columnaStat("Datos usados")
        cStats.addView(filaStats)
        col.addView(cStats)

        // registro del proceso de conexión
        val cReg = tarjeta()
        seccion(cReg, "📝", "Registro")
        // pulsación larga en el título: copia el registro con el detalle técnico del último error
        (cReg.getChildAt(0) as ViewGroup).setOnLongClickListener {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("Registro", Registro.texto(80) + "\n\n" + Registro.detalle))
            aviso("Registro técnico copiado"); true
        }
        tvRegistro = texto("", 12f, TEXTO_SUAVE).apply {
            typeface = Typeface.MONOSPACE
            minLines = 4
            background = redondo(Color.parseColor("#2E2854"), 10)
            setPadding(dp(10), dp(8), dp(10), dp(8))
        }
        cReg.addView(tvRegistro)
        cReg.addView(botonSecundario("📋  Copiar registro") {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("Registro", Registro.texto(80)))
            aviso("Registro copiado")
        })
        col.addView(cReg)

        setContentView(root)
    }

    /** Menú ☰: importar una cuenta nueva (renovación) y la guía de batería. */
    private fun abrirMenu() {
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        val cCuenta = tarjeta()
        seccion(cCuenta, "📥", "Cuenta")
        cCuenta.addView(texto("¿Te mandaron un archivo .zs nuevo (renovación u otra cuenta)? Importalo acá.", 13f, TEXTO_SUAVE))
        cCuenta.addView(botonSecundario("📂  Importar archivo .zs") { elegirArchivo() })
        col.addView(cCuenta)

        val cEst = tarjeta()
        seccion(cEst, "⚙️", "Evitar desconexiones")
        val sw = Switch(this).apply {
            text = "Reconectar al encender el teléfono"; setTextColor(Color.WHITE); isChecked = prefs.autoStart
            setOnCheckedChangeListener { _, on -> prefs.autoStart = on }
        }
        cEst.addView(sw)
        cEst.addView(botonSecundario("🔋  Guía para evitar cortes de batería", NARANJA) { guiaBateria() })
        col.addView(cEst)

        val sv = ScrollView(this).apply { addView(col) }
        dialogo("Configuración", sv).setPositiveButton("Cerrar", null).mostrar()
    }

    private fun refrescar() {
        val tieneCuenta = prefs.config?.valida() == true && prefs.user.isNotBlank()
        cSinCuenta.visibility = if (tieneCuenta) View.GONE else View.VISIBLE

        val corr = ZumoVpnService.corriendo
        val con = ZumoVpnService.conectado
        tvEstado.text = ZumoVpnService.estado
        val colorEstado = when {
            con -> VERDE
            corr -> NARANJA
            else -> ROJO
        }
        tvEstado.setTextColor(colorEstado)
        puntoEstado.background = redondo(colorEstado, 10)
        tvError.text = when {
            con -> ""
            corr || ZumoVpnService.estado == "Error" -> ZumoVpnService.ultimoError
            else -> ""
        }
        btn.text = if (corr) "◼  Desconectar" else "▶  Conectar"
        btn.background = redondo(if (corr) ROJO else VERDE, 16)

        // vencimiento
        val exp = prefs.exp
        val dias = Perfil.diasRestantes(exp)
        when {
            !tieneCuenta -> { tvVence.text = "--"; tvVence.setTextColor(Color.WHITE); tvVenceDetalle.text = "" }
            exp.isBlank() || dias == null -> { tvVence.text = "Sin vencimiento"; tvVence.setTextColor(VERDE); tvVenceDetalle.text = "" }
            Perfil.vencida(exp) -> {
                tvVence.text = "Vencida el ${Perfil.fechaLinda(exp)}"; tvVence.setTextColor(ROJO)
                tvVenceDetalle.text = "Pedí la renovación y abrí el archivo .zs nuevo."
            }
            else -> {
                val d = dias ?: 0
                tvVence.text = "Vence el ${Perfil.fechaLinda(exp)}"
                tvVence.setTextColor(if (d <= 3) NARANJA else VERDE)
                tvVenceDetalle.text = when (d) { 0 -> "Vence hoy a las ${Perfil.HORA_CORTE}:00"; 1 -> "Falta 1 día"; else -> "Faltan $d días" }
            }
        }

        tvVelocidad.text = if (con && ZumoVpnService.velocidad.isNotBlank()) ZumoVpnService.velocidad else "--"
        tvDatos.text = if (con && ZumoVpnService.datosUsados.isNotBlank()) ZumoVpnService.datosUsados else "--"
        tvTiempo.text = if (con && ZumoVpnService.desde > 0) formatearDuracion(System.currentTimeMillis() - ZumoVpnService.desde) else "--"

        tvRegistro.text = Registro.texto(12).ifBlank { "Todavía no hay actividad." }
    }

    private fun formatearDuracion(ms: Long): String {
        val s = ms / 1000
        val hh = s / 3600; val mm = (s % 3600) / 60; val ss = s % 60
        return if (hh > 0) "%d:%02d:%02d".format(hh, mm, ss) else "%02d:%02d".format(mm, ss)
    }

    // ---------- acciones ----------
    private fun alternar() {
        if (ZumoVpnService.corriendo) {
            prefs.wanted = false
            ZumoVpnService.detener(this)
            Registro.add("Desconectado")
            return
        }
        val c = prefs.config
        if (c == null || !c.valida() || prefs.user.isBlank()) { aviso("Primero importá el archivo .zs de tu cuenta"); return }
        if (Perfil.vencida(prefs.exp)) {
            Registro.add("✘ Tu cuenta venció el ${Perfil.fechaLinda(prefs.exp)}. Pedí la renovación.")
            aviso("Tu cuenta está vencida")
            return
        }
        // La primera vez, se pide quedar fuera del ahorro de batería antes de conectar (si no, el
        // sistema puede cerrar la VPN sola al rato, sobre todo en Tecno, Xiaomi y similares).
        if (!prefs.pidioBateria && !PowerGuide.sinOptimizar(this)) {
            prefs.pidioBateria = true
            AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                .setTitle("Antes de conectar")
                .setMessage("Para que la VPN no se corte sola, permití que quede fuera del ahorro de batería.")
                .setPositiveButton("Permitir") { _, _ -> PowerGuide.pedirExclusion(this); aviso("Listo, ahora tocá Conectar de nuevo") }
                .setNegativeButton("Ahora no") { _, _ -> conectarDeVerdad() }
                .mostrar()
            return
        }
        conectarDeVerdad()
    }

    private fun conectarDeVerdad() {
        prefs.wanted = true
        val i = VpnService.prepare(this)
        if (i != null) startActivityForResult(i, 1) else ZumoVpnService.iniciar(this)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(req: Int, res: Int, data: Intent?) {
        super.onActivityResult(req, res, data)
        if (req == 1 && res == RESULT_OK) ZumoVpnService.iniciar(this)
        if (req == 2 && res == RESULT_OK) data?.data?.let { leerArchivo(it) }
    }

    private fun aviso(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()

    /** Diálogo con la misma paleta oscura de la app (el tema del sistema es claro por defecto). */
    private fun dialogo(titulo: String, vista: View): AlertDialog.Builder =
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle(titulo).setView(vista)

    /** Muestra el diálogo con fondo redondeado del color de las tarjetas (en vez de .show() directo). */
    private fun AlertDialog.Builder.mostrar() {
        val d = create()
        d.setOnShowListener { d.window?.setBackgroundDrawable(redondo(CARD, 18)) }
        d.show()
    }

    // ---------- importar .zs ----------
    private fun importarDesdeIntent(i: Intent?) {
        val uri = i?.data ?: return
        if (uri.scheme == "content" || uri.scheme == "file") leerArchivo(uri)
    }

    private fun elegirArchivo() {
        try {
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"), 2)
        } catch (e: Exception) {
            aviso("No se pudo abrir el selector de archivos")
        }
    }

    private fun leerArchivo(uri: Uri) {
        try {
            val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() }
            val p = bytes?.let { Zs.descifrar(it) }
            if (p == null) { aviso("El archivo no es una cuenta válida de Zumo VPN"); Registro.add("✘ Archivo .zs no válido"); return }
            guardarCuenta(p)
        } catch (e: Exception) {
            aviso("No se pudo leer el archivo")
        }
    }

    private fun guardarCuenta(p: Perfil) {
        val cambioDeCuenta = prefs.user != p.user || prefs.config?.host != p.cfg.host
        prefs.config = p.cfg
        prefs.user = p.user; prefs.pass = p.pass
        prefs.exp = p.exp
        Registro.add(if (cambioDeCuenta) "✔ Cuenta importada" else "✔ Cuenta actualizada")
        aviso(if (Perfil.vencida(p.exp)) "Cuenta cargada, pero ya está vencida" else "Cuenta cargada")
        // si la VPN estaba corriendo con la cuenta anterior, se reinicia para usar la nueva
        if (ZumoVpnService.corriendo) { prefs.wanted = false; ZumoVpnService.detener(this) }
        refrescar()
    }

    private fun guiaBateria() {
        val ok = PowerGuide.sinOptimizar(this)
        val msg = (if (PowerGuide.esTecno) "Detecté un teléfono Tecno/Infinix/itel: son los que más cierran apps.\n\n" else "") +
            (if (ok) "✔ Batería: sin límite para esta app.\n\n" else "✘ Batería: el sistema todavía puede cerrarla.\n\n") + PowerGuide.pasos
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
            .setTitle("Evitar desconexiones").setMessage(msg)
            .setPositiveButton("Quitar límite de batería") { _, _ -> PowerGuide.pedirExclusion(this) }
            .setNeutralButton("Abrir autoinicio") { _, _ -> PowerGuide.abrirAutoinicio(this) }
            .setNegativeButton("Cerrar", null).show()
    }
}
