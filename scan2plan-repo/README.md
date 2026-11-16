# scan2plan

**手机扫一间房 → 可编辑的户型图 → DXF / IFC / GLB / OBJ / FBX。**

一条命令从点云生成户型，一个网页把户型改到你满意，五种格式直接交给下游软件。
Python 侧只依赖 **numpy**，前端零框架零构建。

```
手机(ARCore Depth)  ──►  cloud.ply  ──►  scan2plan recon  ──►  户型.s2p.json
                                                                    │
                                          ┌─────────────────────────┤
                                          ▼                         ▼
                                   Web 编辑器(2D/3D)          导出 DXF / IFC / GLB / OBJ / FBX
                                   改墙、改尺寸、改角度
```

---

## 1. 现在能做什么

| 能力 | 状态 | 说明 |
|---|---|---|
| 点云 → 墙体（墙面配对测厚度） | ✅ 已验证 | 墙面位置误差 **0.58~8.32 mm**（合成数据 7 个用例全通过） |
| 整户多房间（墙中线图 → 房间自动派生） | ✅ 已验证 | 9 墙 / 14 顶点 / 6 房间与真值完全一致 |
| 任意朝向户型（曼哈顿主方向估计） | ✅ 已验证 | 旋转 37° / -64° 用例误差 0.58 / 0.75 mm |
| 自动识别门窗洞口 | ✅ 已验证 | 按洞口底部有无墙点区分门 / 窗 |
| 可编辑：改墙长 / 角度 / 厚度 / 加删门窗 | ✅ 已验证 | 改尺寸时邻墙保持垂直（不会被拉歪） |
| 净面积、净周长自动派生 | ✅ 已验证 | 改墙后面积立刻重算 |
| 比例标定（一段实测墙长 → 全图等比校正） | ✅ 已验证 | 把相对误差压到 1% 以内的工程手段 |
| 导出 DXF（施工图样式） | ✅ 已校验 | 图层/墙体填充/门弧/窗符号/房间名+面积/尺寸标注 |
| 导出 IFC4（BIM） | ✅ 已校验 | IfcWall / IfcSlab / IfcSpace / 真实门窗洞口 |
| 导出 GLB / OBJ / FBX | ✅ 已校验 | glTF 2.0 / Wavefront / FBX ASCII 7400 |
| 第三方库交叉校验（trimesh 解析 GLB/OBJ） | ✅ 通过 | 8 个几何体 / 756 三角面 / 包围盒正确 |
| Web 编辑器（2D 平面 + 3D 预览） | ✅ 可用 | 拖拽改角点、改尺寸、改角度、门窗增删、撤销重做 |
| Android 采集端（ARCore Depth） | ⚠️ 代码完整，**未真机验证** | 见 `android/README.md` 的验证清单 |
| 纯 RGB 视频兜底链路 | ❌ 未实现 | 见下方「已知限制」 |

---

## 2. 30 秒上手（不用手机、不用装任何东西）

```bash
# 需要 Python 3.10+，只装 numpy 就够（其余全部零依赖手写）
pip install numpy            # 如果还没有

cd scan2plan

# ① 生成一套样例户型
python -m scan2plan sample apartment -o build/apartment.json

# ② 看统计
python -m scan2plan info build/apartment.json

# ③ 五种格式一次全导出
python -m scan2plan export build/apartment.json --format all --out build

# ④ 打开可视化编辑器（浏览器会自动打开 http://127.0.0.1:8765）
python -m scan2plan serve --open
```

编辑器里能做的事：拖墙角改尺寸、在右侧面板直接输入精确的**长度(m)/角度(°)**
、拖门/窗沿墙移动、改门宽窗高、给房间改名、撤销重做、点按钮导出五种格式。

---

## 2.5. 安装包：桌面端 wheel 与手机端 APK

### 桌面端（已产出，可直接安装）

```bash
cd scan2plan
python tools/build_wheel.py            # 生成 dist/scan2plan-0.1.0-py3-none-any.whl
pip install dist/scan2plan-0.1.0-py3-none-any.whl
scan2plan --help                       # 装完直接有命令
scan2plan serve --open
```

wheel 是零依赖手写的（130 KB，含前端 web 资源），已校验：RECORD 哈希、资源齐全、
从包内直接 import 全部模块、样例户型房间数正确。

### 手机端 APK

**当前环境无法直接产出 APK** —— 没有 JDK / Android SDK / Gradle / Android Studio，
网络被限速到约 11 KB/s（装 SDK 要 1 GB+）。三条你自己能走通的路，详见
**[android/GET_APK.md](android/GET_APK.md)**：

| 路线 | 需要本地环境 | 时间 | 说明 |
|---|---|---|---|
| **A. GitHub Actions 云端编译**（推荐） | 只要 git | 5 分钟 | 仓库已含 [.github/workflows/build-apk.yml](.github/workflows/build-apk.yml)：推上去 → Actions → 下载 artifact → 解压得 `app-debug.apk` |
| **B. Android Studio** | 装 AS（~3GB） | 30~60 分钟 | 打开 `android/` 目录，点 Run 直接装手机 |
| **C. 命令行** | JDK17 + SDK34 + Gradle8.7 | 30~60 分钟 | `powershell -File android\build_apk.ps1`（先检查环境再编） |
| **D. 不用 APK** | 只要 Python + numpy | **10 分钟** | 用商店里的扫描 App 导出 `.obj/.glb`，直接 `scan2plan recon` |

---

## 3. 三条使用路径

### 路径 A：有支持的 Android 手机（精度最好）

```bash
# 1. 装采集端（见 android/README.md）
# 2. 手机上扫完，把目录拷回来
adb pull /sdcard/Android/data/com.scan2plan.capture/files/scan2plan/ ./captures/

# 3. 重建
python -m scan2plan recon captures/scan_20250101_120000/cloud.ply -o 户型.json --voxel 0.03

# 4. 现场标定（把整体比例误差消掉，强烈建议做）
#    量一段墙的实际长度，然后在编辑器里改到实测值，或：
python - <<'PY'
import json
from scan2plan import Project
p = Project.from_dict(json.load(open("户型.json", encoding="utf-8")))
fp = p.active
wid = next(iter(fp.walls))       # 换成你要标定的那面墙
fp.calibrate_by_wall(wid, 3.245) # 实测 3.245 m
json.dump(p.to_dict(), open("户型_已标定.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
```

### 路径 B：任意来源的点云 / 网格

只要你能导出下面任一格式，就能用：

| 类型 | 格式 | 典型来源 |
|---|---|---|
| 点云 | `.ply` `.pcd` `.xyz` `.txt` `.pts` `.csv` `.npy` | 深度相机、Meshroom/COLMAP、本项目采集端 |
| **网格** | **`.obj` `.glb`** | **应用商店扫描 App 最常导出的两种**，会自动按面积重采样成点云 |

```bash
python -m scan2plan recon 你的模型.obj -o 户型.json
python -m scan2plan recon 你的点云.ply -o 户型.json
```

网格导入是**闭环验证过**的（我们自己的导出器 → 再导入重建 → 与真值逐墙比对）：

| 输入 | 点数 | 房间 | 墙面误差 mean / max |
|---|---|---|---|
| `.glb` | 301,387 | 6/6 | **0.61 mm** / 2.02 mm |
| `.obj` | 301,780 | 6/6 | **1.29 mm** / 2.33 mm |

> 手机端还没 APK 时，这是**最快用上完整功能**的路径：
> 商店装一个支持 ARCore 深度的扫描 App → 扫描 → 导出 OBJ/GLB → 跑上面命令。

### 路径 C：只有户型文件，想直接画

```bash
python -m scan2plan serve --open
# 编辑器里用「画墙」模式直接拉墙，画完导出
```

---

## 4. 命令速查

```
python -m scan2plan serve  [--host 127.0.0.1] [--port 8765] [--open]   启动 Web 编辑器
python -m scan2plan sample [apartment|single|two|lshape] -o out.json   生成样例
python -m scan2plan info   <f.s2p.json>                                统计与房间明细
python -m scan2plan export <f.s2p.json> --format {dxf,ifc,glb,obj,fbx,all} --out build
                             [--unit mm|m] [--dims overall|rooms|walls|none] [--ceiling]
python -m scan2plan recon  <cloud.ply|mesh.obj|mesh.glb> -o out.json
                             [--voxel 0.03] [--plane-tolerance 0.025]
                             [--wall-height 2.8] [--thickness-ext 0.24] [--thickness-int 0.12]
                             [--door-height 2.10] [--window-sill 0.90] [--no-manhattan] [-q]
```

---

## 5. 导出格式对照

| 格式 | 给谁用 | 本工具的输出内容 |
|---|---|---|
| **DXF** (R12, GBK) | AutoCAD / 中望 / 浩辰 / LibreCAD / Rhino | AIA 图层（A-WALL/A-DOOR/A-GLAZ/A-DIMS/A-AREA-IDEN）、闭合墙线、墙体填充、门开启弧、四线窗符号、房间名+面积、自绘尺寸标注（不含 DIMENSION 实体，避免样式解释不一致） |
| **IFC4** | Revit / Archicad / Navisworks / Solibri | IfcSite→Building→Storey 层级；每面墙独立 IfcWall 构件；真实门窗洞口（IfcOpeningElement + IfcRelVoidsElement）；IfcSlab 楼板；IfcSpace 房间体 + Qto 净面积 |
| **GLB** | Windows 3D 查看器 / Blender / Three.js / Unity / UE | glTF 2.0，按材质分 primitive，Y-up（规范要求），PBR 颜色 |
| **OBJ + MTL** | 3ds Max / Maya / Blender / SketchUp / C4D | 按构件分组（`o Wall:xxx` / `o Floor:房间名`），带材质颜色 |
| **FBX** (ASCII 7400) | 3ds Max / Maya / UE / Blender | 每材质一个 Mesh + Material，Z-up，单位换算到 cm |

单位约定：内部一律 **米**；DXF 默认输出 **毫米**（`--unit m` 可改）；三维格式保持米（FBX 换算成厘米）。

---

## 6. 精度：说得出数字

`tools/check_recon_suite.py` 会从**已知真值户型**合成带噪声/遮挡/家具干扰的点云，
跑完整重建，再与真值逐墙比对。当前结果：

```
用例                         点云     mean      p95      max   ≤10mm      房间       面积差    判定
基线（噪声4mm+家具）           220856     2.06     3.20     3.78  100.0%     6/6    0.0000    通过
高噪声 10mm               221248     6.82    10.49    12.35   88.9%     6/6    0.0010    通过
稀疏采样 6cm               123881     4.74     7.52     8.56  100.0%     6/6    0.0060    通过
无家具干扰                  217706     4.22     6.57     7.76  100.0%     6/6    0.0010    通过
重度缺失 25%               184138     8.32    12.89    15.29   88.9%     6/6    0.0020    通过
旋转户型 37°               221291     0.58     1.63     1.78  100.0%     6/6    0.0030    通过
旋转户型 -64°              221126     0.75     2.07     2.36  100.0%     6/6    0.0020    通过
通过 7/7
```

- **墙面位置误差** = 每面真值墙中点到最近重建墙轴线的垂直距离（mm）
- **房间面积差** = 同位置房间的净面积之差（m²），最大 0.006 m²
- 结论：纯算法链路已优于「相对误差 1%」的目标（5 m 墙允许 ±50 mm，实测 ≤15 mm）

⚠️ 这是**重建几何精度**，不等于整机测量不确定度。真机上还叠加深度标定误差、
IMU 漂移、玻璃/镜面/纯白墙的缺失，所以务必按 `android/README.md` 的采集规范操作，
并用一段实测墙长做一次**等比标定**。详见 `docs/02-accuracy.md`。

---

## 7. 架构

```
scan2plan/
├── scan2plan/
│   ├── geom.py           二维几何内核（向量/多边形/拟合/吸附）
│   ├── topology.py       平面直线图：顶点焊接、交点打断、**半边遍历取面**
│   ├── wallbody.py       墙体实体：**角度平分线斜接**、按参数切分子段
│   ├── model.py          FloorPlan/Wall/Opening/Room 数据模型 + 编辑操作 + 房间派生
│   ├── mesh.py           2D → 3D 三角网格（含门窗洞口，无布尔运算）
│   ├── derived.py        前端渲染用的紧凑派生数据
│   ├── ops.py            编辑操作解释器（Web/CLI/脚本共用一份语义）
│   ├── server.py         纯标准库 HTTP 服务（模型服务 + 导出）
│   ├── cli.py            命令行入口
│   ├── samples.py        合成户型（测试真值 + 演示）
│   ├── exporters/        dxf / ifc / glb / obj / fbx —— 全部手写，零依赖
│   ├── reconstruct/      点云 → 户型（见下）
│   └── web/              零框架前端（ES modules + Canvas2D + WebGL）
├── android/              Kotlin / ARCore Depth 采集端
├── tools/                诊断与验收脚本
└── docs/                 调研、架构、精度文档
```

重建管线：

```
点云 PLY/PCD/NPY
  ├─[1] 体素化 + 3×3×3 邻域 PCA 法向估计          reconstruct/planes.py
  ├─[2] 曼哈顿主方向：**四倍角圆周均值**（0.06° 级）  planes.estimate_yaw
  ├─[3] 竖直面按法向分 X/Y 两组，沿法向做**直方图峰检测聚类**
  │        （门窗侧壁会把两个墙面"桥"起来，峰检测能自然忽略）  planes.peak_clusters
  ├─[4] 墙面沿墙方向**按高度是否到顶**裁剪延伸段，洞口缺口允许填补  planes.trim_extent_by_height
  ├─[5] 反向且间距 5~45cm 的两个面配成一面墙 → 中线 + **实测厚度**
  ├─[6] 沿墙扫描点覆盖 → 自动识别门/窗洞口
  └─[7] 坐标归一 / 端点吸附 / **拐角补全** / 合并 → FloorPlan
        → 房间从墙中线图的面自动派生（整户一次成型）        reconstruct/vectorize.py
```

### 三个关键设计决定（为什么不按常规做法）

1. **不做多边形布尔运算。**
   房间 = 墙中线平面图的「面」（半边遍历）+ 每边向内偏移半个墙厚。
   比「墙实体求并集再取补集」更快、更稳、边界情况更少，而且改一面墙相邻两个房间会一起更新。

2. **墙体用角度平分线斜接，洞口用参数切分。**
   每片墙存成 `p0L/p1L/p0R/p1R` 四条边，沿长度 `s∈[0,1]` 可线性插值。
   于是在墙上开洞 = `sub(s0,s1)`，**完全不需要布尔运算**，门窗位置的精度是解析级的。

3. **几何只算一次，算在 Python 里。**
   浏览器只负责画和收集操作，所有几何/派生/导出都在 Python。
   避免「前端一套、后端一套」两套几何逻辑逐渐不一致（同类工具最常见的腐化点）。

---

## 8. 与现有开源项目的关系

调研了 20+ 个相关项目，最终**没有直接依赖任何一个**，但吸收了很多设计。完整的
调研结论、每个项目借鉴了什么、为什么没直接用，见 **[docs/00-research.md](docs/00-research.md)**。

一句话总结：
- **Apple RoomPlan** 证明了「原生 AR 平面/构件 API + 可编辑矢量输出」这条路可行，但它是 iOS/LiDAR 闭源，
  且输出不可参数化编辑 —— 我们做的是它的开源 Android 替代。
- **openPlan3D** 是 2D/3D 户型编辑器的开源参考，但它不解决「从点云重建」；
  我们的编辑器从它那里借鉴了「2D/3D 双视图」的交互组织。
- **PolyFit** 的「假设-选择」多边形重建思路启发了我们的墙面配对策略；
  但它是 C++/点云库重依赖，我们改成了纯 numpy 的「平面配对 + 直方图峰检测」。
- **FloorNet / cubiCasa5K** 走深度学习路线（从扫描体素/户型图图像回归），
  需要训练数据且不可解释、不可精确到毫米 —— 对「1cm 精度」是错的方向。
- **COLMAP / NeRF / 3DGS** 只能给点云/网格，尺度不可观，且不输出矢量墙 —— 适合作为纯 RGB 兜底链路的上游。

---

## 9. 已知限制（不藏）

1. **采集端未在真机验证。** 代码路径完整、ARCore API 用法按官方文档写，但没有设备实测。
   `android/README.md` 里有 8 项验证清单，拿到机器后请按顺序过一遍。
2. **依赖曼哈顿假设。** 墙体互相垂直（0/90°）时效果最好；圆弧墙、斜墙、异形吊顶会被
   强行拉成正交。`--no-manhattan` 可关掉主方向对齐，但精度会下降。
3. **纯 RGB 视频链路未实现。** 单目视频没有真实尺度，需要 COLMAP/3DGS 先重建点云
   再用路径 B 导入。这条链路我们没做集成，属于后续工作。
4. **多楼层未做层间对齐。** 数据结构支持多层（`Project.plans`），但自动叠层对齐未实现。
5. **IFC 是「可读可算量」级别，不是施工级 BIM。** 没有材质/钢筋/构造层/属性集，门窗是
   简易填充构件。够做算量、碰撞、展示，不够出施工图。
6. **DWG 需要商业授权。** 只输出 DXF（AutoCAD 可直接打开另存为 DWG）。ODA/RealDWG 是收费的。
7. **大户型内存。** 点云全在内存里处理，1000 万点以上建议先降采样（`--voxel 0.04`）。
8. **门窗识别是启发式的。** 按"洞口底部有没有墙点"区分门/窗，垭口会被判成门或窗
   （实测 9 个真值洞口识别出 10 个）。数量对得上，语义需要人工在编辑器里点一下改类型。
9. **旋转"穿过多条隔墙"的长墙会让形变较大。** 落在墙身上的 T 型接头会跟着一起转，
   但 2° 的旋转在 8 m 跨墙上就是 28 cm 位移，窄房间可能退化。改完点一下「全图正交化」
   可以恢复；把角度改回原值则能精确复原（已验证）。

---

## 10. 路线图

- [ ] 真机采集验证 + 参数整定（楔形误差补偿、深度置信度阈值）
- [ ] 多段扫描的位姿图拼接（同一户分几次扫）
- [ ] 纯 RGB 视频链路（COLMAP → 点云 → 现有管线）
- [ ] 弧形墙 / 斜墙支持（放宽曼哈顿假设）
- [ ] 家具/固定装置识别（橱柜、洁具）用于装修报价
- [ ] DXF 输出 R2018 + 真 DIMENSION 实体
- [ ] 编辑器：测量工具、楼层管理、导入底图对照

---

## 11. 开发与验收

```bash
# 几何/模型/编辑/导出/JSON 往返
python tools/diag.py

# 五种导出格式 + 结构校验
python tools/check_exports.py

# 用第三方库（trimesh / ezdxf / ifcopenshell）真正把导出文件解析一遍
# 可选，需要先 python tools/bootstrap_libs.py trimesh ezdxf
python tools/validate_with_thirdparty.py

# 重建精度（单用例，可调噪声/种子）
python tools/check_recon.py --seed 3 --noise 0.006

# 重建鲁棒性回归（7 个用例）
python tools/check_recon_suite.py

# 重建中间过程调试
python tools/probe_recon.py
```

> 注：`tools/bootstrap_libs.py` 是一个**可选**的辅助脚本，用来拉 ezdxf/trimesh 等第三方库
> 做交叉校验。当前实现完全不依赖它们，日常开发不需要联网。

实测交叉校验结果：

```
=== DXF 交叉校验（ezdxf 1.4.5）===
    DXF 版本 AC1009，实体 162 个
    实体类型: {'LINE': 85, 'SOLID': 33, 'TEXT': 24, 'POLYLINE': 16, 'ARC': 4}
    图层(13): A-WALL / A-WALL-FILL / A-WALL-CENTER / A-DOOR / A-GLAZ / A-AREA-IDEN
              / A-ROOM-BNDY / A-DIMS / A-AXIS / A-OUTLINE / A-TEXT ...
    审计错误: 0                          ← ezdxf 审计零错误
  [通过]
=== GLB 交叉校验（trimesh 5.2.0）===
    trimesh: 几何体 8 个，三角面 756，材质 8
    包围盒: [[-0.12, -0.12, -8.12], [10.12, 2.8, 0.12]]   ← Y-up 转换正确
  [通过]
=== OBJ 交叉校验（trimesh 5.2.0）===
    trimesh: 几何体 8，三角面 756
  [通过]
=== IFC 属性个数校验（681 个实体全查）===
  [通过]
```

IFC 没有可用的独立解析器（ifcopenshell 体积太大未安装），改为逐条核对
**IFC4 规范要求的属性个数**（681/681 通过）——这是手写 STEP 文件最容易出错、
且 Revit/Archicad 会直接拒收的地方。

---

MIT License · 数据全部本地处理，不上传任何点云或户型。
