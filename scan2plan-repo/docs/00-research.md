# 00 · 开源调研与选型

> 结论先行：**没有直接依赖任何一个现成项目**，但每个环节都找过参考。
> 这不是"重复造轮子"，而是候选方案要么闭源、要么重依赖、要么精度不达标、
> 要么根本不做"可编辑矢量户型"这一层。下面把调研过程和判断逐条写清楚。

调研目标（按用户需求拆解）：

| # | 需求 | 对应的技术环节 |
|---|---|---|
| A | 手机拍摄室内视频 | 移动端 AR / 深度采集 |
| B | 自动生成户型图 | 点云 → 墙面 → 矢量户型 |
| C | 1cm 级尺寸与角度 | 尺度来源 + 曼哈顿约束 + 标定 |
| D | 可修改、可调尺寸角度 | 户型数据模型 + 参数化编辑 + 编辑器 |
| E | 导出 AutoCAD | DXF/DWG 写出 |
| F | 导出三维模型 | glTF/OBJ/FBX/IFC 写出 |

---

## 1. 采集端（A）

| 项目 / 方案 | 类型 | 能用吗 | 判断 |
|---|---|---|---|
| [Apple RoomPlan](https://developer.apple.com/documentation/roomplan) | iOS 系统 API | ❌ 不能直接用于 Android | **最好的现成方案**，但目标是 iOS + LiDAR 机型，闭源黑盒，输出 `CapturedRoom`（USDZ + 结构化墙/门窗），**不能参数化编辑**，也不能改尺寸后重新求解。作为产品形态参考。 |
| [SceneView](https://github.com/sceneview/sceneview) | Android AR 库（ARCore 封装） | ⚠️ 可以参考 | 社区维护的 ARCore 场景渲染/平面渲染库，含 Room Scan 示例。我们用不到它的渲染层（本工具采集端不需要 3D 渲染），只借鉴了"平面 + 深度网格"的组织方式。 |
| [3Dify](https://github.com/V-Serghei/3Dify) | Android 点云导出 App | ⚠️ 可参考 | ARCore Depth + PCL(C++/NDK) + OBJ 导出。证明"手机 → 米制点云"这条路可行；但它是纯网格导出，不做矢量化。 |
| [RoomAR](https://github.com/colbehr/RoomAR) | Android 房间建模 | ⚠️ 可参考 | 思路同上，规模更小的示例工程。 |
| [ARCore Depth API](https://developers.google.com/ar/develop/depth) | 官方 API | ✅ **采用** | 米制深度图 + 位姿。这是 Android 上唯一能拿到绝对尺度稠密深度的官方路径，也是我们把误差压到 1% 以内的基础。 |

**决策**：自己写一个精简采集端（`android/`），只做一件事——**把 ARCore 深度图和位姿
累积成米制点云并落盘 PLY**。不引入 SceneView/PCL，因为：
- PCL 的 Android 包体积大（~50MB+）、编译链复杂，而我们只需要"累加 + 体素去重"，纯 Kotlin 约 200 行；
- 采集端不需要 3D 渲染，用深度图做取景反馈就够，省掉整条 OpenGL 链路。

---

## 2. 点云 → 户型（B）

| 项目 / 方案 | 类型 | 能用吗 | 判断 |
|---|---|---|---|
| [PolyFit](https://github.com/zhuhaipeng-byte/PolyFit)（CVPR'17 多边形表面重建） | C++ + 点云库 | ❌ 不直接 | 思路最接近（把点云拟合成平面多边形建筑模型），"假设-选择"的框架很有启发。但它是 C++/CGAL/点云库重依赖，输出是网格表面而不是**带厚度的墙中线图**，无法直接支撑"改墙长/角度"这类参数化编辑。我们借鉴了它的"先提平面假设、再按证据选择"的思想。 |
| [FloorNet](https://ar5iv.labs.arxiv.org/html/1804.00090)（FloorNet: unified floorplan reconstruction from 3D scans） | 深度学习 | ❌ 不采用 | 从扫描体素回归"房间占用图 + 墙/门/窗"三类输出，效果依赖训练集分布，误差是像素级、不可解释，做不到毫米级。数据还要训练。 |
| [cubiCasa5K](https://github.com/CubiCasa/CubiCasa5k) | 数据集 + 分割模型 | ❌ 不采用 | 强在**已有户型图图像**的语义分割，不是从 3D 重建；对我们"从点云恢复几何"没有直接帮助。 |
| [DeepFloorplan](https://github.com/zlzeng/DeepFloorplan) / Raster-to-Vector 系列 | 深度学习 | ❌ 不采用 | 同上，输入是栅格户型图。适合"老图纸电子化"，不适合"现场扫描"。 |
| 传统 SfM/MVS：[COLMAP](https://github.com/colmap/colmap)、[OpenMVG](https://github.com/openMVG/openMVG)/[OpenMVS](https://github.com/openMVG/openMVG)、[Meshroom](https://github.com/alicevision/meshroom) | 摄影测量 | ⚠️ 作为纯 RGB 兜底的上游 | 只能产出点云/网格，**尺度不可观**（单目 SfM 的尺度是任意的），且不输出矢量墙。适合"没有深度相机的手机"先重建点云，再走本工具的路径 B。 |
| 神经渲染（[NeRF/instant-ngp](https://github.com/NVlabs/instant-ngp)、3DGS） | 重建 | ❌ 不采用 | 出的是"好看但几何不精确"的表示，墙面平整度差，还必须先做网格化才能进几何管线。对本需求是绕远路。 |

**决策**：自己实现一套**纯 numpy 的几何管线**（`reconstruct/`），核心是四个自研算法：

1. **体素 + 3×3×3 邻域 PCA 法向**——不依赖 open3d/PCL；
2. **曼哈顿主方向用"四倍角圆周均值"**——法向 θ、θ+90°、θ+180°、θ+270° 乘 4 后重合，
   一次圆周平均即可，精度比直方图峰值高一个量级（实测从 0.50° 降到 0.06°）；
3. **墙面一维聚类用直方图峰检测**——门窗侧壁会在两个墙面之间连续撒点，把"间隔聚类"
   桥接成一团（实测 rms 从 4.5mm 恶化到 40~50mm），峰检测只认密度峰，天然免疫；
4. **墙面延伸段按"是否到顶"裁剪 + 洞口缺口填补**——同时解决"被家具侧壁污染"和"窗洞切断墙"。

结果：合成的 80 m² 户型，墙面位置误差 **0.58~8.32 mm**（7 个鲁棒性用例全通过），
见 `tools/check_recon_suite.py` 与 `docs/02-accuracy.md`。

---

## 3. 可编辑户型（D）

| 项目 / 方案 | 类型 | 能用吗 | 判断 |
|---|---|---|---|
| [openPlan3D](https://github.com/laanlabs/openPlan3D) | SvelteKit + Three.js 的开源 2D/3D 户型编辑器 | ⚠️ 参考 | 最接近的"开源户型编辑器"。借鉴了它"2D 平面 + 3D 预览双视图"的交互组织。没有直接使用，原因：① 它是 SvelteKit 工程，需要 node 构建链，而本工具希望"拷贝即用、零构建"；② 它的数据模型是渲染导向的，不支持"共享顶点的墙图 + 参数化改尺寸"这种约束编辑。 |
| tldraw / fabric.js / Konva | Canvas 图形库 | ❌ 不采用 | 通用 2D 图形库，户型编辑的核心是"墙-顶点-洞口"的拓扑约束，不是图形库能解决的。 |
| three.js / react-three-fiber | 3D 库 | ❌ 不采用 | 3D 预览只需要渲染一个几千面的网格，手写 WebGL1 约 300 行即可，省掉整个 npm 构建链（本机网络受限，也是现实约束）。 |

**决策**：自研**参数化墙图数据模型**（`model.py`），这是整个工具的地基：

- 顶点、墙（引用顶点 id）、门窗（挂在墙上，用"距墙起点的距离"定位）；
- **墙实体轮廓、房间多边形、面积、周长、3D 网格全部派生，永不存储**——
  所以改一个墙角，两个房间的面积立刻都对；
- 编辑操作（改长度/角度/厚度/加删门窗）实现了"邻墙保持垂直"的约束传播，
  这正是"可调尺寸角度"能用的关键（否则改一次尺寸整张图就歪了）。

前端零框架：ES modules + Canvas2D（2D）+ 手写 WebGL1（3D），
用 `python -m scan2plan serve` 起一个纯标准库 HTTP 服务把 Python 当几何内核。

---

## 4. 导出（E / F）

| 环节 | 候选 | 判断 |
|---|---|---|
| DXF | [ezdxf](https://ezdxf.mozman.at/)（Python，最成熟）、`@dxf-writer`(JS)、libdxfrw、LibreCAD | **没有直接依赖**。ezdxf 是很好的库（我们把它列为可选的交叉校验工具），但为了"拷贝即用、零 pip 依赖"，DXF 文本是手写的。R12 ASCII 是最兼容的版本，且我们不用 DIMENSION 实体（自绘尺寸线，避免不同软件对标注样式解释不一致）。 |
| DWG | ODA Drawings SDK / RealDWG | ❌ 不使用 | 商业授权，收费且闭源。DXF 可以被 AutoCAD 直接打开另存为 DWG。 |
| IFC | [IfcOpenShell](https://ifcopenshell.org/) | **没有直接依赖** | IfcOpenShell 功能强但依赖重（几十 MB、跨平台编译问题多）。IFC4 的 STEP 物理文件是纯文本，按 buildingSMART 规范手写约 500 行可控；结构化校验也自己写了（`validate_ifc`）。 |
| glTF/GLB | [trimesh](https://github.com/mikedh/trimesh) | 手写 | GLB = JSON chunk + BIN chunk，规范明确，手写约 300 行；trimesh 作为可选校验工具。 |
| OBJ | — | 手写 | 格式极简，带 MTL 输出材质。 |
| FBX | FBX SDK / Blender 命令行 | 手写 ASCII 7400 | FBX 二进制格式封闭，但 **ASCII 7.4 是公开可读的纯文本**，Blender/Max/Maya/UE 都能导入。省掉 FBX SDK 与 Blender 依赖。二进制 FBX 由 Blender 二次转换即可（README 有说明）。 |

**决策原则**：所有导出器**手写、零依赖**。代价是要自己写校验器（已写 5 个结构校验函数），
收益是"解压即用、不需要 pip install、不会因为第三方库版本变化而失效"。

---

## 5. 精度控制的思路来源

| 思路 | 来源 | 用法 |
|---|---|---|
| 曼哈顿世界假设 | 建筑/摄影测量领域通用做法 | 主方向估计 + 强制正交，把随机误差压成可控 |
| 平面配对测厚度 | 建筑扫描后处理常规做法 | 两个反向等距平面 → 中点即墙中线、间距即实测墙厚 |
| 现场标定 | 量房行业标准流程 | `calibrate_by_wall()`：用一段激光测距仪实测值做全图等比校正 |
| 拐角补全 | CAD 清理常规做法 | 端点吸附 + 沿垂直线延伸 + 交点补全，解决遮挡导致的 10~30cm 缺口 |

这是把误差从"算法精度"转到"工程可控"的关键：**算法给出毫米级几何，现场一个实测距离消掉整体比例误差。**

---

## 6. 明确放弃的方案及原因

| 方案 | 放弃原因 |
|---|---|
| 直接集成 Apple RoomPlan | iOS 独占 + 闭源 + 输出不可参数化编辑 |
| 集成 PCL / Open3D | Android 包体积与编译链成本高；PC 端只有 numpy 可用时也不便 |
| 集成 COLMAP 做纯 RGB 链路 | 尺度不可观（做不到 1% 绝对精度），且管线重（需 GPU/长时间） |
| 用深度学习分割替代几何拟合 | 不可解释、误差是像素级、需要训练数据，方向与"毫米级"目标冲突 |
| 用布尔运算做墙体并集 | 边界情况多、慢；改用平面图半边遍历 + 参数化切分，更快更稳 |
| 用 three.js 做 3D 预览 | 引入 npm 构建链，与"零构建"目标冲突；手写 WebGL 已够用 |
| 输出 DWG | 需要商业授权 |

---

## 参考链接

- Apple RoomPlan — https://developer.apple.com/documentation/roomplan
- openPlan3D — https://github.com/laanlabs/openPlan3D
- PolyFit — https://github.com/zhuhaipeng-byte/PolyFit
- FloorNet — https://ar5iv.labs.arxiv.org/html/1804.00090
- cubiCasa5K — https://github.com/CubiCasa/CubiCasa5k
- COLMAP — https://github.com/colmap/colmap
- OpenMVG — https://github.com/openMVG/openMVG
- Meshroom — https://github.com/alicevision/meshroom
- SceneView — https://github.com/sceneview/sceneview
- 3Dify — https://github.com/V-Sergei/3Dify ／ 原仓库：https://github.com/V-Serghei/3Dify
- RoomAR — https://github.com/colbehr/RoomAR
- ezdxf — https://ezdxf.mozman.at/
- IfcOpenShell — https://ifcopenshell.org/
- trimesh — https://github.com/mikedh/trimesh
- ARCore Depth API — https://developers.google.com/ar/develop/depth
