# scan2plan 采集端 —— 一键编译 APK（Windows）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File android\build_apk.ps1
#
# 脚本会先检查 JDK / Android SDK / Gradle 是否齐全，
# 缺什么就明确告诉你装什么，不会瞎猜。

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Ok($msg)   { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Bad($msg)  { Write-Host "  [缺失] $msg" -ForegroundColor Red }
function Hint($msg) { Write-Host "         $msg" -ForegroundColor DarkGray }

Say "`n=== scan2plan 采集端编译检查 ===`n" Cyan

$missing = @()

# ---------- JDK 17 ----------
$java = Get-Command java -ErrorAction SilentlyContinue
if ($java) {
    $ver = (& java -version 2>&1 | Select-Object -First 1)
    Ok "java: $($java.Source)"
    Hint $ver
} else {
    Bad "没有找到 java（需要 JDK 17）"
    Hint "winget install Microsoft.OpenJDK.17"
    Hint "或从 https://adoptium.net 下载 Temurin 17，装完把 JAVA_HOME 指过去"
    $missing += "JDK17"
}

# ---------- Android SDK ----------
$sdk = $env:ANDROID_HOME
if (-not $sdk) { $sdk = $env:ANDROID_SDK_ROOT }
if (-not $sdk) {
    foreach ($c in @("$env:LOCALAPPDATA\Android\Sdk", "C:\Android\sdk", "C:\Program Files\Android\Sdk")) {
        if (Test-Path $c) { $sdk = $c; break }
    }
}
if ($sdk -and (Test-Path $sdk)) {
    Ok "Android SDK: $sdk"
    $hasPlatform = Test-Path (Join-Path $sdk "platforms\android-34")
    $hasBuildTools = Test-Path (Join-Path $sdk "build-tools\34.0.0")
    if (-not $hasPlatform) { Bad "SDK 里没有 platforms;android-34"; Hint "sdkmanager --install `"platforms;android-34`""; $missing += "platform-34" }
    else { Ok "platforms;android-34" }
    if (-not $hasBuildTools) { Bad "SDK 里没有 build-tools;34.0.0"; Hint "sdkmanager --install `"build-tools;34.0.0`""; $missing += "build-tools-34" }
    else { Ok "build-tools;34.0.0" }
} else {
    Bad "没有找到 Android SDK"
    Hint "装 Android Studio 最简单（自带 SDK），或用 commandline-tools："
    Hint "  sdkmanager --install `"platforms;android-34`" `"build-tools;34.0.0`""
    $missing += "AndroidSDK"
}

# ---------- Gradle ----------
$gradle = Get-Command gradle -ErrorAction SilentlyContinue
if ($gradle) {
    Ok "gradle: $($gradle.Source)"
} else {
    Bad "没有找到 gradle（需要 8.7+）"
    Hint "winget install Gradle.Gradle"
    Hint "或直接用 Android Studio 打开 android\ 目录（它自带 Gradle）"
    $missing += "Gradle"
}

if ($missing.Count -gt 0) {
    Say "`n缺少：$($missing -join ', ')" Yellow
    Say "`n两条更省事的路：" Yellow
    Say "  1) 用 GitHub Actions 云端编译（不需要本地任何环境）：见 android\GET_APK.md 路线 A"
    Say "  2) 用 Android Studio 打开 android\ 目录，点 Run / Build APK"
    Say "  3) 先不装 APK：用应用商店扫描 App 导出 .obj/.glb，直接跑 scan2plan recon（路线 D）"
    exit 1
}

# ---------- 编译 ----------
Say "`n环境齐全，开始编译…`n" Cyan
Push-Location $here
try {
    if (-not (Test-Path "local.properties") -and $sdk) {
        $escaped = $sdk.Replace('\', '\\')
        "sdk.dir=$escaped" | Out-File -Encoding ascii local.properties
        Say "已写入 local.properties（sdk.dir=$sdk）"
    }
    & gradle :app:assembleDebug --no-daemon --stacktrace
    if ($LASTEXITCODE -ne 0) {
        Say "`n编译失败，请把上面的报错发我。" Red
        exit $LASTEXITCODE
    }
    $apk = Get-ChildItem "app\build\outputs\apk\debug\*.apk" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($apk) {
        Say "`n=== 编译成功 ===" Green
        Say "APK: $($apk.FullName)"
        Say "大小: $([math]::Round($apk.Length/1MB,2)) MB"
        Say "`n安装到手机：`n  adb install -r `"$($apk.FullName)`""
        Say "或把 apk 拷到手机手动安装。"
    } else {
        Say "编译命令成功但没找到 apk，请检查 app\build\outputs\apk\debug\" Red
    }
} finally {
    Pop-Location
}
