import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

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

androidComponents {
    onVariants { v -> v.sources.assets?.addGeneratedSourceDirectory(cifrarServidores, CifrarServidores::salida) }
}
