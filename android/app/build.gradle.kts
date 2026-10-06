import groovy.json.JsonSlurper
import java.awt.RenderingHints
import java.awt.image.BufferedImage
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import javax.imageio.IIOImage
import javax.imageio.ImageIO
import javax.imageio.ImageWriteParam

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Apariencia de la app: android/marca/tema.json (nombre, colores...), y opcionalmente icono.png y
// fondo.jpg en la misma carpeta. El bot de Telegram los reemplaza al compilar (ver bot/marca.py).
val carpetaMarca = rootProject.layout.projectDirectory.dir("marca")
val temaMarca: Map<*, *> = try {
    JsonSlurper().parse(carpetaMarca.file("tema.json").asFile, "UTF-8") as? Map<*, *> ?: emptyMap<String, Any>()
} catch (e: Exception) {
    emptyMap<String, Any>()
}
// el nombre va al manifiesto: fuera los símbolos que lo romperían (igual que bot/tema.py)
val nombreApp = ((temaMarca["nombre"] as? String) ?: "")
    .replace(Regex("[<>&\"'\\\\\\p{Cntrl}]"), "").replace(Regex("\\s+"), " ").trim().trimStart('@', '?').trim().take(30).trim()
    .ifBlank { "Zumo VPN" }
val hayIconoMarca = carpetaMarca.file("icono.png").asFile.isFile

android {
    namespace = "com.zumo.vpn"
    compileSdk = 34
    ndkVersion = "27.0.12077973"

    defaultConfig {
        applicationId = "com.zumo.vpn"
        minSdk = 24
        targetSdk = 34
        versionCode = (System.getenv("ZUMO_VERSION_CODE") ?: "1").toInt()
        versionName = "1.0." + (System.getenv("ZUMO_VERSION_CODE") ?: "1")
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
        manifestPlaceholders["nombreApp"] = nombreApp
        manifestPlaceholders["iconoApp"] = if (hayIconoMarca) "@mipmap/ic_marca" else "@drawable/ic_launcher"
    }

    externalNativeBuild {
        ndkBuild { path = file("hev-socks5-tunnel/Android.mk") }
    }

    signingConfigs {
        create("zumo") {
            val ks = System.getenv("ZUMO_KEYSTORE")
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("ZUMO_KS_PASS")
                keyAlias = "zumo"
                keyPassword = System.getenv("ZUMO_KS_PASS")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("zumo")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    testOptions { unitTests.isReturnDefaultValues = true }

    packaging {
        resources.excludes += listOf("META-INF/versions/**", "META-INF/*.kotlin_module", "META-INF/DEPENDENCIES", "META-INF/LICENSE*", "META-INF/NOTICE*")
        jniLibs.useLegacyPackaging = true
    }
}

dependencies {
    implementation("com.github.mwiede:jsch:0.2.20")
    implementation("androidx.core:core:1.13.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.apache.sshd:sshd-core:2.12.1")
    testImplementation("org.slf4j:slf4j-simple:1.7.36")
    testImplementation("org.json:json:20240303")
}

/**
 * La lista de servidores (android/servidores.txt) no va en texto plano dentro del APK: se cifra
 * acá y la app la abre con Zs.descifrarLista. Formato: "ZL1" + IV (12) + AES-256-GCM(texto).
 * El IV sale del propio texto, así la misma lista da siempre el mismo archivo.
 */
abstract class CifrarServidores : DefaultTask() {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val origen: RegularFileProperty

    @get:Input
    abstract val secreto: Property<String>

    @get:OutputDirectory
    abstract val salida: DirectoryProperty

    @TaskAction
    fun cifrar() {
        val texto = origen.get().asFile.readBytes()
        val clave = SecretKeySpec(
            MessageDigest.getInstance("SHA-256").digest(("ZUMO-ZS-1:" + secreto.get()).toByteArray(Charsets.UTF_8)), "AES")
        val iv = MessageDigest.getInstance("SHA-256").digest(texto).copyOf(12)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, clave, GCMParameterSpec(128, iv))
        val dir = salida.get().asFile
        dir.deleteRecursively()
        dir.mkdirs()
        dir.resolve("servidores.bin").writeBytes("ZL1".toByteArray(Charsets.US_ASCII) + iv + c.doFinal(texto))
    }
}

val cifrarServidores = tasks.register<CifrarServidores>("cifrarServidores") {
    origen.set(rootProject.layout.projectDirectory.file("servidores.txt"))
    // el mismo secreto que usa la app (una sola fuente: Zs.kt)
    secreto.set(providers.provider {
        Regex("SECRETO\\s*=\\s*\"([^\"]+)\"").find(file("src/main/java/com/zumo/vpn/Zs.kt").readText())!!.groupValues[1]
    })
    // la carpeta de salida la pone el plugin de Android (addGeneratedSourceDirectory)
}

/**
 * Deja la apariencia (android/marca) lista para el APK:
 *  - assets: tema.json tal cual, logo.png (el ícono, para mostrarlo arriba del título) y fondo.jpg
 *    (la imagen de fondo, achicada a lo que usa un teléfono);
 *  - res: el ícono de la app en todos los tamaños (mipmap ic_marca, clásico y adaptable).
 * Sin icono.png ni fondo.jpg no genera imágenes y la app usa el ícono original y el fondo de color.
 */
abstract class GenerarMarca : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.NAME_ONLY)
    abstract val origen: ConfigurableFileCollection

    @get:Input
    abstract val colorFondo: Property<String>

    @get:OutputDirectory
    abstract val assets: DirectoryProperty

    @get:OutputDirectory
    abstract val res: DirectoryProperty

    private fun leer(f: File): BufferedImage =
        (try { ImageIO.read(f) } catch (e: Exception) { null })
            ?: throw GradleException("android/marca/${f.name} no es una imagen PNG o JPG válida")

    private fun lienzo(w: Int, h: Int, tipo: Int, dibujar: (java.awt.Graphics2D) -> Unit): BufferedImage {
        val im = BufferedImage(w, h, tipo)
        val g = im.createGraphics()
        g.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC)
        g.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY)
        dibujar(g)
        g.dispose()
        return im
    }

    /** Achica de a mitades (queda más prolijo que un solo salto grande). */
    private fun escalar(src: BufferedImage, w: Int, h: Int, tipo: Int = BufferedImage.TYPE_INT_ARGB): BufferedImage {
        var actual = src
        var cw = src.width
        var ch = src.height
        while (cw / 2 >= w && ch / 2 >= h) {
            cw /= 2; ch /= 2
            val anterior = actual
            actual = lienzo(cw, ch, tipo) { it.drawImage(anterior, 0, 0, cw, ch, null) }
        }
        val ultimo = actual
        return lienzo(w, h, tipo) { it.drawImage(ultimo, 0, 0, w, h, null) }
    }

    private fun guardarPng(im: BufferedImage, f: File) {
        f.parentFile.mkdirs()
        ImageIO.write(im, "png", f)
    }

    @TaskAction
    fun generar() {
        System.setProperty("java.awt.headless", "true")
        val dirAssets = assets.get().asFile
        val dirRes = res.get().asFile
        dirAssets.deleteRecursively(); dirAssets.mkdirs()
        dirRes.deleteRecursively(); dirRes.mkdirs()
        val archivos = origen.files.associateBy { it.name }

        archivos["tema.json"]?.takeIf { it.isFile }?.copyTo(dirAssets.resolve("tema.json"), overwrite = true)

        archivos["fondo.jpg"]?.takeIf { it.isFile }?.let { f ->
            val src = leer(f)
            val cabecera = f.inputStream().use { it.readNBytes(2) }
            val esJpg = cabecera.size == 2 && cabecera[0] == 0xFF.toByte() && cabecera[1] == 0xD8.toByte()
            if (esJpg && maxOf(src.width, src.height) <= 1920) {
                f.copyTo(dirAssets.resolve("fondo.jpg"), overwrite = true)   // ya viene lista (así la deja el bot): no se vuelve a comprimir
                return@let
            }
            val factor = minOf(1.0, 1920.0 / maxOf(src.width, src.height))
            val w = maxOf(1, (src.width * factor).toInt())
            val h = maxOf(1, (src.height * factor).toInt())
            val rgb = escalar(src, w, h, BufferedImage.TYPE_INT_RGB)   // sin transparencia: lo que no tenga color queda negro
            val escritor = ImageIO.getImageWritersByFormatName("jpg").next()
            val calidad = escritor.defaultWriteParam.apply { compressionMode = ImageWriteParam.MODE_EXPLICIT; compressionQuality = 0.86f }
            ImageIO.createImageOutputStream(dirAssets.resolve("fondo.jpg")).use { salida ->
                escritor.output = salida
                escritor.write(null, IIOImage(rgb, null, null), calidad)
            }
            escritor.dispose()
        }

        archivos["icono.png"]?.takeIf { it.isFile }?.let { f ->
            val leida = leer(f)
            // cuadrada: si no lo es, se centra sobre transparente (sin recortar)
            val lado = maxOf(leida.width, leida.height)
            val src = lienzo(lado, lado, BufferedImage.TYPE_INT_ARGB) { it.drawImage(leida, (lado - leida.width) / 2, (lado - leida.height) / 2, null) }
            guardarPng(escalar(src, minOf(lado, 256), minOf(lado, 256)), dirAssets.resolve("logo.png"))

            val bordes = listOf(0 to 0, lado - 1 to 0, 0 to lado - 1, lado - 1 to lado - 1, lado / 2 to 0, lado / 2 to lado - 1, 0 to lado / 2, lado - 1 to lado / 2)
            val transparente = bordes.take(4).any { (x, y) -> (src.getRGB(x, y) ushr 24) < 250 }
            // fondo del ícono adaptable: con logo transparente, el color de fondo del tema; con una
            // imagen llena, el color promedio de sus bordes (así el relleno no se nota)
            val fondo = if (transparente) colorFondo.get() else {
                val px = bordes.map { (x, y) -> src.getRGB(x, y) }
                "#%02X%02X%02X".format(px.sumOf { (it shr 16) and 0xFF } / px.size, px.sumOf { (it shr 8) and 0xFF } / px.size, px.sumOf { it and 0xFF } / px.size)
            }
            // Android recorta el ícono adaptable con la forma de cada marca (círculo, cuadrado
            // redondeado...): de los 108 dp del lienzo se ven como mucho los 72 del centro, y siempre
            // los 66 del centro. Un logo transparente va entero en la zona segura; una imagen llena
            // ocupa toda la parte visible.
            val parte = if (transparente) 62.0 / 108 else 72.0 / 108
            for ((dens, base) in listOf("mdpi" to 48, "hdpi" to 72, "xhdpi" to 96, "xxhdpi" to 144, "xxxhdpi" to 192)) {
                guardarPng(escalar(src, base, base), dirRes.resolve("mipmap-$dens/ic_marca.png"))
                val n = base * 108 / 48
                val dentro = (n * parte).toInt()
                val chico = escalar(src, dentro, dentro)
                guardarPng(lienzo(n, n, BufferedImage.TYPE_INT_ARGB) { it.drawImage(chico, (n - dentro) / 2, (n - dentro) / 2, null) },
                    dirRes.resolve("mipmap-$dens/ic_marca_fg.png"))
            }
            dirRes.resolve("values").mkdirs()
            dirRes.resolve("values/marca.xml").writeText(
                "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<resources>\n    <color name=\"ic_marca_fondo\">$fondo</color>\n</resources>\n")
            dirRes.resolve("mipmap-anydpi-v26").mkdirs()
            dirRes.resolve("mipmap-anydpi-v26/ic_marca.xml").writeText(
                "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<adaptive-icon xmlns:android=\"http://schemas.android.com/apk/res/android\">\n" +
                    "    <background android:drawable=\"@color/ic_marca_fondo\" />\n    <foreground android:drawable=\"@mipmap/ic_marca_fg\" />\n</adaptive-icon>\n")
        }
    }
}

androidComponents {
    onVariants { v ->
        v.sources.assets?.addGeneratedSourceDirectory(cifrarServidores, CifrarServidores::salida)
        val generarMarca = tasks.register<GenerarMarca>("generarMarca" + v.name.replaceFirstChar { it.uppercase() }) {
            origen.from(carpetaMarca.asFileTree.matching { include("tema.json", "icono.png", "fondo.jpg") })
            colorFondo.set(((temaMarca["fondo"] as? String) ?: "").takeIf { Regex("#[0-9A-Fa-f]{6}").matches(it) } ?: "#14102B")
        }
        v.sources.assets?.addGeneratedSourceDirectory(generarMarca, GenerarMarca::assets)
        v.sources.res?.addGeneratedSourceDirectory(generarMarca, GenerarMarca::res)
    }
}
