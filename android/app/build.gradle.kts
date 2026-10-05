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
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.apache.sshd:sshd-core:2.12.1")
    testImplementation("org.slf4j:slf4j-simple:1.7.36")
    testImplementation("org.json:json:20240303")
}
