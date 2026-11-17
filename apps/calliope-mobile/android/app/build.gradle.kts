plugins { id("com.android.application") }
android {
    namespace = "ai.rvbbit.calliope.mobile"
    compileSdk = 35
    defaultConfig {
        applicationId = "ai.rvbbit.calliope.mobile"
        minSdk = 26
        // First compatibility probe; edge-to-edge/insets need their own validation.
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
        ndk { abiFilters += "arm64-v8a" }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            // Intentional: this sideload-only probe uses run-as to provision its session.
            isDebuggable = true
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}

dependencies { implementation("androidx.core:core:1.15.0") }
