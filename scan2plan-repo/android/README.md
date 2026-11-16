# scan2plan 采集端（Android / ARCore Depth）

> **想直接拿可安装的 APK？** 看 **[GET_APK.md](GET_APK.md)** —— 4 条路线，
> 包括「GitHub 云端编译」（不需要本地任何环境）和「不用 APK 也能跑通全流程」。

把手机变成一个室内「量房扫描仪」：边走边拍，输出**米制点云**，直接喂给
`scan2plan recon` 生可编辑户型图。

> 为什么不是「录一段普通视频」？
> 普通视频只有像素、没有真实尺度，单目 SfM 的尺度是猜出来的（典型误差 1%~5%）。
> ARCore 的 Depth API 给的是**米制深度**，配合 IMU 位姿直接就能得到绝对尺度点云——
> 这是把误差压到 1% 以内最省事的路径。普通视频链路见根目录 README 的「纯 RGB 兜底」。

---

## 1. 设备要求

| 项 | 要求 |
|---|---|
| Android 版本 | 8.0 (API 26) 及以上 |
| 硬件 | 支持 **ARCore** 且支持 **Depth API**（2019 年后的中高端机型，如 Pixel 4+ / 多数骁龙 8 系 / 麒麟 9 系 / 三星 S20+） |
| Google Play 服务（AR） | 需安装并保持更新 |
| 存储 | 每次扫描约 2~10 MB |

判断是否支持深度：运行后看顶部状态，出现「⚠ 无 Depth API」说明该机型只有稀疏点，
精度会明显下降（仍可用，但建议换机）。

---

## 2. 构建

```bash
# 在 Android Studio 里直接打开 android/ 目录即可（推荐，会自动补齐 SDK/Gradle）
# 或用命令行：
cd android
./gradlew :app:assembleDebug          # Windows: gradlew.bat :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

工程信息：
- Kotlin 1.9.24 / AGP 8.5.2 / compileSdk 34 / minSdk 26
- 唯一第三方依赖：`com.google.ar:core:1.43.0`
- 无 OpenGL 渲染（用深度图做取景预览），代码量小、易审

---

## 3. 采集流程（决定最终精度，务必按这个来）

1. **先站到房间中间，原地慢慢转一圈**——让 ARCore 建立稳定的位姿跟踪。
2. 每间房**沿墙绕一圈**，手机离墙 **0.5~3 m**，镜头略向下 10~20°。
3. 移动速度 **≤0.5 m/s**（比走路慢一半），不要甩。
4. 相邻房间要在**同一次采集**里连续走过去（不要停、不要重启 App），
   这样所有房间天然在同一坐标系里，不需要事后拼接。
5. 每面墙至少扫到 **一次正面 + 一次斜 45°**，减少掠射角造成的空洞。
6. 避开：纯白墙（无纹理，但深度仍有效）、镜子/玻璃、强逆光、正在走动的人。
7. 顶部「点云 N 点」到 **20 万点以上**再停；80 m² 通常在 **40 秒~2 分钟**。

停止后得到：

```
scan2plan/scan_20250101_120000/
├── cloud.ply      # 米制点云（世界坐标，Z 向上，3cm 体素去重）← 主产物
├── poses.jsonl    # 每帧相机位姿（排查/二次优化用）
└── session.json   # 设备、深度模式、点数、包围盒等元数据
```

把整个目录拷到电脑（或 `adb pull`）：

```bash
adb pull /sdcard/Android/data/com.scan2plan.capture/files/scan2plan/ ./captures/
```

---

## 4. 转成户型图

```bash
# 电脑上（只需要 Python 3.10+ 与 numpy）
python -m scan2plan recon captures/scan_20250101_120000/cloud.ply -o 户型.json --voxel 0.03

# 看统计
python -m scan2plan info 户型.json

# 导出 CAD / BIM / 三维
python -m scan2plan export 户型.json --format all --out build

# 打开可视化编辑器改尺寸、改角度、加门窗
python -m scan2plan serve --open
```

---

## 5. 代码结构

```
app/src/main/java/com/scan2plan/capture/
├── MainActivity.kt       界面：权限、ARCore 安装、开始/停止、路径
├── CaptureEngine.kt      ARCore Session 生命周期 + 采集主循环（独占后台线程）
├── DepthSampler.kt       深度图 → 世界坐标点云（内参 unproject + 位姿变换）
├── VoxelAccumulator.kt   3cm 体素去重，控制内存
└── SessionWriter.kt      写 cloud.ply / poses.jsonl / session.json
```

### 关键技术点（改代码前请先读）

1. **深度图有自己的内参**：`camera.getImageIntrinsics(session, DEPTH_IMAGE)`。
   用错内参（比如拿彩色相机内参）会导致点云整体缩放/畸变错误。
2. **ARCore 相机坐标系是 OpenGL 约定**：+X 右、+Y 上、**-Z 朝前**，
   所以 `unproject` 得到的是 `(x, y, -d)`。
3. **DEPTH16 的单位是毫米**，且是沿光轴的距离（不是欧氏距离）。
4. **Session 是线程绑定的**：创建、`update()`、`close()` 必须在同一个线程，
   否则会出现偶发崩溃 —— 本项目把整条链路都放在 `scan2plan-capture` 线程里。
5. `Config.UpdateMode.LATEST_CAMERA_IMAGE`：只处理最新帧，避免延迟累积。

---

## 6. 真机验证清单（我第一次拿到设备会按这个顺序过）

- [ ] App 启动不崩，状态栏出现「ARCore 就绪」
- [ ] 点开始后预览画面是深度灰度图，且近处亮、远处暗
- [ ] 原地转一圈后「✓ 定位正常」
- [ ] 扫一面 3m 墙，点数持续增长（约 1~2 万点/秒）
- [ ] 停止后 `cloud.ply` 能被 MeshLab / CloudCompare 打开，形状是房间
- [ ] `python -m scan2plan recon cloud.ply` 能输出至少 1 个房间
- [ ] 用激光测距仪量一段墙，与重建结果对比，误差在 1% 内
- [ ] 整户扫描：所有房间出现在同一张图里（无重影、无错位）

最后一项目前没有真机数据，属于**待验证**。合成数据下的算法精度见
`tools/check_recon_suite.py`（7/7 通过，墙面误差 0.58~8.32 mm）。
