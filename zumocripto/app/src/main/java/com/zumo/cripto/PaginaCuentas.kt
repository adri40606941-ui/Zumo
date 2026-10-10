package com.zumo.cripto

import android.app.Activity
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.View
import android.widget.EditText
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/**
 * Pestaña "Cuentas": el usuario pega la clave API de solo lectura de cada exchange. Con ella la app lee las redes de retiro y
 * depósito reales de su cuenta (y la comisión de retiro). No se pide nunca usuario ni contraseña, y la clave no sale del teléfono.
 */
class PaginaCuentas(private val act: Activity, private val almacen: AlmacenCuentas) {
    private val ui = Ui(act)
    private val principal = Handler(Looper.getMainLooper())
    private val estados = HashMap<String, TextView>()

    val vista: ScrollView = armar()

    private fun toast(t: String) = Toast.makeText(act, t, Toast.LENGTH_LONG).show()

    private fun armar(): ScrollView {
        val sv = ScrollView(act)
        val col = ui.vertical()
        col.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(24))

        val aviso = ui.tarjeta()
        aviso.addView(ui.texto("🔐 Solo claves de lectura", 16f, Paleta.TEXTO, true))
        aviso.addView(ui.texto(
            "• Creá en el exchange una clave API con permiso SOLO de lectura. No actives retiros ni trading: la app no los necesita.\n" +
            "• Nunca pongas tu usuario ni tu contraseña acá.\n" +
            "• La clave se guarda cifrada en este teléfono y solo se usa para consultar al propio exchange.\n" +
            "• Si la clave tiene lista de IP permitidas, la app no va a poder usarla (tu IP cambia).",
            13.5f, Paleta.APAGADO), ui.params(arriba = 8))
        col.addView(aviso, ui.params(abajo = 12))

        for (f in Privadas.FUENTES) col.addView(tarjeta(f), ui.params(abajo = 12))

        sv.addView(col)
        return sv
    }

    private fun tarjeta(f: FuentePrivada): View {
        val t = ui.tarjeta()
        val fila = ui.horizontal()
        fila.addView(ui.texto(f.nombre, 17f, Paleta.TEXTO, true), ui.params(ancho = 0, peso = 1f))
        val estado = ui.texto("", 12.5f, Paleta.APAGADO, true)
        estados[f.nombre] = estado
        fila.addView(estado)
        t.addView(fila)

        val clave = ui.campo("Clave API (API key)")
        val secreto = ui.campo("Secreto (API secret)")
        secreto.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        t.addView(clave, ui.params(arriba = 10))
        t.addView(secreto, ui.params(arriba = 8))
        var frase: EditText? = null
        if (f.pideFrase) {
            frase = ui.campo("Frase de la clave (passphrase)")
            frase.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            t.addView(frase, ui.params(arriba = 8))
        }

        val botones = ui.horizontal()
        botones.addView(ui.boton("Guardar") {
            val c = clave.text.toString().trim(); val s = secreto.text.toString().trim(); val p = frase?.text?.toString()?.trim() ?: ""
            if (c.isEmpty() || s.isEmpty() || (f.pideFrase && p.isEmpty())) { toast("Completá todos los campos"); return@boton }
            try {
                almacen.guardar(Credencial(f.nombre, c, s, p))
                clave.setText(""); secreto.setText(""); frase?.setText("")
                toast("Guardada en este teléfono")
            } catch (_: Exception) { toast("No se pudo guardar la clave") }
            pintar(f.nombre)
        }, ui.params(ancho = 0, peso = 1f, der = 6))
        botones.addView(ui.boton("Probar", false) { probar(f) }, ui.params(ancho = 0, peso = 1f, der = 6))
        botones.addView(ui.boton("Borrar", false) { almacen.borrar(f.nombre); pintar(f.nombre) }, ui.params(ancho = 0, peso = 1f))
        t.addView(botones, ui.params(arriba = 10))

        pintar(f.nombre)
        return t
    }

    private fun pintar(nombre: String, texto: String? = null) {
        val e = estados[nombre] ?: return
        e.text = texto ?: if (almacen.hay(nombre)) "✅ Guardada" else "Sin clave"
    }

    /** Hace una consulta de verdad con la clave guardada y dice si el exchange la aceptó. */
    private fun probar(f: FuentePrivada) {
        val c = almacen.leer(f.nombre)
        if (c == null) { toast("Primero guardá la clave de ${f.nombre}"); return }
        pintar(f.nombre, "Probando…")
        Thread({
            val r = Red.pedirFirmado(f.pedido(c, System.currentTimeMillis()))
            val texto = when {
                r.cuerpo == null && (r.codigo == 401 || r.codigo == 403) -> "❌ Rechazada (${r.codigo})"
                r.cuerpo == null && r.codigo == 0 -> "Sin conexión"
                r.cuerpo == null -> "❌ Error ${r.codigo}"
                else -> {
                    val n = f.parsear(r.cuerpo).size
                    if (n > 0) "✅ Funciona ($n activos)" else "❌ La clave no sirvió"
                }
            }
            principal.post { pintar(f.nombre, texto) }
        }, "zumocripto-probar").start()
    }
}
