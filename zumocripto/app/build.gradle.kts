plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.zumo.cripto"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.zumo.cripto"
        minSdk = 24
        targetSdk = 34
        // versionCode = minutos desde 2025-01-01 (lo pone el flujo de GitHub): un APK nuevo siempre se instala encima del anterior.
        versionCode = (System.getenv("ZUMO_VERSION_CODE") ?: "1").toInt()
        versionName = "1.0." + (System.getenv("ZUMO_VERSION_CODE") ?: "1")
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
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    // org.json ya viene con Android; para las pruebas unitarias (que corren en la JVM, no en Android) hace falta el jar real.
    testImplementation("org.json:json:20240303")
}
