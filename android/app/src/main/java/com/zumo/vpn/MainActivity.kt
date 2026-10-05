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

    private val BG = Color.parseColor("#1B1535")
    private val CARD = Color.parseColor("#2A2150")
    private val ACENTO = Color.parseColor("#B388FF")
    private val VERDE = Color.parseColor("#69F0AE")
    private val ROJO = Color.parseColor("#FF6E6E")
    private val NARANJA = Color.parseColor("#FFB74D")

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

    private fun boton(t: String, color: Int = ACENTO, onClick: () -> Unit): Button =
        Button(this).apply {
            text = t; isAllCaps = false; setTextColor(Color.BLACK); textSize = 15f
            background = redondo(color)
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)).apply { topMargin = dp(8) }
        }

    private fun redondo(c: Int) = android.graphics.drawable.GradientDrawable().apply {
        setColor(c); cornerRadius = dp(14).toFloat()
    }

    private fun tarjeta(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        background = redondo(CARD)
        setPadding(dp(16), dp(14), dp(16), dp(14))
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(12) }
    }

    private fun campo(hint: String, tipo: Int = InputType.TYPE_CLASS_TEXT): EditText = EditText(this).apply {
        this.hint = hint; setHintTextColor(Color.GRAY); setTextColor(Color.WHITE); inputType = tipo
        setSingleLine(true)
    }

    private fun armarUi() {
        val root = ScrollView(this).apply { setBackgroundColor(BG); isFillViewport = true }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(36), dp(16), dp(24))
        }
        root.addView(col)

        col.addView(texto("ZUMO VPN", 26f, ACENTO, true).apply { gravity = Gravity.CENTER })

        val cEstado = tarjeta()
        tvEstado = texto("Desconectado", 22f, ROJO, true).apply { gravity = Gravity.CENTER }
        tvError = texto("", 13f, NARANJA).apply { gravity = Gravity.CENTER }
        cEstado.addView(tvEstado); cEstado.addView(tvError)
        btn = boton("Conectar", VERDE) { alternar() }
        cEstado.addView(btn)
        col.addView(cEstado)

        // login
        val cLogin = tarjeta()
        cLogin.addView(texto("Cuenta", 16f, ACENTO, true))
        swHwid = Switch(this).apply {
            text = "Modo HWID"; setTextColor(Color.WHITE); isChecked = prefs.useHwid
            setOnCheckedChangeListener { _, on -> prefs.useHwid = on; refrescarLogin() }
        }
        cLogin.addView(swHwid)
        tvHwid = texto("", 13f, NARANJA).apply {
            setTextIsSelectable(true)
        }
        cLogin.addView(tvHwid)
        boxLogin = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        etUser = campo("Usuario").apply { setText(prefs.user) }
        etPass = campo("Contraseña", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD).apply { setText(prefs.pass) }
        boxLogin.addView(etUser); boxLogin.addView(etPass)
        cLogin.addView(boxLogin)
        col.addView(cLogin)

        // configuración
        val cCfg = tarjeta()
        cCfg.addView(texto("Servidor", 16f, ACENTO, true))
        tvConfig = texto("", 14f)
        cCfg.addView(tvConfig)
        cCfg.addView(boton("Pegar enlace de configuración") { pegarEnlace() })
        cCfg.addView(boton("Editar configuración", CARD.let { Color.parseColor("#D1C4E9") }) { editarConfig() })
        cCfg.addView(boton("Compartir configuración", Color.parseColor("#D1C4E9")) { compartir() })
        col.addView(cCfg)

        // estabilidad
        val cEst = tarjeta()
        cEst.addView(texto("Evitar desconexiones", 16f, ACENTO, true))
        val sw = Switch(this).apply {
            text = "Reconectar al encender el teléfono"; setTextColor(Color.WHITE); isChecked = prefs.autoStart
            setOnCheckedChangeListener { _, on -> prefs.autoStart = on }
        }
        cEst.addView(sw)
        cEst.addView(boton("Guía para evitar cortes (batería)", NARANJA) { guiaBateria() })
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
        else "${c.name}\n${c.host}:${c.sshPort}  ·  ${nombreModo(c.mode)}"
        val corr = ZumoVpnService.corriendo
        tvEstado.text = ZumoVpnService.estado
        tvEstado.setTextColor(
            when {
                ZumoVpnService.conectado -> VERDE
                corr -> NARANJA
                else -> ROJO
            }
        )
        tvError.text = if (corr || ZumoVpnService.estado == "Error") ZumoVpnService.ultimoError else ""
        btn.text = if (corr) "Desconectar" else "Conectar"
        btn.background = redondo(if (corr) ROJO else VERDE)
    }

    private fun nombreModo(m: String) = when (m) { "ws" -> "WebSocket"; "payload" -> "Payload"; else -> "SSH directo" }

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
        val et = campo("zumo://...").apply { setText(if (portapapeles.startsWith("zumo://")) portapapeles else ""); setSingleLine(false) }
        AlertDialog.Builder(this).setTitle("Enlace de configuración").setView(et)
            .setPositiveButton("Importar") { _, _ -> importar(et.text.toString()) }
            .setNegativeButton("Cancelar", null).show()
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
        AlertDialog.Builder(this).setTitle("Importar configuración").setMessage("¿Guardar esta configuración de servidor?")
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
        AlertDialog.Builder(this).setTitle("Evitar desconexiones").setMessage(msg)
            .setPositiveButton("Quitar límite de batería") { _, _ -> PowerGuide.pedirExclusion(this) }
            .setNeutralButton("Abrir autoinicio") { _, _ -> PowerGuide.abrirAutoinicio(this) }
            .setNegativeButton("Cerrar", null).show()
    }

    private fun editarConfig() {
        val c = prefs.config ?: Config()
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(8), dp(20), 0) }
        fun f(hint: String, v: String, tipo: Int = InputType.TYPE_CLASS_TEXT) = campo(hint, tipo).apply { setText(v); setTextColor(Color.BLACK); setHintTextColor(Color.GRAY) }
        val nombre = f("Nombre", c.name)
        val host = f("Servidor (VPS)", c.host)
        val sshPort = f("Puerto SSH", c.sshPort.toString(), InputType.TYPE_CLASS_NUMBER)
        val modo = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, listOf("WebSocket", "Payload personalizado", "SSH directo"))
            setSelection(when (c.mode) { "ws" -> 0; "payload" -> 1; else -> 2 })
        }
        val pHost = f("Proxy / dominio al que se conecta (vacío = servidor)", c.proxyHost)
        val pPort = f("Puerto del proxy", c.proxyPort.toString(), InputType.TYPE_CLASS_NUMBER)
        val tls = CheckBox(this).apply { text = "TLS / SSL (puerto 443)"; isChecked = c.tls }
        val sni = f("SNI (vacío = automático)", c.sni)
        val wsHost = f("Host del WebSocket (vacío = servidor)", c.wsHost)
        val wsPath = f("Ruta del WebSocket", c.wsPath)
        val payload = f("Payload (usa [host_port], [crlf], [split]...)", c.payload).apply { setSingleLine(false); minLines = 3 }
        listOf(nombre, host, sshPort, modo, pHost, pPort, tls, sni, wsHost, wsPath, payload).forEach { col.addView(it) }
        val sv = ScrollView(this).apply { addView(col) }
        AlertDialog.Builder(this).setTitle("Configuración").setView(sv)
            .setPositiveButton("Guardar") { _, _ ->
                prefs.config = Config(
                    name = nombre.text.toString().ifBlank { "Zumo" }, host = host.text.toString().trim(),
                    sshPort = sshPort.text.toString().toIntOrNull() ?: 22,
                    mode = listOf("ws", "payload", "direct")[modo.selectedItemPosition],
                    proxyHost = pHost.text.toString().trim(), proxyPort = pPort.text.toString().toIntOrNull() ?: 80,
                    tls = tls.isChecked, sni = sni.text.toString().trim(), wsHost = wsHost.text.toString().trim(),
                    wsPath = wsPath.text.toString().ifBlank { "/" }, payload = payload.text.toString()
                )
                refrescar()
            }.setNegativeButton("Cancelar", null).show()
    }
}
