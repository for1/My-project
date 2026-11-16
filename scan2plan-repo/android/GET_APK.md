# 怎么拿到可以安装的 APK

先说实话（省得你白等）：

> **我这边无法产出 APK 二进制。** 运行环境里**没有任何 Android 构建链** ——
> 没有 JDK、没有 Android SDK、没有 Gradle、没有 Android Studio，
> 连 `.gradle` / `.android` 缓存目录都不存在（说明这台机器从没编过 Android），
> 包管理器 `winget` 也是坏的，网络限速约 11 KB/s（装 SDK 要 1 GB+）。
> 编 APK 需要的工具链一个都不在，这是硬约束。

**但仓库已经为你准备好了「一次点击就能出 APK」的东西**，见路线 A。
工程本身也做了「第一次编译就能过」的简化（零 AndroidX 依赖、无 viewBinding）。

---

## 路线 A（推荐）：GitHub 云端编译，5 分钟出 APK

仓库里已含 [`.github/workflows/build-apk.yml`](../.github/workflows/build-apk.yml)。
你**不需要装 git**，直接用浏览器拖拽上传即可。

### A-1. 上传项目（浏览器操作，不用 git）

1. 打开 <https://github.com/new>，建一个仓库（Public / Private 都行），
   **不要**勾选 "Add a README"（否则会和上传冲突）。
2. 进入新仓库 → 点 **"uploading an existing file"**（或 `Add file → Upload files`）。
3. 把 `scan2plan` 目录里的**全部内容**拖进浏览器窗口。
   - 注意：要拖**目录内的内容**，让 `.github/`、`android/` 出现在仓库根目录。
   - 如果浏览器不允许拖整个目录，就用随附的 `dist/scan2plan-repo.zip`
     在网页上解压后拖，或用 GitHub Desktop。
4. 点 **Commit changes**。

### A-2. 触发编译并下载 APK

1. 仓库页面点 **Actions** 标签页。
2. 左侧选 **Build Android APK** → 右侧 **Run workflow** → 绿色按钮。
3. 等 **3~5 分钟**（首次会下载 Gradle/SDK 依赖，之后有缓存会更快）。
4. 进入该次运行页面 → 页面底部 **Artifacts** → 下载
   `scan2plan-capture-debug-apk.zip`。
5. 解压得到 **`app-debug.apk`** —— 这就是可安装的安装包。

### A-3. 安装到手机

```
① 把 app-debug.apk 传到手机（微信文件传输 / 数据线 / 网盘）
② 手机上点开 apk
③ 允许「安装未知来源应用」（设置里给对应 App 授权）
④ 安装完成，桌面出现「scan2plan采集」
```

> 这是 **debug 签名**的 APK，可以直接装、可以直接用，不需要你做任何签名配置。
> 工作流里还会打印 `aapt2 dump badging`（包名 `com.scan2plan.capture`、minSdk 26），
> 作为编译成功的证据。

### 工作流做了什么（为什么大概率一次就过）

| 步骤 | 作用 |
|---|---|
| `setup-java 17` | JDK 17（AGP 8.5 要求） |
| `setup-android` + `sdkmanager` | 装 platform-34 / build-tools-34 |
| `setup-gradle 8.7` | AGP 8.5 要求 Gradle ≥8.7 |
| **自动拉取 ARCore 最新版本号并改写依赖** | 避免"版本号不存在"导致依赖解析失败 —— 这是手写工程最容易翻车的一处 |
| `gradle :app:assembleDebug` | 编 debug APK |
| `upload-artifact` | 把 APK 挂到运行页面供下载 |

工程侧也做了减法，降低首次编译失败率：
- **零 AndroidX / Material / Lifecycle 依赖**，界面用系统 `Activity` + `findViewById`
- **不开 viewBinding / Compose**（少一层代码生成）
- 主题用系统 `@android:style/Theme.Material.NoActionBar`
- 唯一第三方依赖就是 `com.google.ar:core`

---

## 路线 B：有 Android Studio（最方便调试）

1. 装 [Android Studio](https://developer.android.com/studio)（自带 JDK、SDK、Gradle）。
2. `File → Open` → 选 **`scan2plan/android`** 目录（注意是 android 子目录）。
3. 首次同步会自动下载 AGP / Kotlin / ARCore 依赖（需要能上外网）。
4. 连手机（开 USB 调试）→ 点 **▶ Run** 直接装到手机；
   或 `Build → Build Bundle(s)/APK(s) → Build APK(s)` 得到 `app-debug.apk`。

---

## 路线 C：命令行编译

前提：JDK 17、Android SDK（platform-34 + build-tools-34.0.0）、Gradle 8.7+。

```powershell
cd scan2plan\android
# 用完仓库自带的检查脚本（缺什么会明确告诉你）
powershell -ExecutionPolicy Bypass -File ..\android\build_apk.ps1
```

或手动：

```powershell
winget install Microsoft.OpenJDK.17
# 装 Android Studio 或 commandline-tools 后：
"sdk.dir=C:\\Android\\sdk" | Out-File -Encoding ascii local.properties
gradle :app:assembleDebug
# 产物 app\build\outputs\apk\debug\app-debug.apk
```

---

## 路线 D：**今天就能用，连 APK 都不需要**

用应用商店里成熟的扫描 App 采集，导出网格，直接喂给本项目的 Python 管线。
**不用装我们的采集端，也不用编译任何东西。**

```
手机 App 扫描  →  导出 .obj / .glb  →  scan2plan recon  →  可编辑户型图  →  DXF/IFC/GLB/OBJ/FBX
```

这条链路是**闭环验证过**的（我们的导出器 → 导入器 → 重建 → 与真值逐墙比对）：

| 输入 | 点数 | 房间 | 墙面位置误差 |
|---|---|---|---|
| `.glb` | 301,387 | 6/6 | mean **0.61 mm** / max 2.02 mm |
| `.obj` | 301,780 | 6/6 | mean **1.29 mm** / max 2.33 mm |
| `.ply` | 219,758 | 6/6 | mean 7.56 mm / max 13.91 mm |

步骤：

1. 手机上装一个支持 **ARCore 深度扫描**、且能导出 **OBJ 或 GLB** 的 App
   （例如 Polycam；**以你手机上该 App 当前版本实际提供的导出选项为准**）。
2. 按 [README.md](README.md) 第 3 节的要点扫一遍（每间房绕墙一圈、慢速平移）。
3. 导出模型 → 传到电脑。
4. 跑：

```bash
python -m scan2plan recon 扫描结果.obj -o 户型.json
python -m scan2plan info 户型.json
python -m scan2plan export 户型.json --format all --out build
python -m scan2plan serve --open            # 浏览器里改尺寸/角度
```

> 这条路精度取决于 App 的深度质量，通常不如自研采集端（我们直接读原始深度图，
> 不做多余的网格重采样），但**今天就能出可用户型图**。

---

## 四条路线对比

| | 需要本地环境 | 需要联网 | 时间 | 采集精度 |
|---|---|---|---|---|
| **A GitHub Actions** | 只要浏览器 | 是 | **约 5 分钟** | ★★★★（自研采集端） |
| B Android Studio | 装 AS（~3 GB） | 是 | 30~60 分钟 | ★★★★ |
| C 命令行 | JDK+SDK+Gradle | 是 | 30~60 分钟 | ★★★★ |
| D 商店 App + Python | 只要 Python + numpy | 装 App 时需要 | **约 10 分钟** | ★★★ |

---

## 拿到 APK 之后：真机验证 8 项清单

1. App 能启动，状态栏出现「ARCore 就绪」
2. 点开始后预览是深度灰度图，近处亮、远处暗
3. 原地转一圈后显示「✓ 定位正常」
4. 扫一面 3 m 墙，点数持续增长（约 1~2 万点/秒）
5. 停止后 `cloud.ply` 能用 MeshLab / CloudCompare 打开，形状是一间房
6. `python -m scan2plan recon cloud.ply` 至少能出 1 个房间
7. 用激光测距仪量一段墙，与重建结果比，误差 ≤1%
8. 整户扫描：所有房间在同一张图里，无重影、无错位

第 5~8 项目前**没有真机数据**，属于待验证。算法侧精度见
`tools/check_recon_suite.py`（7/7 通过，墙面误差 0.58~8.32 mm）。
