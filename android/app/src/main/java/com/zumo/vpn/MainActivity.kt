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
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*

class MainActivity : Activity() {

    private lateinit var prefs: Prefs
    private val h = Handler(Looper.getMainLooper())
    private lateinit var tvEstado: TextView
    private lateinit var tvError: TextView
    private lateinit var tvConfig: TextView
    private lateinit var btn: Button
    private lateinit var etUser: EditText
    private lateinit var etPass: EditText
    private lateinit var swHwid: Switch
    private lateinit var tvHwid: TextView
    private lateinit var boxLogin: LinearLayout
    private lateinit var puntoEstado: View

    private val BG = Color.parseColor("#14102B")
    private val CARD = Color.parseColor("#201A3D")
    private val BORDE = Color.parseColor("#36305E")
    private val ACENTO = Color.parseColor("#B388FF")
    private val ACENTO_OSCURO = Color.parseColor("#8B6CC9")
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
        guardarCampos()
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

    private fun campo(hint: String, tipo: Int = InputType.TYPE_CLASS_TEXT): EditText = EditText(this).apply {
        this.hint = hint; setHintTextColor(Color.GRAY); setTextColor(Color.WHITE); inputType = tipo
        setSingleLine(true)
    }

    private fun armarUi() {
        val root = ScrollView(this).apply { setBackgroundColor(BG); isFillViewport = true }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(40), dp(18), dp(28))
        }
        root.addView(col)

        // encabezado
        val cab = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER }
        cab.addView(texto("🛡", 34f).apply { gravity = Gravity.CENTER })
        cab.addView(texto("ZUMO VPN", 25f, Color.WHITE, true).apply { gravity = Gravity.CENTER; letterSpacing = 0.03f })
        cab.addView(texto("Conexión privada y estable", 13f, TEXTO_SUAVE).apply {
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(2) }
        })
        col.addView(cab)

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

        // login
        val cLogin = tarjeta()
        seccion(cLogin, "🔐", "Cuenta")
        swHwid = Switch(this).apply {
            text = "Modo HWID (sin usuario ni clave)"; setTextColor(Color.WHITE); isChecked = prefs.useHwid
            setOnCheckedChangeListener { _, on -> prefs.useHwid = on; refrescarLogin() }
        }
        cLogin.addView(swHwid)
        tvHwid = texto("", 12.5f, TEXTO_SUAVE).apply {
            setTextIsSelectable(true)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) }
        }
        cLogin.addView(tvHwid)
        boxLogin = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(4) }
        }
        etUser = campo("Usuario").apply { setText(prefs.user) }
        etPass = campo("Contraseña", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD).apply {
            setText(prefs.pass)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) }
        }
        boxLogin.addView(etUser); boxLogin.addView(etPass)
        cLogin.addView(boxLogin)
        col.addView(cLogin)

        // configuración del servidor
        val cCfg = tarjeta()
        seccion(cCfg, "🌐", "Servidor")
        tvConfig = texto("", 13.5f, TEXTO_SUAVE)
        cCfg.addView(tvConfig)
        cCfg.addView(botonSecundario("📋  Pegar enlace de configuración") { pegarEnlace() })
        cCfg.addView(botonSecundario("✏️  Editar configuración") { editarConfig() })
        cCfg.addView(botonSecundario("📤  Compartir configuración") { compartir() })
        col.addView(cCfg)

        // estabilidad de la conexión
        val cEst = tarjeta()
        seccion(cEst, "⚙️", "Evitar desconexiones")
        val sw = Switch(this).apply {
            text = "Reconectar al encender el teléfono"; setTextColor(Color.WHITE); isChecked = prefs.autoStart
            setOnCheckedChangeListener { _, on -> prefs.autoStart = on }
        }
        cEst.addView(sw)
        cEst.addView(botonSecundario("🔋  Guía para evitar cortes de batería", NARANJA) { guiaBateria() })
        col.addView(cEst)

        setContentView(root)
        refrescarLogin()
    }

    private fun refrescarLogin() {
        val on = swHwid.isChecked
        boxLogin.visibility = if (on) View.GONE else View.VISIBLE
        tvHwid.visibility = if (on) View.VISIBLE else View.GONE
        tvHwid.text = "Tu HWID (pásaselo a quien te da el servicio):\n" + Hwid.get(this)
    }

    private fun guardarCampos() {
        if (!::etUser.isInitialized) return
        prefs.user = etUser.text.toString().trim()
        prefs.pass = etPass.text.toString().trim()
    }

    private fun refrescar() {
        val c = prefs.config
        tvConfig.text = if (c == null || c.host.isBlank()) "Sin configurar. Pega el enlace que te dieron."
        else "${c.name}\n${c.host}:${c.sshPort}  ·  " + (if (c.payload.isBlank()) "SSH directo" else "SSH + payload") + (if (c.tls) " + TLS" else "")
        val corr = ZumoVpnService.corriendo
        tvEstado.text = ZumoVpnService.estado
        val colorEstado = when {
            ZumoVpnService.conectado -> VERDE
            corr -> NARANJA
            else -> ROJO
        }
        tvEstado.setTextColor(colorEstado)
        puntoEstado.background = redondo(colorEstado, 10)
        tvError.text = when {
            ZumoVpnService.conectado -> ""
            corr -> listOf(ZumoVpnService.ultimoError, ZumoVpnService.etapaActual).filter { it.isNotBlank() }.joinToString("\n")
            ZumoVpnService.estado == "Error" -> ZumoVpnService.ultimoError
            else -> ""
        }
        btn.text = if (corr) "◼  Desconectar" else "▶  Conectar"
        btn.background = redondo(if (corr) ROJO else VERDE, 16)
    }

    // ---------- acciones ----------
    private fun alternar() {
        guardarCampos()
        if (ZumoVpnService.corriendo) {
            prefs.wanted = false
            ZumoVpnService.detener(this)
            return
        }
        val c = prefs.config
        if (c == null || !c.valida()) { aviso("Primero pega o edita la configuración del servidor"); return }
        if (!prefs.useHwid && (prefs.user.isBlank() || prefs.pass.isBlank())) { aviso("Escribe tu usuario y contraseña, o activa el modo HWID"); return }
        val i = VpnService.prepare(this)
        if (i != null) startActivityForResult(i, 1) else ZumoVpnService.iniciar(this)
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(req: Int, res: Int, data: Intent?) {
        super.onActivityResult(req, res, data)
        if (req == 1 && res == RESULT_OK) ZumoVpnService.iniciar(this)
    }

    private fun aviso(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()

    private fun pegarEnlace() {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val portapapeles = cm.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString() ?: ""
        val et = campo("zumo://...").apply {
            setText(if (portapapeles.startsWith("zumo://")) portapapeles else ""); setSingleLine(false)
            setTextColor(Color.BLACK); setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        val cont = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(8), dp(20), 0); addView(et) }
        dialogo("Enlace de configuración", cont)
            .setPositiveButton("Importar") { _, _ -> importar(et.text.toString()) }
            .setNegativeButton("Cancelar", null).mostrar()
    }

    /** Diálogo con la misma paleta oscura de la app (el tema del sistema es claro por defecto). */
    private fun dialogo(titulo: String, vista: View): AlertDialog.Builder =
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert).setTitle(titulo).setView(vista)

    /** Muestra el diálogo con fondo redondeado del color de las tarjetas (en vez de .show() directo). */
    private fun AlertDialog.Builder.mostrar() {
        val d = create()
        d.setOnShowListener { d.window?.setBackgroundDrawable(redondo(CARD, 18)) }
        d.show()
    }


    private fun importar(link: String) {
        val c = Config.fromLink(link)
        if (c == null) { aviso("El enlace no es válido"); return }
        prefs.config = c
        aviso("Configuración \"${c.name}\" guardada")
        refrescar()
    }

    private fun importarDesdeIntent(i: Intent?) {
        val d = i?.dataString ?: return
        if (!d.startsWith("zumo://")) return
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
            .setTitle("Importar configuración").setMessage("¿Guardar esta configuración de servidor?")
            .setPositiveButton("Sí") { _, _ -> importar(d) }.setNegativeButton("No", null).show()
    }

    private fun compartir() {
        val c = prefs.config ?: run { aviso("No hay configuración"); return }
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, c.toLink()), "Compartir"))
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

    private fun editarConfig() {
        val c = prefs.config ?: Config()
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(10), dp(20), 0) }
        fun etiqueta(t: String) = col.addView(texto(t, 12.5f, TEXTO_SUAVE, true).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(10) }
        })
        fun estiloCampo(v: View) { v.background = redondo(Color.parseColor("#2E2854"), 10); v.setPadding(dp(12), dp(10), dp(12), dp(10)) }
        fun f(hint: String, v: String, tipo: Int = InputType.TYPE_CLASS_TEXT) =
            campo(hint, tipo).apply { setText(v); setTextColor(Color.WHITE); setHintTextColor(TEXTO_SUAVE); estiloCampo(this) }
        etiqueta("Nombre")
        val nombre = f("Ej: Mi VPS", c.name)
        etiqueta("Servidor (dominio o IP)")
        val host = f("Ej: 157.254.54.170", c.host, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        etiqueta("Puerto")
        val puerto = f("22, 80, 443...", c.sshPort.toString(), InputType.TYPE_CLASS_NUMBER)
        etiqueta("Payload (opcional)")
        val payload = EditText(this).apply {
            hint = "Comodines: [host] [port] [host_port] [crlf] [lf] [split]"
            setHintTextColor(TEXTO_SUAVE); setTextColor(Color.WHITE); estiloCampo(this)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setSingleLine(false); minLines = 4; gravity = Gravity.TOP
            setText(c.payload)
        }
        val tls = CheckBox(this).apply {
            text = "TLS / SSL (puerto 443)"; setTextColor(Color.WHITE); isChecked = c.tls
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(12) }
        }
        etiqueta("SNI (vacío = el servidor)")
        val sni = f("Opcional", c.sni)
        listOf(nombre, host, puerto, payload).forEachIndexed { i, v ->
            if (i > 0) (v.layoutParams as? LinearLayout.LayoutParams)?.let {} // separación ya la da etiqueta()
            col.addView(v)
        }
        col.addView(tls); col.addView(sni)
        val sv = ScrollView(this).apply { addView(col) }
        dialogo("Configuración del servidor", sv)
            .setPositiveButton("Guardar") { _, _ ->
                prefs.config = Config(
                    name = nombre.text.toString().ifBlank { "Zumo" }, host = host.text.toString(),
                    sshPort = puerto.text.toString().toIntOrNull() ?: 22,
                    payload = payload.text.toString(), tls = tls.isChecked, sni = sni.text.toString().trim()
                ).limpiar()
                refrescar()
            }.setNegativeButton("Cancelar", null).mostrar()
    }
}
