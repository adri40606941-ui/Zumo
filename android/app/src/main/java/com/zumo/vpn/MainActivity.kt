package com.zumo.vpn

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.core.content.FileProvider
import java.io.File

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
    private lateinit var tvVelocidad: TextView
    private lateinit var tvTiempo: TextView
    private lateinit var tvDatos: TextView
    private var perfilPendienteGuardar: Perfil? = null

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

        setContentView(root)
    }

    /** Pantalla de configuración (servidor, login, HWID, batería) detrás del botón ☰ de la esquina. */
    private fun abrirMenu() {
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        // login
        val cLogin = tarjeta()
        seccion(cLogin, "🔐", "Cuenta")
        swHwid = Switch(this).apply {
            text = "Modo HWID (sin usuario ni clave)"; setTextColor(Color.WHITE); isChecked = prefs.useHwid
            setOnCheckedChangeListener { _, on -> prefs.useHwid = on; refrescarLogin() }
        }
        cLogin.addView(swHwid)
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

        // configuración del servidor (sin mostrar el host/payload reales: solo si está configurado o no)
        val cCfg = tarjeta()
        seccion(cCfg, "🌐", "Servidor")
        tvConfig = texto("", 13.5f, TEXTO_SUAVE)
        cCfg.addView(tvConfig)
        cCfg.addView(botonSecundario("📋  Pegar enlace de configuración") { pegarEnlace() })
        cCfg.addView(botonSecundario("✏️  Editar configuración") { editarConfig() })
        cCfg.addView(botonSecundario("📤  Compartir configuración") { compartir() })
        col.addView(cCfg)

        // HWID en su propio cuadro, grande y con botón de copiar (para que no haya error al pasarlo)
        val cHwid = tarjeta()
        seccion(cHwid, "🪪", "Tu HWID")
        texto("Fijo para este teléfono. Pásaselo a quien te da el servicio.", 12f, TEXTO_SUAVE).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(10) }
        }.also { cHwid.addView(it) }
        val valorHwid = Hwid.get(this)
        tvHwid = texto(valorHwid, 19f, Color.WHITE, true).apply {
            setTextIsSelectable(true)
            gravity = Gravity.CENTER
            typeface = Typeface.MONOSPACE
            letterSpacing = 0.04f
            background = redondo(Color.parseColor("#2E2854"), 12)
            setPadding(dp(14), dp(14), dp(14), dp(14))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        cHwid.addView(tvHwid)
        cHwid.addView(botonPrimario("📋  Copiar HWID", ACENTO) {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("HWID", valorHwid))
            aviso("HWID copiado")
        })
        col.addView(cHwid)

        // perfil completo (servidor + inicio de sesión) en un archivo para enviar por WhatsApp
        val cPerfil = tarjeta()
        seccion(cPerfil, "📁", "Perfil (servidor + login)")
        cPerfil.addView(botonSecundario("💾  Guardar config (archivo para WhatsApp)") { guardarConfigArchivo() })
        cPerfil.addView(botonSecundario("📥  Importar config (desde archivo)") { importarConfigArchivo() })
        cPerfil.addView(botonSecundario("🗑  Borrar datos", ROJO) { borrarDatos() })
        col.addView(cPerfil)

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

        val sv = ScrollView(this).apply { addView(col) }
        dialogo("Configuración", sv).setPositiveButton("Cerrar", null).mostrar()
        refrescarLogin()
        refrescar()
    }

    private fun refrescarLogin() {
        if (!::swHwid.isInitialized || !::boxLogin.isInitialized) return
        val on = swHwid.isChecked
        boxLogin.visibility = if (on) View.GONE else View.VISIBLE
    }

    private fun guardarCampos() {
        if (!::etUser.isInitialized) return
        prefs.user = etUser.text.toString().trim()
        prefs.pass = etPass.text.toString().trim()
    }

    private fun refrescar() {
        if (::tvConfig.isInitialized) {
            val c = prefs.config
            // No se muestran host/payload acá: son datos del servidor, no algo para exponer en pantalla.
            tvConfig.text = if (c == null || c.host.isBlank()) "Sin configurar. Pega el enlace que te dieron."
            else "✔ Configurado: \"${c.name}\""
        }
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
            corr -> listOf(ZumoVpnService.ultimoError, ZumoVpnService.etapaActual).filter { it.isNotBlank() }.joinToString("\n")
            ZumoVpnService.estado == "Error" -> ZumoVpnService.ultimoError
            else -> ""
        }
        btn.text = if (corr) "◼  Desconectar" else "▶  Conectar"
        btn.background = redondo(if (corr) ROJO else VERDE, 16)

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
        guardarCampos()
        if (ZumoVpnService.corriendo) {
            prefs.wanted = false
            ZumoVpnService.detener(this)
            return
        }
        val c = prefs.config
        if (c == null || !c.valida()) { aviso("Primero pega o edita la configuración del servidor"); return }
        if (!prefs.useHwid && (prefs.user.isBlank() || prefs.pass.isBlank())) { aviso("Escribe tu usuario y contraseña, o activa el modo HWID"); return }
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
        if (req == 2 && res == RESULT_OK) data?.data?.let { leerArchivoConfig(it) }
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

    /** Guarda un perfil completo (servidor + usuario/clave o HWID) y actualiza la pantalla de login. */
    private fun importarPerfil(p: Perfil) {
        prefs.config = p.cfg
        prefs.useHwid = p.useHwid
        if (!p.useHwid) { prefs.user = p.user; prefs.pass = p.pass }
        if (::swHwid.isInitialized) swHwid.isChecked = prefs.useHwid
        if (::etUser.isInitialized) { etUser.setText(prefs.user); etPass.setText(prefs.pass) }
        refrescarLogin()
        aviso("Configuración \"${p.cfg.name}\" guardada" + if (p.useHwid) " (modo HWID)" else "")
        refrescar()
    }

    /** Borra el servidor, usuario/clave y cualquier .zumoconf exportado: vuelve la app a como estaba recién instalada. */
    private fun borrarDatos() {
        AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
            .setTitle("Borrar datos")
            .setMessage("Se borra el servidor, el usuario/clave guardados y los archivos de configuración exportados. El HWID de este teléfono no cambia. Vas a tener que configurar todo de nuevo.")
            .setPositiveButton("Borrar") { _, _ ->
                if (ZumoVpnService.corriendo) { prefs.wanted = false; ZumoVpnService.detener(this) }
                prefs.config = null
                prefs.user = ""; prefs.pass = ""; prefs.useHwid = false
                try { File(cacheDir, "config").deleteRecursively() } catch (_: Exception) {}
                if (::swHwid.isInitialized) swHwid.isChecked = false
                if (::etUser.isInitialized) { etUser.setText(""); etPass.setText("") }
                refrescarLogin(); refrescar()
                aviso("Datos borrados")
            }.setNegativeButton("Cancelar", null).show()
    }

    private fun importarDesdeIntent(i: Intent?) {
        val uri = i?.data ?: return
        when (uri.scheme) {
            "zumo" -> {
                val d = i.dataString ?: return
                AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                    .setTitle("Importar configuración").setMessage("¿Guardar esta configuración de servidor?")
                    .setPositiveButton("Sí") { _, _ -> importar(d) }.setNegativeButton("No", null).show()
            }
            "content", "file" -> leerArchivoConfig(uri)
        }
    }

    /** Arma un perfil (servidor + login) y pregunta dónde guardarlo: compartir o Descargas/Zumo. */
    private fun guardarConfigArchivo() {
        guardarCampos()
        val c = prefs.config
        if (c == null || !c.valida()) { aviso("Primero configurá el servidor"); return }
        val cbHwid = CheckBox(this).apply {
            text = "Usar el HWID de este teléfono como usuario (compatible con el panel)"
            setTextColor(Color.WHITE)
            isChecked = prefs.useHwid
        }
        val cont = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(8), dp(20), 0); addView(cbHwid) }
        dialogo("Guardar configuración", cont)
            .setPositiveButton("Guardar en Descargas/Zumo") { _, _ ->
                val p = if (cbHwid.isChecked) Perfil(c, useHwid = true)
                else Perfil(c, user = prefs.user, pass = prefs.pass, useHwid = false)
                guardarEnDescargas(p)
            }
            .setNeutralButton("Compartir (WhatsApp, etc.)") { _, _ ->
                val p = if (cbHwid.isChecked) Perfil(c, useHwid = true)
                else Perfil(c, user = prefs.user, pass = prefs.pass, useHwid = false)
                exportarArchivo(p)
            }
            .setNegativeButton("Cancelar", null).mostrar()
    }

    private fun exportarArchivo(p: Perfil) {
        try {
            val dir = File(cacheDir, "config").apply { mkdirs() }
            val nombre = p.cfg.name.ifBlank { "zumo" }.replace(Regex("[^A-Za-z0-9_-]"), "_") + ".zumoconf"
            val f = File(dir, nombre)
            f.writeText(p.toJson().toString())
            val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", f)
            val i = Intent(Intent.ACTION_SEND).setType("application/x-zumoconfig")
                .putExtra(Intent.EXTRA_STREAM, uri)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            startActivity(Intent.createChooser(i, "Compartir configuración"))
        } catch (e: Exception) {
            aviso("No se pudo crear el archivo: ${e.message}")
        }
    }

    /** Guarda el .zumoconf directo en Descargas/Zumo del teléfono (crea la carpeta si no existe). */
    private fun guardarEnDescargas(p: Perfil) {
        val nombre = p.cfg.name.ifBlank { "zumo" }.replace(Regex("[^A-Za-z0-9_-]"), "_") + ".zumoconf"
        val contenido = p.toJson().toString()
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, nombre)
                    put(MediaStore.MediaColumns.MIME_TYPE, "application/x-zumoconfig")
                    put(MediaStore.MediaColumns.RELATIVE_PATH, "Download/Zumo")
                }
                val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                if (uri == null) { aviso("No se pudo crear el archivo"); return }
                contentResolver.openOutputStream(uri)?.use { it.write(contenido.toByteArray(Charsets.UTF_8)) }
                aviso("Guardado en Descargas/Zumo/$nombre")
            } else {
                if (checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                    perfilPendienteGuardar = p
                    requestPermissions(arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), 9)
                    return
                }
                @Suppress("DEPRECATION")
                val dir = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "Zumo").apply { mkdirs() }
                File(dir, nombre).writeText(contenido)
                aviso("Guardado en Descargas/Zumo/$nombre")
            }
        } catch (e: Exception) {
            aviso("No se pudo guardar: ${e.message}")
        }
    }

    override fun onRequestPermissionsResult(req: Int, perms: Array<out String>, resultados: IntArray) {
        super.onRequestPermissionsResult(req, perms, resultados)
        if (req == 9) {
            val p = perfilPendienteGuardar; perfilPendienteGuardar = null
            if (p != null) {
                if (resultados.firstOrNull() == PackageManager.PERMISSION_GRANTED) guardarEnDescargas(p)
                else aviso("Sin permiso para guardar en Descargas")
            }
        }
    }

    private fun importarConfigArchivo() {
        try {
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*"), 2)
        } catch (e: Exception) {
            aviso("No se pudo abrir el selector de archivos")
        }
    }

    private fun leerArchivoConfig(uri: Uri) {
        try {
            val texto = contentResolver.openInputStream(uri)?.use { it.readBytes().toString(Charsets.UTF_8) }
            val p = texto?.let { Perfil.desdeTexto(it) }
            if (p == null) { aviso("El archivo no es una configuración válida de Zumo VPN"); return }
            importarPerfil(p)
        } catch (e: Exception) {
            aviso("No se pudo leer el archivo: ${e.message}")
        }
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
