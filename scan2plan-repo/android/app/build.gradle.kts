plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.scan2plan.capture"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.scan2plan.capture"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    // 刻意不开启 viewBinding / Compose：
    // 少一层代码生成，就少一类「第一次编译就失败」的可能。
}

dependencies {
    // 唯一依赖：ARCore（Depth API 从 1.31 起提供 getImageIntrinsics，这里用 1.43）。
    // 不用 AndroidX / Material / Lifecycle —— 界面用系统 Activity + findViewById，
    // 这样在 CI 里一次编译通过的概率最高，APK 也最小。
    implementation("com.google.ar:core:1.43.0")
}
