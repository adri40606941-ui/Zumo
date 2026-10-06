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
import android.provider.Settings
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.*

/**
 * Pantalla mínima: cuenta, conectar, vencimiento y un registro de lo que pasa al conectar.
 * La cuenta se carga de dos formas:
 *  - eligiendo un servidor de la lista que trae la app (ver [Servidores]) y poniendo usuario y
 *    contraseña; el host y el payload de cada servidor no se ven en pantalla;
 *  - abriendo el archivo .zs que genera el bot de Telegram (trae servidor, payload, usuario,
 *    clave y vencimiento, y nada de eso se ve ni se edita).
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
    private lateinit var cCuenta: LinearLayout       // servidor de la lista + usuario y contraseña
    private lateinit var tvServidor: TextView
    private lateinit var tvCuentaZs: TextView
    private lateinit var cajaLogin: LinearLayout
    private lateinit var etUser: EditText
    private lateinit var etPass: EditText
    private lateinit var cVence: LinearLayout
    private var cargando = false                      // se están poniendo textos por código: no guardar
    private var claveVisible = false
    private val servidores: List<Config> by lazy { Servidores.lista(this) }
    private lateinit var tvVence: TextView
    private lateinit var tvVenceDetalle: TextView

    private val BG = Color.parseColor("#14102B")
    private val CARD = Color.parseColor("#201A3D")
    private val BORDE = Color.parseColor("#36305E")
    private val ACENTO = Color.parseColor("#B388FF")
    private val VERDE = Color.parseColor("#4CE0A8")
    private val ROJO = Color.parseColor("#FF6E6E")
    private val NARANJA = Color.parseColor("#FFB74D")
    private val TEXTO_SUAVE = Color.parseColor("#9D96C4")
    private val CAMPO = Color.parseColor("#2E2854")

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        prefs = Prefs(this)
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

    /** Campo de texto con la paleta de la app. Sin autocorrector (usuarios y claves no son palabras). */
    private fun campo(pista: String, clave: Boolean): EditText = EditText(this).apply {
        hint = pista; setHintTextColor(TEXTO_SUAVE); setTextColor(Color.WHITE); textSize = 15.5f
        inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or
            (if (clave) InputType.TYPE_TEXT_VARIATION_PASSWORD else InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD)
        maxLines = 1
        background = redondo(CAMPO, 12, trazo = 1)
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
        val root = ScrollView(this).apply { setBackgroundColor(BG); isFillViewport = true }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(40), dp(18), dp(28))
            isFocusableInTouchMode = true   // que al abrir no salte el teclado por los campos de la cuenta
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
        tvServidor = texto("", 15.5f, Color.WHITE, true).apply {
            background = redondo(CAMPO, 12, trazo = 1)
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
            background = redondo(CAMPO, 12, trazo = 1)
            layoutParams = LinearLayout.LayoutParams(dp(48), ViewGroup.LayoutParams.MATCH_PARENT).apply { marginStart = dp(8) }
            setOnClickListener {
                // mostrar / ocultar la contraseña que el cliente está escribiendo
                claveVisible = !claveVisible
                etPass.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS or
                    (if (claveVisible) InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD else InputType.TYPE_TEXT_VARIATION_PASSWORD)
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
        cVence = tarjeta()
        seccion(cVence, "📅", "Vencimiento")
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
        col.addView(cTel)

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
        val hayLista = servidores.isNotEmpty()
        cSinCuenta.visibility = if (!hayLista && !tieneCuenta) View.VISIBLE else View.GONE
        cCuenta.visibility = if (hayLista) View.VISIBLE else View.GONE

        val corr = ZumoVpnService.corriendo
        val con = ZumoVpnService.conectado
        // con la VPN encendida no se cambia de servidor ni de usuario
        if (etUser.isEnabled == corr) {
            etUser.isEnabled = !corr; etPass.isEnabled = !corr
            val a = if (corr) 0.55f else 1f
            tvServidor.alpha = a; etUser.alpha = a; etPass.alpha = a
        }
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

        // vencimiento: lo trae el .zs; entrando con usuario y contraseña la app no lo conoce
        cVence.visibility = if (prefs.servidor.isNotBlank()) View.GONE else View.VISIBLE
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

    /** Abre la pantalla de DNS privado para que el usuario lo ponga en "Desactivado".
     *  Android no deja que una app lo apague sola (es un ajuste protegido del sistema). */
    private fun abrirDnsPrivado() {
        aviso("Poné el DNS privado en \"Desactivado\" / \"Off\" y volvé a la app")
        val intentos = listOf(
            Intent("android.settings.PRIVATE_DNS_SETTINGS"),
            Intent(Settings.ACTION_WIRELESS_SETTINGS),
            Intent(Settings.ACTION_SETTINGS)
        )
        for (i in intentos) {
            try { i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); startActivity(i); return } catch (_: Exception) {}
        }
        aviso("Abrí Ajustes → Conexiones/Red → DNS privado y ponelo en Desactivado")
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
        tvServidor.setTextColor(if (elegido != null || porArchivo) Color.WHITE else NARANJA)
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
        val nombres = servidores.map { it.name }.toTypedArray()
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
            .setTitle("Elegí el servidor")
            .setItems(nombres) { _, i -> usarServidor(servidores[i]) }
            .setNegativeButton("Cancelar", null)
            .mostrar()
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
            if (p == null) { aviso("El archivo no es una cuenta válida de Zumo VPN"); Registro.add("✘ Archivo .zs no válido"); return }
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
            (if (ok) "✔ Batería: sin límite para esta app.\n\n" else "✘ Batería: el sistema todavía puede cerrarla.\n\n") + PowerGuide.pasos
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
            .setTitle("Evitar desconexiones").setMessage(msg)
            .setPositiveButton("Quitar límite de batería") { _, _ -> PowerGuide.pedirExclusion(this) }
            .setNeutralButton("Abrir autoinicio") { _, _ -> PowerGuide.abrirAutoinicio(this) }
            .setNegativeButton("Cerrar", null).show()
    }
}
