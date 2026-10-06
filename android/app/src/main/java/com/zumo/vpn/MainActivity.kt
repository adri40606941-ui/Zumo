package com.zumo.vpn

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.Outline
import android.graphics.Typeface
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.ViewOutlineProvider
import android.view.inputmethod.InputMethodManager
import android.widget.*

/**
 * Pantalla mínima: cuenta, conectar, vencimiento y un registro de lo que pasa al conectar.
 * La cuenta se carga de dos formas:
 *  - eligiendo un servidor de la lista que trae la app (ver [Servidores]) y poniendo usuario y
 *    contraseña; el host y el payload de cada servidor no se ven en pantalla;
 *  - abriendo el archivo .zs que genera el bot de Telegram (trae servidor, payload, usuario,
 *    clave y vencimiento, y nada de eso se ve ni se edita).
 * Nombre, colores, fondo, letra y secciones salen del tema de la app (ver [Tema]).
 */
class MainActivity : Activity() {

    private lateinit var prefs: Prefs
    private val h = Handler(Looper.getMainLooper())
    private lateinit var tvEstado: TextView
    private lateinit var tvError: TextView
    private lateinit var btn: Button
    private var btnActualizar: TextView? = null
    @Volatile private var actualizando = false
    private lateinit var puntoEstado: View
    private lateinit var tvVelocidad: TextView
    private lateinit var tvTiempo: TextView
    private lateinit var tvDatos: TextView
    private lateinit var cSinCuenta: LinearLayout
    private lateinit var cCuenta: LinearLayout       // servidor de la lista + usuario y contraseña
    private lateinit var tvServidor: TextView
    private lateinit var tvCuentaZs: TextView
    private lateinit var cajaLogin: LinearLayout
    private lateinit var etUser: EditText
    private lateinit var etPass: EditText
    private lateinit var cVence: LinearLayout
    private var cargando = false                      // se están poniendo textos por código: no guardar
    private var claveVisible = false
    private var servidores: List<Config> = emptyList()
    private lateinit var tvVence: TextView
    private lateinit var tvVenceDetalle: TextView

    private val tema: Tema by lazy { Tema.actual(this) }
    private val BG get() = tema.fondo
    private val CARD get() = tema.tarjeta
    private val BORDE get() = tema.borde
    private val ACENTO get() = tema.acento
    private val VERDE get() = tema.conectar
    private val ROJO get() = tema.desconectar
    private val NARANJA get() = tema.aviso
    private val TEXTO get() = tema.texto
    private val TEXTO_SUAVE get() = tema.suave
    private val CAMPO get() = tema.campo
    private val SOBRE get() = tema.sobreBoton

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    /** Tamaño de letra según el tema (100 % = el de siempre). */
    private fun sp(v: Float) = v * tema.escala / 100f

    /** Radio de esquina proporcional al de las tarjetas del tema. */
    private fun radio(factor: Float) = (tema.radio * factor + 0.5f).toInt()

    private fun letra(negrita: Boolean): Typeface = Typeface.create(tema.fuente, if (negrita) Typeface.BOLD else Typeface.NORMAL)

    /** Tarjetas y campos translúcidos cuando el tema lo pide (para que se vea el fondo). */
    private fun conOpacidad(c: Int) =
        if (tema.opacidad >= 100) c else (c and 0x00FFFFFF) or ((255 * tema.opacidad / 100) shl 24)

    /** Los diálogos del sistema traen letra clara u oscura según el tema: se elige por el color de las tarjetas. */
    private val estiloDialogo get() =
        if (Tema.esClaro(CARD)) android.R.style.Theme_Material_Light_Dialog_Alert else android.R.style.Theme_Material_Dialog_Alert

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        prefs = Prefs(this)
        servidores = Servidores.lista(this)
        armarUi()
        cargarCuenta()
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
    private fun texto(t: String, size: Float = 15f, color: Int = TEXTO, bold: Boolean = false): TextView =
        TextView(this).apply {
            text = t; textSize = sp(size); setTextColor(color)
            typeface = letra(bold)
        }

    private fun redondo(c: Int, radio: Int = 16, trazo: Int = 0, colorTrazo: Int = BORDE) =
        android.graphics.drawable.GradientDrawable().apply {
            setColor(c); cornerRadius = dp(radio).toFloat()
            if (trazo > 0) setStroke(dp(trazo), colorTrazo)
        }

    /** Botón principal: fondo sólido, texto oscuro, bien visible. */
    private fun botonPrimario(t: String, color: Int, onClick: () -> Unit): Button =
        Button(this).apply {
            text = t; isAllCaps = false; setTextColor(SOBRE); textSize = sp(16f)
            typeface = letra(true)
            stateListAnimator = null
            background = redondo(color, radio(0.8f))
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52)).apply { topMargin = dp(10) }
        }

    /** Botón secundario: solo borde, fondo transparente. Menos protagonismo que el principal. */
    private fun botonSecundario(t: String, color: Int = ACENTO, onClick: () -> Unit): Button =
        Button(this).apply {
            text = t; isAllCaps = false; setTextColor(color); textSize = sp(14.5f)
            typeface = letra(false)
            stateListAnimator = null
            background = redondo(Color.TRANSPARENT, radio(0.7f), trazo = 1, colorTrazo = color)
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46)).apply { topMargin = dp(8) }
        }

    private fun tarjeta(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = redondo(conOpacidad(CARD), tema.radio, trazo = 1)
        setPadding(dp(18), dp(16), dp(18), dp(16))
        if (tema.opacidad >= 100) elevation = dp(2).toFloat()   // la sombra se vería a través de una tarjeta translúcida
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

    /** Campo de texto con la paleta de la app. Sin autocorrector (usuarios y claves no son palabras). */
    private fun campo(pista: String, clave: Boolean): EditText = EditText(this).apply {
        hint = pista; setHintTextColor(TEXTO_SUAVE); setTextColor(TEXTO); textSize = sp(15.5f)
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or
            (if (clave) InputType.TYPE_TEXT_VARIATION_PASSWORD else InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD)
        typeface = letra(false)   // después del inputType: los campos de clave lo cambian a monoespaciada
        maxLines = 1
        background = redondo(conOpacidad(CAMPO), radio(0.6f), trazo = 1)
        setPadding(dp(14), dp(12), dp(14), dp(12))
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(10) }
    }

    private fun alCambiar(et: EditText, guardar: (String) -> Unit) {
        et.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) { if (!cargando) guardar(s?.toString() ?: "") }
        })
    }

    private fun armarUi() {
        ponerFondo()
        val root = ScrollView(this).apply { isFillViewport = true }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(40), dp(18), dp(28))
            isFocusableInTouchMode = true   // que al abrir no salte el teclado por los campos de la cuenta
        }
        root.addView(col)

        // encabezado: título centrado
        val filaCab = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val cab = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        // logo: el ícono importado (si el tema lo pide) o el emoji
        val fotoLogo = if (tema.logoImagen) Tema.logoImagen(this) else null
        if (fotoLogo != null) {
            cab.addView(ImageView(this).apply {
                setImageBitmap(fotoLogo)
                scaleType = ImageView.ScaleType.FIT_CENTER
                outlineProvider = object : ViewOutlineProvider() {
                    override fun getOutline(v: View, o: Outline) { o.setRoundRect(0, 0, v.width, v.height, dp(14).toFloat()) }
                }
                clipToOutline = true
                layoutParams = LinearLayout.LayoutParams(dp(64), dp(64)).apply { bottomMargin = dp(8) }
            })
        } else if (tema.logo.isNotBlank()) {
            cab.addView(texto(tema.logo, 34f).apply { gravity = Gravity.CENTER })
        }
        cab.addView(texto(tema.titulo, 25f, TEXTO, true).apply { gravity = Gravity.CENTER; letterSpacing = 0.03f })
        if (tema.lema.isNotBlank()) cab.addView(texto(tema.lema, 13f, TEXTO_SUAVE).apply {
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(2) }
        })
        // botón ↻ arriba a la derecha: baja la lista de servidores actualizada desde internet.
        // Solo aparece si la app se compiló con una URL de actualización (desde el centro).
        if (Servidores.urlActualizar(this).isNotBlank()) {
            btnActualizar = texto("↻", 22f, ACENTO, true).apply {
                gravity = Gravity.CENTER
                background = redondo(conOpacidad(CAMPO), radio(0.9f), trazo = 1, colorTrazo = BORDE)
                layoutParams = LinearLayout.LayoutParams(dp(44), dp(44))
                setOnClickListener { actualizarServidores() }
            }
            // espaciador a la izquierda del mismo ancho, para que el título quede centrado
            filaCab.addView(View(this).apply { layoutParams = LinearLayout.LayoutParams(dp(44), dp(44)) })
            filaCab.addView(cab)
            filaCab.addView(btnActualizar)
        } else {
            filaCab.addView(cab)
        }
        col.addView(filaCab)

        // app sin servidores adentro y sin cuenta cargada: solo se pide abrir el .zs
        cSinCuenta = tarjeta()
        seccion(cSinCuenta, "📥", "Falta tu cuenta")
        cSinCuenta.addView(texto("Abrí con esta app el archivo .zs que te pasaron, o elegilo desde acá.", 13.5f, TEXTO_SUAVE))
        cSinCuenta.addView(botonPrimario("Importar archivo .zs", ACENTO) { elegirArchivo() })
        col.addView(cSinCuenta)

        // cuenta: se elige un servidor de la lista de la app y se pone usuario y contraseña
        cCuenta = tarjeta()
        seccion(cCuenta, "🔑", "Tu cuenta")
        cCuenta.addView(texto("Servidor", 12f, TEXTO_SUAVE))
        tvServidor = texto("", 15.5f, TEXTO, true).apply {
            background = redondo(conOpacidad(CAMPO), radio(0.6f), trazo = 1)
            setPadding(dp(14), dp(13), dp(14), dp(13))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) }
            setOnClickListener { elegirServidor() }
        }
        cCuenta.addView(tvServidor)
        tvCuentaZs = texto("Cuenta cargada desde un archivo .zs. Para entrar con usuario y contraseña, elegí un servidor de la lista.", 12.5f, TEXTO_SUAVE).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(10) }
        }
        cCuenta.addView(tvCuentaZs)
        cajaLogin = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        etUser = campo("Usuario", clave = false)
        cajaLogin.addView(etUser)
        etPass = campo("Contraseña", clave = true).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val btnVer = TextView(this).apply {
            text = "👁"; textSize = 17f; gravity = Gravity.CENTER
            background = redondo(conOpacidad(CAMPO), radio(0.6f), trazo = 1)
            layoutParams = LinearLayout.LayoutParams(dp(48), ViewGroup.LayoutParams.MATCH_PARENT).apply { marginStart = dp(8) }
            setOnClickListener {
                // mostrar / ocultar la contraseña que el cliente está escribiendo
                claveVisible = !claveVisible
                etPass.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or
                    (if (claveVisible) InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD else InputType.TYPE_TEXT_VARIATION_PASSWORD)
                etPass.typeface = letra(false)
                etPass.setSelection(etPass.text.length)
            }
        }
        val filaPass = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(10) }
        }
        filaPass.addView(etPass); filaPass.addView(btnVer)
        cajaLogin.addView(filaPass)
        cCuenta.addView(cajaLogin)
        alCambiar(etUser) { prefs.user = it.trim() }
        alCambiar(etPass) { prefs.pass = it.trim() }
        col.addView(cCuenta)

        // estado de la conexión
        val cEstado = tarjeta().apply { gravity = Gravity.CENTER_HORIZONTAL }
        puntoEstado = View(this).apply {
            layoutParams = LinearLayout.LayoutParams(dp(10), dp(10))
            background = redondo(ROJO, 10)
        }
        val filaEstado = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        filaEstado.addView(puntoEstado)
        tvEstado = texto("Desconectado", 20f, TEXTO, true).apply {
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
        cVence = tarjeta()
        seccion(cVence, "📅", "Vencimiento")
        tvVence = texto("--", 17f, TEXTO, true)
        tvVenceDetalle = texto("", 13f, TEXTO_SUAVE).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(2) }
        }
        cVence.addView(tvVence); cVence.addView(tvVenceDetalle)
        if (tema.verVencimiento) col.addView(cVence)

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
            val valor = texto("--", 15.5f, TEXTO, true).apply {
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
        if (tema.verConexion) col.addView(cStats)

        // ajustes del teléfono que ayudan a conectar: DNS privado y batería
        val cTel = tarjeta()
        seccion(cTel, "📶", "Ajustes del teléfono")
        cTel.addView(texto("Si la VPN no conecta, desactivá el DNS privado y sacale el límite de batería a la app.", 12.5f, TEXTO_SUAVE).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(6) }
        })
        cTel.addView(botonPrimario("🌐  DNS privado", ACENTO) { abrirDnsPrivado() })
        cTel.addView(botonPrimario("🔋  Uso de batería", NARANJA) {
            PowerGuide.pedirExclusion(this)
            aviso(if (PowerGuide.sinOptimizar(this)) "La app ya está sin límite de batería" else "Permití \"Sin restricciones\" para esta app")
        })
        if (tema.verTelefono) col.addView(cTel)

        setContentView(root)
    }

    /**
     * Fondo de toda la ventana (también detrás de la barra de estado): la imagen del tema, un
     * degradado o un color liso. Va en la ventana y no en la lista para que no se mueva ni se
     * deforme cuando aparece el teclado.
     */
    private fun ponerFondo() {
        val foto = Tema.fondoImagen(this)
        val fondo2 = tema.fondo2
        window.setBackgroundDrawable(when {
            foto != null -> FondoFoto(foto, tema.velo)
            fondo2 != null -> GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(BG, fondo2))
            else -> ColorDrawable(BG)
        })
        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.TRANSPARENT
        // sobre un fondo claro, los íconos de las barras del sistema van oscuros
        var barras = 0
        if (foto == null && Tema.esClaro(BG)) barras = barras or View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        if (foto == null && Tema.esClaro(fondo2 ?: BG) && Build.VERSION.SDK_INT >= 26) barras = barras or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = barras
    }

    private fun refrescar() {
        val tieneCuenta = prefs.config?.valida() == true && prefs.user.isNotBlank()
        val hayLista = servidores.isNotEmpty()
        cSinCuenta.visibility = if (!hayLista && !tieneCuenta) View.VISIBLE else View.GONE
        cCuenta.visibility = if (hayLista) View.VISIBLE else View.GONE

        val con = ZumoVpnService.conectado
        val conectando = ZumoVpnService.conectando && !con
        val ocupado = con || conectando     // con la VPN encendida o conectando no se cambia servidor/usuario
        if (etUser.isEnabled == ocupado) {
            etUser.isEnabled = !ocupado; etPass.isEnabled = !ocupado
            val a = if (ocupado) 0.55f else 1f
            tvServidor.alpha = a; etUser.alpha = a; etPass.alpha = a
        }
        tvEstado.text = if (conectando) "Conectando…" else ZumoVpnService.estado
        val colorEstado = when {
            con -> VERDE
            conectando -> NARANJA
            else -> ROJO
        }
        tvEstado.setTextColor(colorEstado)
        puntoEstado.background = redondo(colorEstado, 10)
        tvError.text = when {
            con || conectando -> ""
            ZumoVpnService.estado == "Error" -> ZumoVpnService.ultimoError
            else -> ""
        }
        btn.text = when {
            con -> "◼  Desconectar"
            conectando -> "⏳  Conectando…  (tocá para cancelar)"
            else -> "▶  Conectar"
        }
        btn.background = redondo(when { con -> ROJO; conectando -> NARANJA; else -> VERDE }, radio(0.8f))

        // vencimiento: lo trae el .zs; entrando con usuario y contraseña la app no lo conoce
        cVence.visibility = if (prefs.servidor.isNotBlank()) View.GONE else View.VISIBLE
        val exp = prefs.exp
        val dias = Perfil.diasRestantes(exp)
        when {
            !tieneCuenta -> { tvVence.text = "--"; tvVence.setTextColor(TEXTO); tvVenceDetalle.text = "" }
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
    }

    private fun formatearDuracion(ms: Long): String {
        val s = ms / 1000
        val hh = s / 3600; val mm = (s % 3600) / 60; val ss = s % 60
        return if (hh > 0) "%d:%02d:%02d".format(hh, mm, ss) else "%02d:%02d".format(mm, ss)
    }

    // ---------- acciones ----------
    private fun alternar() {
        // Si está conectado o intentando conectar, el botón corta (cancela el intento).
        if (ZumoVpnService.corriendo || ZumoVpnService.conectando || ZumoVpnService.conectado) {
            prefs.wanted = false
            ZumoVpnService.detener(this)
            Registro.add(if (ZumoVpnService.conectado) "Desconectado" else "Conexión cancelada")
            refrescar()
            return
        }
        ocultarTeclado()
        Servidores.refrescar(this, prefs)
        val c = prefs.config
        if (c == null || !c.valida() || prefs.user.isBlank() || prefs.pass.isBlank()) {
            aviso(when {
                servidores.isEmpty() -> "Primero importá el archivo .zs de tu cuenta"
                c == null || !c.valida() -> "Primero elegí un servidor"
                prefs.user.isBlank() -> "Poné tu usuario"
                else -> "Poné tu contraseña"
            })
            return
        }
        if (Perfil.vencida(prefs.exp)) {
            Registro.add("✘ Tu cuenta venció el ${Perfil.fechaLinda(prefs.exp)}. Pedí la renovación.")
            aviso("Tu cuenta está vencida")
            return
        }
        // La primera vez, se pide quedar fuera del ahorro de batería antes de conectar (si no, el
        // sistema puede cerrar la VPN sola al rato, sobre todo en Tecno, Xiaomi y similares).
        if (!prefs.pidioBateria && !PowerGuide.sinOptimizar(this)) {
            prefs.pidioBateria = true
            AlertDialog.Builder(this, estiloDialogo)
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
        if (i != null) startActivityForResult(i, 1) else { ZumoVpnService.iniciar(this); refrescar() }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(req: Int, res: Int, data: Intent?) {
        super.onActivityResult(req, res, data)
        if (req == 1 && res == RESULT_OK) { ZumoVpnService.iniciar(this); refrescar() }
        if (req == 2 && res == RESULT_OK) data?.data?.let { leerArchivo(it) }
    }

    /** Ruta a mano hasta "DNS privado" según la marca del teléfono (cada fabricante lo guarda en otro lado). */
    private fun rutaDnsPrivado(): String {
        val m = Build.MANUFACTURER.lowercase()
        return when {
            "samsung" in m -> "Ajustes → Conexiones → Más ajustes de conexión → DNS privado"
            "xiaomi" in m || "redmi" in m || "poco" in m -> "Ajustes → Conexión y uso compartido → DNS privado"
            "oppo" in m || "realme" in m || "oneplus" in m -> "Ajustes → Conexión y uso compartido → DNS privado"
            "tecno" in m || "infinix" in m || "itel" in m -> "Ajustes → Conexiones → Más conexiones → DNS privado"
            "huawei" in m || "honor" in m -> "Ajustes → Conexiones → Más conexiones → DNS privado"
            "vivo" in m || "iqoo" in m -> "Ajustes → Red móvil/Conexiones → Más ajustes → DNS privado"
            else -> "Ajustes → Red e internet → DNS privado"
        }
    }

    /** Abre la pantalla de DNS privado para que el usuario lo ponga en "Desactivado".
     *  Android no deja que una app lo apague sola (es un ajuste protegido del sistema) ni tiene una
     *  pantalla pública para abrirlo directo: se prueban las rutas conocidas y, si el teléfono no
     *  responde a ninguna, se abre Conexiones y se muestra el camino exacto según la marca. */
    private fun abrirDnsPrivado() {
        val ruta = rutaDnsPrivado()
        val resaltar = ":settings:fragment_args_key"
        val q = "DNS privado"
        // 0: buscador de Ajustes con "DNS privado" escrito · 1: pantalla de DNS privado directa · 2: Conexiones/Ajustes
        val intentos = listOf(
            0 to Intent("android.settings.APP_SEARCH_SETTINGS").putExtra(android.app.SearchManager.QUERY, q).putExtra("query", q),
            0 to Intent(Intent.ACTION_SEARCH).setPackage("com.android.settings").putExtra(android.app.SearchManager.QUERY, q),
            1 to Intent().setClassName("com.android.settings", "com.android.settings.Settings\$PrivateDnsSettingsActivity"),
            1 to Intent("android.settings.PRIVATE_DNS_SETTINGS"),
            2 to Intent(Settings.ACTION_WIRELESS_SETTINGS).putExtra(resaltar, "private_dns_settings"),
            2 to Intent(Settings.ACTION_SETTINGS).putExtra(resaltar, "private_dns_settings")
        )
        for ((tipo, i) in intentos) {
            try {
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(i)
                aviso(when (tipo) {
                    0 -> "Si el buscador está vacío, escribí: $q\nPonelo en \"Desactivado\" y volvé a la app"
                    1 -> "Poné el DNS privado en \"Desactivado\" / \"Off\" y volvé a la app"
                    else -> "Buscá: $ruta\nPonelo en \"Desactivado\" y volvé a la app"
                })
                return
            } catch (_: Exception) {}
        }
        aviso("Buscá: $ruta\nPonelo en \"Desactivado\"")
    }

    /** Abre un botón de contacto del tema (WhatsApp, Telegram, una web...). */
    private fun abrirEnlace(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            aviso("No hay una app instalada para abrir ese enlace")
        }
    }

    private fun aviso(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()

    /** Diálogo con la misma paleta oscura de la app (el tema del sistema es claro por defecto). */
    private fun dialogo(titulo: String, vista: View): AlertDialog.Builder =
        AlertDialog.Builder(this, estiloDialogo).setTitle(titulo).setView(vista)

    /** Muestra el diálogo con fondo redondeado del color de las tarjetas (en vez de .show() directo). */
    private fun AlertDialog.Builder.mostrar() {
        val d = create()
        d.setOnShowListener { d.window?.setBackgroundDrawable(redondo(CARD, radio(0.9f))) }
        d.show()
    }

    private fun ocultarTeclado() {
        val v = currentFocus ?: return
        (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).hideSoftInputFromWindow(v.windowToken, 0)
        v.clearFocus()
    }

    // ---------- cuenta: servidor de la lista + usuario y contraseña ----------
    /** Pone en pantalla la cuenta guardada: servidor elegido, usuario y contraseña. */
    private fun cargarCuenta() {
        val elegido = Servidores.buscar(this, prefs.servidor)
        if (elegido == null && prefs.servidor.isNotBlank()) {
            // el servidor que usaba ya no está en esta versión de la app: tiene que elegir otro
            prefs.servidor = ""; prefs.config = null
        }
        if (elegido != null && elegido != prefs.config) prefs.config = elegido   // payload nuevo tras actualizar la app
        val cfg = prefs.config
        val porArchivo = elegido == null && cfg?.valida() == true && prefs.user.isNotBlank()   // cuenta de un .zs
        tvServidor.text = when {
            elegido != null -> "🌐  ${elegido.name}   ▾"
            porArchivo -> "📄  ${cfg?.name ?: "Zumo"}   ▾"
            else -> "Elegí un servidor   ▾"
        }
        tvServidor.setTextColor(if (elegido != null || porArchivo) TEXTO else NARANJA)
        tvCuentaZs.visibility = if (porArchivo) View.VISIBLE else View.GONE
        cajaLogin.visibility = if (elegido != null) View.VISIBLE else View.GONE
        cargando = true
        etUser.setText(if (elegido != null) prefs.user else "")
        etPass.setText(if (elegido != null) prefs.pass else "")
        cargando = false
    }

    private fun elegirServidor() {
        if (ZumoVpnService.corriendo) { aviso("Desconectá primero para cambiar de servidor"); return }
        if (servidores.isEmpty()) return
        ocultarTeclado()
        val lista = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(4), dp(16), dp(4)) }
        lateinit var d: AlertDialog
        for (sv in servidores) {
            val actual = sv.name == prefs.servidor
            val fila = texto((if (actual) "✓  " else "🌐  ") + sv.name, 16f, if (actual) ACENTO else TEXTO, actual).apply {
                background = redondo(conOpacidad(CAMPO), radio(0.6f), trazo = if (actual) 2 else 1, colorTrazo = if (actual) ACENTO else BORDE)
                setPadding(dp(16), dp(14), dp(16), dp(14))
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) }
                setOnClickListener { d.dismiss(); usarServidor(sv) }
            }
            lista.addView(fila)
        }
        val scroll = ScrollView(this).apply { addView(lista) }
        d = dialogo("Elegí el servidor", scroll).setNegativeButton("Cancelar", null).create()
        d.setOnShowListener { d.window?.setBackgroundDrawable(redondo(CARD, radio(0.9f))) }
        d.show()
    }

    /** Baja la lista de servidores actualizada desde internet (botón ↻). */
    private fun actualizarServidores() {
        if (actualizando) return
        actualizando = true
        btnActualizar?.apply { text = "…"; isEnabled = false }
        Thread {
            val r = Servidores.descargar(this)
            runOnUiThread {
                actualizando = false
                btnActualizar?.apply { text = "↻"; isEnabled = true }
                when (r.estado) {
                    Servidores.Estado.OK -> {
                        servidores = Servidores.lista(this)
                        Servidores.refrescar(this, prefs)   // si tu servidor cambió de payload, se toma el nuevo
                        cargarCuenta()
                        refrescar()
                        aviso("Servidores actualizados (${r.cantidad})")
                        Registro.add("Servidores actualizados desde internet: ${r.cantidad}")
                    }
                    Servidores.Estado.SIN_INTERNET -> aviso("Sin internet: probá de nuevo")
                    Servidores.Estado.VACIA -> aviso("La lista descargada está vacía")
                    Servidores.Estado.SIN_URL -> aviso("Esta app no tiene servidor de actualización")
                    Servidores.Estado.ERROR -> aviso("No se pudo actualizar: probá más tarde")
                }
            }
        }.start()
    }

    private fun usarServidor(s: Config) {
        if (s.name == prefs.servidor) return
        // la clave que venía dentro de un .zs no se muestra nunca: al pasar a la lista se empieza de cero
        val veniaDeArchivo = prefs.servidor.isBlank() && prefs.config?.valida() == true
        if (veniaDeArchivo) { prefs.user = ""; prefs.pass = "" }
        prefs.servidor = s.name; prefs.config = s; prefs.exp = ""
        Registro.add("Servidor elegido: ${s.name}")
        cargarCuenta()
        refrescar()
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
            if (p == null) { aviso("El archivo no es una cuenta válida de ${tema.nombre}"); Registro.add("✘ Archivo .zs no válido"); return }
            guardarCuenta(p)
        } catch (e: Exception) {
            aviso("No se pudo leer el archivo")
        }
    }

    private fun guardarCuenta(p: Perfil) {
        val cambioDeCuenta = prefs.user != p.user || prefs.config?.host != p.cfg.host
        prefs.servidor = ""   // la cuenta pasa a ser la del archivo, no un servidor de la lista
        prefs.config = p.cfg
        prefs.user = p.user; prefs.pass = p.pass
        prefs.exp = p.exp
        Registro.add(if (cambioDeCuenta) "✔ Cuenta importada" else "✔ Cuenta actualizada")
        aviso(if (Perfil.vencida(p.exp)) "Cuenta cargada, pero ya está vencida" else "Cuenta cargada")
        // si la VPN estaba corriendo con la cuenta anterior, se reinicia para usar la nueva
        if (ZumoVpnService.corriendo) { prefs.wanted = false; ZumoVpnService.detener(this) }
        cargarCuenta()
        refrescar()
    }

    private fun guiaBateria() {
        val ok = PowerGuide.sinOptimizar(this)
        val msg = (if (PowerGuide.esTecno) "Detecté un teléfono Tecno/Infinix/itel: son los que más cierran apps.\n\n" else "") +
            (if (ok) "✔ Batería: sin límite para esta app.\n\n" else "✘ Batería: el sistema todavía puede cerrarla.\n\n") + PowerGuide.pasos(tema.nombre)
        AlertDialog.Builder(this, estiloDialogo)
            .setTitle("Evitar desconexiones").setMessage(msg)
            .setPositiveButton("Quitar límite de batería") { _, _ -> PowerGuide.pedirExclusion(this) }
            .setNeutralButton("Abrir autoinicio") { _, _ -> PowerGuide.abrirAutoinicio(this) }
            .setNegativeButton("Cerrar", null).show()
    }
}
