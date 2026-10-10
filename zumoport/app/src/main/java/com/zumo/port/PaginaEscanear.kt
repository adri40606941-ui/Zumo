package com.zumo.port

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import java.net.Inet4Address
import java.net.NetworkInterface

/** Pestaña "Escanear": una IP, un rango o un dominio, y qué puertos mirar. */
class PaginaEscanear(private val act: Activity, private val ui: Ui, private val red: Red) {
    private class Equipo(val nombre: String, val ip: String) { val hallazgos = ArrayList<Hallazgo>() }

    private val principal = Handler(Looper.getMainLooper())
    private val candado = Any()
    private val equipos = LinkedHashMap<String, Equipo>()       // clave = "nombre|ip"
    private var version = 0                                     // sube con cada hallazgo nuevo
    private var versionPintada = -1

    private var escaner: Escaner? = null
    private var escaneando = false
    @Volatile private var hechos = 0L          // los escribe el hilo del escaneo y los lee la pantalla
    @Volatile private var totalTareas = 0L
    private var inicio = 0L
    private var velocidad = Velocidad.NORMAL
    private var verificarWeb = true
    private var filtroPuerto = 0            // 0 = todos
    private var soloWebOk = false

    private val campoObjetivo = ui.campo("IP, rango o dominio")
    private val campoPuertos = ui.campo("Puertos: 80,443,8000-8100")
    private val botonPrincipal: TextView
    private val textoEstado = ui.texto("Listo para escanear", 14f, Paleta.APAGADO)
    private val barra = Barra(act, ui)
    private val cajaFiltros = ui.horizontal()
    private val cajaResultados = ui.vertical()
    private val textoResumen = ui.texto("", 13f, Paleta.APAGADO)
    private val chipsVelocidad = ArrayList<Pair<Velocidad, TextView>>()
    private val chipsPuertos = ArrayList<Pair<String, TextView>>()
    private lateinit var chipWeb: TextView
    private val chipsSalida = ArrayList<Pair<Salida, TextView>>()
    private var nombreRed = ""

    val vista: ScrollView = ScrollView(act)

    private val refresco = object : Runnable {
        override fun run() {
            pintarEstado()
            if (escaneando) principal.postDelayed(this, 300)
        }
    }

    init {
        campoPuertos.setText("80,443")
        campoObjetivo.inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
        val col = ui.vertical()
        col.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(24))

        // --- Objetivo
        val t1 = ui.tarjeta()
        t1.addView(ui.texto("🎯  Qué escanear", 16f, Paleta.TEXTO, true))
        t1.addView(campoObjetivo, ui.params(arriba = 10))
        t1.addView(ui.texto("Una IP (192.168.1.10), un rango (192.168.1.1-254 o 10.0.0.1-10.0.1.50), un bloque (10.0.0.0/24) o un dominio (ejemplo.com). Podés poner varios separados por coma.", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        val filaRed = ui.horizontal()
        filaRed.addView(ui.chip("📶 Mi red", false) { llenarMiRed() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 8))
        filaRed.addView(ui.chip("📋 Pegar", false) { pegar() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT))
        t1.addView(filaRed, ui.params(arriba = 10))
        col.addView(t1)

        // --- Puertos
        val t2 = ui.tarjeta()
        t2.addView(ui.texto("🔌  Qué puertos mirar", 16f, Paleta.TEXTO, true))
        val filaP = ui.horizontal()
        for ((nombre, lista) in PRESETS) {
            val c = ui.chip(nombre, false) { campoPuertos.setText(lista); pintarPresets() }
            chipsPuertos.add(lista to c)
            filaP.addView(c, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        }
        val scrollP = android.widget.HorizontalScrollView(act)
        scrollP.isHorizontalScrollBarEnabled = false
        scrollP.addView(filaP)
        t2.addView(scrollP, ui.params(arriba = 10))
        t2.addView(campoPuertos, ui.params(arriba = 10))
        col.addView(t2, ui.params(arriba = 12))
        pintarPresets()

        // --- Por qué red salir
        val tr = ui.tarjeta()
        tr.addView(ui.texto("📶  Por qué red salir", 16f, Paleta.TEXTO, true))
        val filaR = ui.horizontal()
        for (sa in Salida.values()) {
            val c = ui.chip(sa.titulo, sa == Ajustes.salida) { Ajustes.salida = sa; repintarRed() }
            chipsSalida.add(sa to c)
            filaR.addView(c, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        }
        tr.addView(filaR, ui.params(arriba = 10))
        tr.addView(ui.texto("Con «Datos móviles» el escaneo sale por tu operadora aunque el WiFi esté prendido (sirve para ver qué IP y puertos dejan pasar tus datos).", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        col.addView(tr, ui.params(arriba = 12))

        // --- Opciones
        val t3 = ui.tarjeta()
        t3.addView(ui.texto("⚡  Velocidad", 16f, Paleta.TEXTO, true))
        val filaV = ui.horizontal()
        for (v in Velocidad.values()) {
            val c = ui.chip(v.titulo, v == velocidad) { velocidad = v; pintarVelocidad() }
            chipsVelocidad.add(v to c)
            filaV.addView(c, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        }
        t3.addView(filaV, ui.params(arriba = 10))
        chipWeb = ui.chip("✔ Verificar respuesta web (HTTP / HTTPS)", verificarWeb) { verificarWeb = !verificarWeb; ui.pintarChip(chipWeb, verificarWeb) }
        t3.addView(chipWeb, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, arriba = 10))
        t3.addView(ui.texto("Suave cuida la batería y la red; Rápida barra más equipos por segundo pero algunos routers lo toman por un ataque.", 12f, Paleta.APAGADO), ui.params(arriba = 8))
        col.addView(t3, ui.params(arriba = 12))

        // --- Botón y progreso
        botonPrincipal = ui.boton("🚀  ESCANEAR") { if (escaneando) detener() else empezar() }
        col.addView(botonPrincipal, ui.params(arriba = 14))
        val t4 = ui.tarjeta()
        t4.addView(textoEstado)
        t4.addView(barra, ui.params(arriba = 10))
        t4.addView(textoResumen, ui.params(arriba = 8))
        col.addView(t4, ui.params(arriba = 14))

        // --- Resultados
        val t5 = ui.tarjeta()
        val cab = ui.horizontal()
        cab.addView(ui.texto("📡  Resultados", 16f, Paleta.TEXTO, true), ui.params(ancho = 0, peso = 1f))
        cab.addView(ui.chip("Copiar", false) { copiar() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        cab.addView(ui.chip("Compartir", false) { compartir() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT))
        t5.addView(cab)
        val scrollF = android.widget.HorizontalScrollView(act)
        scrollF.isHorizontalScrollBarEnabled = false
        scrollF.addView(cajaFiltros)
        t5.addView(scrollF, ui.params(arriba = 10))
        t5.addView(cajaResultados, ui.params(arriba = 6))
        col.addView(t5, ui.params(arriba = 14))

        vista.addView(col)
        pintarVelocidad()
        pintarEstado()
    }

    fun ponerObjetivo(texto: String) {
        campoObjetivo.setText(texto)
        if (campoPuertos.text.toString().isBlank()) campoPuertos.setText("80,443")
        pintarPresets()
    }

    fun repintarRed() { for ((sa, c) in chipsSalida) ui.pintarChip(c, sa == Ajustes.salida) }

    private fun pintarVelocidad() { for ((v, c) in chipsVelocidad) ui.pintarChip(c, v == velocidad) }

    private fun pintarPresets() {
        val actual = campoPuertos.text.toString().replace(" ", "")
        for ((lista, c) in chipsPuertos) ui.pintarChip(c, lista.replace(" ", "") == actual)
    }

    private fun toast(t: String) { Toast.makeText(act, t, Toast.LENGTH_SHORT).show() }

    private fun pegar() {
        val cm = act.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val t = cm.primaryClip?.getItemAt(0)?.text?.toString().orEmpty()
        if (t.isBlank()) toast("No hay nada para pegar") else campoObjetivo.setText(t.trim())
    }

    private fun llenarMiRed() {
        val red = miRed()
        if (red == null) toast("No encontré tu red. ¿Estás conectado al WiFi?") else campoObjetivo.setText(red)
    }

    /** El bloque /24 de la red a la que está conectado el celular (por ejemplo 192.168.1.0/24). */
    private fun miRed(): String? {
        try {
            val lista = NetworkInterface.getNetworkInterfaces() ?: return null
            for (ni in lista) {
                if (!ni.isUp || ni.isLoopback) continue
                for (a in ni.inetAddresses) {
                    if (a is Inet4Address && a.isSiteLocalAddress) {
                        val p = a.hostAddress.orEmpty().split(".")
                        if (p.size == 4) return "${p[0]}.${p[1]}.${p[2]}.0/24"
                    }
                }
            }
        } catch (_: Exception) {}
        return null
    }

    private fun empezar() {
        val obj = Objetivos.analizar(campoObjetivo.text.toString())
        if (obj.errores.isNotEmpty()) { toast(obj.errores.first()); return }
        val pu = Puertos.analizar(campoPuertos.text.toString())
        if (pu.error != null) { toast(pu.error); return }
        val total = obj.total
        if (total * pu.puertos.size > 3_000_000L) { toast("Son demasiados sondeos (${total * pu.puertos.size}). Achicá el rango o la lista de puertos."); return }
        if (Ajustes.salida == Salida.MOVIL && obj.hayLocales) {
            toast("Las IP de una red local (192.168…, 10…) no se alcanzan por datos móviles. Elegí WiFi o Automática."); return
        }
        val imm = act.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(vista.windowToken, 0)
        if (esperandoRed) return
        esperandoRed = true
        textoEstado.text = "Conectando por ${Ajustes.salida.nombre}…"
        textoEstado.setTextColor(Paleta.CYAN)
        red.conectar(Ajustes.salida) { ok, texto ->
            esperandoRed = false
            if (!ok) { toast(texto); textoEstado.text = texto; textoEstado.setTextColor(Paleta.AMARILLO) }
            else { nombreRed = texto; arrancar(obj, pu.puertos, total) }
        }
    }

    private var esperandoRed = false

    private fun arrancar(obj: Objetivos.Parseo, puertos: List<Int>, total: Long) {
        synchronized(candado) { equipos.clear(); version++ }
        filtroPuerto = 0; soloWebOk = false
        hechos = 0; totalTareas = total * puertos.size; inicio = System.currentTimeMillis()
        escaneando = true
        botonPrincipal.text = "⏹  DETENER"
        act.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val e = Escaner()
        escaner = e
        val opc = Opciones(velocidad, verificarWeb, true)
        Thread({
            e.escanear(obj.equipos(), puertos, opc, total, { h -> agregar(h) }, { hecho, tot -> hechos = hecho; totalTareas = tot })
            principal.post { terminar() }
        }, "zumoport-escaneo").start()
        principal.post(refresco)
    }

    private fun agregar(h: Hallazgo) {
        synchronized(candado) {
            val clave = h.equipo + "|" + h.ip
            val eq = equipos.getOrPut(clave) { Equipo(h.equipo, h.ip) }
            eq.hallazgos.add(h)
            version++
        }
    }

    private fun detener() { escaner?.cancelar(); textoEstado.text = "Deteniendo…" }

    private fun terminar() {
        escaneando = false
        red.soltar()
        botonPrincipal.text = "🚀  ESCANEAR"
        act.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        pintarEstado()
    }

    fun cerrar() { escaner?.cancelar() }


    private fun pintarEstado() {
        val seg = if (inicio == 0L) 0 else (System.currentTimeMillis() - inicio) / 1000
        val frac = if (totalTareas > 0) hechos.toDouble() / totalTareas else 0.0
        val (con, abiertos) = synchronized(candado) { equipos.size to equipos.values.sumOf { it.hallazgos.size } }
        if (escaneando) {
            textoEstado.text = "Escaneando por $nombreRed… ${(frac * 100).toInt()} %"
            textoEstado.setTextColor(Paleta.CYAN)
        } else if (inicio != 0L) {
            textoEstado.text = if (frac >= 0.999) "✔ Terminó en ${seg} s" else "Detenido a las ${(frac * 100).toInt()} %"
            textoEstado.setTextColor(if (frac >= 0.999) Paleta.VERDE else Paleta.AMARILLO)
        }
        barra.poner(frac)
        textoResumen.text = if (inicio == 0L) "" else "$con equipos con puertos abiertos · $abiertos puertos abiertos · $hechos de $totalTareas sondeos"
        if (versionPintada != version) { versionPintada = version; pintarResultados() }
    }

    private fun copia(): List<Equipo> = synchronized(candado) {
        equipos.values.sortedWith(compareBy({ ordenIp(it.ip) }, { it.nombre })).map { e ->
            Equipo(e.nombre, e.ip).also { n -> n.hallazgos.addAll(e.hallazgos.sortedBy { it.puerto }) }
        }
    }

    private fun ordenIp(ip: String): Long = Objetivos.ipv4(ip) ?: Long.MAX_VALUE

    private fun visibles(h: Hallazgo): Boolean = (filtroPuerto == 0 || h.puerto == filtroPuerto) && (!soloWebOk || h.webOk)

    private fun pintarResultados() {
        val todos = copia()
        cajaFiltros.removeAllViews()
        cajaResultados.removeAllViews()
        if (todos.isEmpty()) {
            cajaResultados.addView(ui.texto(if (inicio == 0L) "Todavía no escaneaste nada." else if (escaneando) "Buscando…" else "No se encontró ningún puerto abierto.", 13f, Paleta.APAGADO))
            return
        }
        // filtros: Todos, ✔ Web OK y un chip por cada puerto que apareció
        cajaFiltros.addView(ui.chip("Todos", filtroPuerto == 0 && !soloWebOk) { filtroPuerto = 0; soloWebOk = false; pintarResultados() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        cajaFiltros.addView(ui.chip("✔ Web OK", soloWebOk) { soloWebOk = !soloWebOk; pintarResultados() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        val puertos = todos.flatMap { e -> e.hallazgos.map { it.puerto } }.distinct().sorted()
        for (p in puertos.take(20)) {
            cajaFiltros.addView(ui.chip(p.toString(), filtroPuerto == p) { filtroPuerto = if (filtroPuerto == p) 0 else p; pintarResultados() }, ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 6))
        }
        var mostrados = 0
        var ocultos = 0
        for (eq in todos) {
            val hs = eq.hallazgos.filter { visibles(it) }
            if (hs.isEmpty()) continue
            if (mostrados >= MAX_FILAS) { ocultos++; continue }
            mostrados++
            val fila = ui.vertical()
            fila.background = ui.fondo(Paleta.TARJETA_2, 12)
            fila.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10))
            val titulo = if (eq.nombre != eq.ip) "${eq.nombre}  (${eq.ip})" else eq.ip
            fila.addView(ui.texto(titulo, 15f, Paleta.CYAN, true))
            for (h in hs) {
                val l = ui.horizontal()
                l.addView(ui.insignia(h.puerto.toString(), colorDe(h)), ui.params(ancho = ViewGroup.LayoutParams.WRAP_CONTENT, der = 8))
                l.addView(ui.texto(h.resumen(), 13f, Paleta.TEXTO), ui.params(ancho = 0, peso = 1f))
                fila.addView(l, ui.params(arriba = 6))
            }
            cajaResultados.addView(fila, ui.params(abajo = 8))
        }
        if (mostrados == 0) cajaResultados.addView(ui.texto("Ningún resultado con ese filtro.", 13f, Paleta.APAGADO))
        if (ocultos > 0) cajaResultados.addView(ui.texto("… y $ocultos equipos más. Usá Copiar o Compartir para llevarte la lista completa.", 12f, Paleta.AMARILLO))
    }

    private fun colorDe(h: Hallazgo): Int = when {
        h.webOk -> Paleta.VERDE
        h.http in 400..499 -> Paleta.AMARILLO
        h.http >= 500 -> Paleta.NARANJA
        else -> Paleta.CYAN
    }

    private fun textoPlano(): String {
        val sb = StringBuilder()
        for (eq in copia()) for (h in eq.hallazgos) {
            sb.append(eq.ip).append(':').append(h.puerto).append("  ").append(h.resumen())
            if (eq.nombre != eq.ip) sb.append("  [").append(eq.nombre).append(']')
            sb.append('\n')
        }
        return sb.toString().trim()
    }

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
        act.startActivity(Intent.createChooser(i, "Compartir resultados"))
    }

    companion object {
        const val MAX_FILAS = 300
        val PRESETS = listOf(
            "80 y 443" to "80,443",
            "Web" to Puertos.WEB.joinToString(","),
            "Comunes" to Puertos.COMUNES.joinToString(","),
            "1–1024" to "1-1024",
        )
    }
}
