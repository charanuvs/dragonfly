plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.dragonfly.tabletstream"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.dragonfly.tabletstream"
        minSdk = 23
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("com.github.pedroSG94:RTSP-Server:1.3.0")
    implementation("com.github.pedroSG94.RootEncoder:library:2.4.6")
}
