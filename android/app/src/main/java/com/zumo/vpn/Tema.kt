package com.zumo.vpn

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable
import org.json.JSONObject

/** Botón de contacto del menú ☰ (WhatsApp, Telegram, una web...). */
data class Enlace(val texto: String, val url: String)

/**
 * Apariencia de la app: nombre, colores, fondo, letra y qué secciones se ven.
 *
 * Sale de android/marca/tema.json (o del paquete que sube el bot de Telegram al compilar), que
 * queda dentro del APK como assets/tema.json. El ícono y la imagen de fondo, si los hay, quedan
 * como assets/logo.png y assets/fondo.jpg. Las claves y los valores por defecto son los mismos
 * que los de bot/tema.py. Lo que falte o esté mal escrito usa el valor por defecto: un tema roto
 * nunca deja la app sin abrir.
 */
data class Tema(
    val nombre: String = "Zumo VPN",
    val lema: String = "Conexión privada y estable",
    val logo: String = "🚀",
    val logoImagen: Boolean = false,
    val tituloMayus: Boolean = true,
    val fondo: Int = 0xFF14102B.toInt(),
    val fondo2: Int? = null,
    val velo: Int = 55,
    val tarjeta: Int = 0xFF201A3D.toInt(),
    val borde: Int = 0xFF36305E.toInt(),
    val acento: Int = 0xFFB388FF.toInt(),
    val texto: Int = 0xFFFFFFFF.toInt(),
    val suave: Int = 0xFF9D96C4.toInt(),
    val campo: Int = 0xFF2E2854.toInt(),
    val conectar: Int = 0xFF4CE0A8.toInt(),
    val desconectar: Int = 0xFFFF6E6E.toInt(),
    val aviso: Int = 0xFFFFB74D.toInt(),
    val sobreBoton: Int = 0xFF0F0B21.toInt(),
    val opacidad: Int = 100,
    val radio: Int = 20,
    val fuente: String = "sans-serif",
    val escala: Int = 100,
    val verVencimiento: Boolean = true,
    val verConexion: Boolean = true,
    val verTelefono: Boolean = true,
    val verImportar: Boolean = true,
    val enlaces: List<Enlace> = emptyList(),
) {
    /** El nombre como va arriba en la pantalla. */
    val titulo: String get() = if (tituloMayus) nombre.uppercase() else nombre

    companion object {
        private const val ASSET = "tema.json"
        private val FUENTES = setOf(
            "sans-serif", "sans-serif-medium", "sans-serif-light", "sans-serif-condensed",
            "serif", "monospace", "casual", "cursive"
        )
        private val ESQUEMAS = listOf("https://", "http://", "tg://", "mailto:", "tel:")

        @Volatile private var cache: Tema? = null

        fun actual(ctx: Context): Tema {
            cache?.let { return it }
            val t = try {
                desdeJson(ctx.applicationContext.assets.open(ASSET).use { it.readBytes() }.toString(Charsets.UTF_8))
            } catch (e: Exception) {
                Tema()
            }
            cache = t
            return t
        }

        /** "#RRGGBB" → color opaco. null si no es un color. */
        fun color(hex: String?): Int? {
            val h = hex?.trim()?.removePrefix("#") ?: return null
            if (h.length != 6 || h.any { it !in '0'..'9' && it !in 'a'..'f' && it !in 'A'..'F' }) return null
            return (0xFF000000 or h.toLong(16)).toInt()
        }

        /** ¿Es un color claro? (para decidir si los íconos de la barra de estado van oscuros, etc.) */
        fun esClaro(c: Int): Boolean {
            val r = (c shr 16) and 0xFF
            val g = (c shr 8) and 0xFF
            val b = c and 0xFF
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.6
        }

        fun desdeJson(texto: String): Tema {
            val j = try { JSONObject(texto) } catch (e: Exception) { return Tema() }
            val d = Tema()
            fun c(k: String, def: Int) = color(j.optString(k, "")) ?: def
            fun n(k: String, def: Int, min: Int, max: Int) = j.optInt(k, def).coerceIn(min, max)
            fun s(k: String, def: String, largo: Int) =
                if (j.has(k)) j.optString(k, def).replace(Regex("\\s+"), " ").trim().take(largo) else def
            val enlaces = ArrayList<Enlace>()
            j.optJSONArray("enlaces")?.let { a ->
                for (i in 0 until a.length()) {
                    val o = a.optJSONObject(i) ?: continue
                    val t = o.optString("texto", "").trim().take(30)
                    val u = o.optString("url", "").trim()
                    if (t.isNotEmpty() && ESQUEMAS.any { u.startsWith(it, ignoreCase = true) } && enlaces.size < 3) enlaces.add(Enlace(t, u))
                }
            }
            return Tema(
                nombre = s("nombre", d.nombre, 30).ifBlank { d.nombre },
                lema = s("lema", d.lema, 60),
                logo = s("logo", d.logo, 8),
                logoImagen = j.optBoolean("logo_imagen", d.logoImagen),
                tituloMayus = j.optBoolean("titulo_mayus", d.tituloMayus),
                fondo = c("fondo", d.fondo),
                fondo2 = color(j.optString("fondo2", "")),
                velo = n("velo", d.velo, 0, 90),
                tarjeta = c("tarjeta", d.tarjeta),
                borde = c("borde", d.borde),
                acento = c("acento", d.acento),
                texto = c("texto", d.texto),
                suave = c("suave", d.suave),
                campo = c("campo", d.campo),
                conectar = c("conectar", d.conectar),
                desconectar = c("desconectar", d.desconectar),
                aviso = c("aviso", d.aviso),
                sobreBoton = c("sobre_boton", d.sobreBoton),
                opacidad = n("opacidad", d.opacidad, 40, 100),
                radio = n("radio", d.radio, 0, 32),
                fuente = j.optString("fuente", d.fuente).takeIf { it in FUENTES } ?: d.fuente,
                escala = n("escala", d.escala, 85, 130),
                verVencimiento = j.optBoolean("ver_vencimiento", true),
                verConexion = j.optBoolean("ver_conexion", true),
                verTelefono = j.optBoolean("ver_telefono", true),
                verImportar = j.optBoolean("ver_importar", true),
                enlaces = enlaces,
            )
        }

        /** Imagen de fondo que trae la app, o null si no tiene. */
        fun fondoImagen(ctx: Context): Bitmap? = imagen(ctx, "fondo.jpg")

        /** El ícono importado, para mostrarlo arriba del título. null si la app usa el ícono original. */
        fun logoImagen(ctx: Context): Bitmap? = imagen(ctx, "logo.png")

        private fun imagen(ctx: Context, nombre: String): Bitmap? = try {
            ctx.applicationContext.assets.open(nombre).use { BitmapFactory.decodeStream(it) }
        } catch (e: Exception) {
            null
        }
    }
}

/**
 * Fondo de pantalla con una foto: la recorta para llenar la ventana sin deformarla (como
 * "center crop") y le pone encima un velo negro para que se lean las letras.
 */
class FondoFoto(private val foto: Bitmap, velo: Int) : Drawable() {
    private val pincel = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)
    private val pincelVelo = Paint().apply { color = (255 * velo.coerceIn(0, 100) / 100) shl 24 }
    private val m = Matrix()

    override fun draw(canvas: Canvas) {
        val b = bounds
        if (b.isEmpty || foto.width == 0 || foto.height == 0) return
        val escala = maxOf(b.width().toFloat() / foto.width, b.height().toFloat() / foto.height)
        m.setScale(escala, escala)
        m.postTranslate(b.left + (b.width() - foto.width * escala) / 2f, b.top + (b.height() - foto.height * escala) / 2f)
        canvas.drawBitmap(foto, m, pincel)
        canvas.drawRect(b, pincelVelo)
    }

    override fun setAlpha(alpha: Int) {}
    override fun setColorFilter(cf: ColorFilter?) {}
    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.OPAQUE
}
