# 01 · 架构与数据模型

## 1. 坐标与单位约定（全工程唯一真相）

| 项 | 约定 |
|---|---|
| 长度单位 | **米**（`float`）。只在导出 CAD 时换算成毫米/厘米 |
| 平面坐标 | X 向东、Y 向北（CAD 世界坐标系习惯），**Y 轴向上** |
| 高度 | Z 向上，楼板面 `z = 0`，楼层通过 `FloorPlan.elevation` 平移 |
| 角度 | 度，数学正方向（逆时针为正），0° 指向 +X |
| 多边形方向 | 逆时针（面积为正）；`geom.dedupe_ring()` 会强制归一 |

所有模块都遵守这套约定。唯一的例外是 glTF 规范要求 Y-up，`exporters/glb.py` 里做了转换。

---

## 2. 数据模型：只存一份真相

```python
Project
 └── FloorPlan               # 一层
      ├── Vertex  {id, xy}                    # 墙角 / T 形接头
      ├── Wall    {id, start, end, thickness, height, kind, source, confidence}
      ├── Opening {id, wall, kind, offset, width, height, sill, swing}
      ├── RoomLabel {name, xy, kind}          # 房间命名锚点
      └── meta
```

**关键点：除了上面这些，什么都不存。**

派生、永不落盘的：墙体实体轮廓（`WallBody`）、房间多边形与面积（`Room`）、
建筑外轮廓、3D 三角网格、统计信息。

### 为什么这么设计

编辑器的核心矛盾是"改了墙，房间的面积/周长/墙面积/三维模型都要跟着对"。
如果把它们存下来，就必须手工维护一致性 —— 这是绝大多数户型编辑器出现
"面积不刷新""房间多边形和新墙不一致"的根源。

派生方案的代价是每次编辑要重算一遍（本项目 9 面墙的户型重算 < 1ms，
包括三维网格），完全可接受。

### 拓扑共享

`Wall` 引用 `Vertex.id`，所以**拖动一个墙角会同时带动所有相连的墙**，
不需要额外的一致性维护。`FloorPlan.add_vertex()` 默认在 5mm 内焊接，
保证"画墙时接上了就真的接上了"。

### 门窗的定位方式

门窗用「距墙 `start` 端的距离 `offset`」定位，而不是存世界坐标。
好处：墙被移动/旋转/改长度后，门窗自动跟着走；改门窗只需改一个标量。
坏处：墙被大幅缩短时门窗可能超出墙端 —— 由 `update_opening` 里的夹取逻辑兜住。

---

## 3. 几何内核的三个关键决定

### 3.1 房间 = 平面图的面（不用布尔运算）

```
墙中线 → 顶点焊接 + 交点打断（topology.normalize_graph）
       → 半边遍历取所有面（topology.planar_faces）
       → 每个面按各边所属墙厚向内偏移半个墙厚（model.offset_ring）
       = 房间净轮廓
```

半边遍历的规则：半边 `(u→v)` 的下一条 = 在 `v` 处、从 `(v→u)` 开始**顺时针方向最近**的出边。
这样绕着走一圈就得到该半边左侧的面，有界面（正面积）就是房间。

相比"墙实体求并集 → 取补集 → 连通域"：

| | 半边遍历 | 布尔运算 |
|---|---|---|
| 速度 | O(E log E) | 依赖库，慢一个数量级 |
| 边界情况 | 少（纯图论） | 共线/重合/微小重叠都要特判 |
| 改一面墙的影响 | 相邻两房间自动更新 | 需要重算整张图的并集 |
| 依赖 | 零 | 需要 shapely/CGAL |

代价：非闭合的墙（悬空端点）不产生房间，`validate()` 会报警。这反而是想要的行为。

### 3.2 墙体 = 参数化四边形（洞口不用布尔运算）

每片墙的平面实体是一个四边形，用**左右两条边各自的起止点**表示：

```
p0L ─────────── p1L          (+) 左侧（法向 +n）
p0R ─────────── p1R          (−) 右侧
```

沿长度 `s ∈ [0,1]` 线性插值即可得到任意位置的左右边界，于是：

```python
body.sub(s0, s1)          # 任意子段的多边形 —— 开洞就用它
body.opening_frame(offset, width)   # 洞口 → 参数区间
body.boxes_with_openings(...)       # 切出「洞口下墙/过梁/洞口间墙段」盒体
```

**转角处理**：

- 二度节点（两墙相接）→ **角度平分线斜接**：分别求本墙两个侧面与邻墙对应侧面的交点，
  连线即接缝。这样转角处两片墙正好拼合，无缺口无重叠。
  "对应侧面"的配对（同侧 / 交叉）由「接缝应平行于两墙方向的角平分线」来判定 ——
  这个判据在不等厚墙、非 90° 转角下都成立。
- 其它节点（T 型、十字、悬空端）→ 平头封口 + 沿自身方向外扩一个"邻墙最大半厚"，
  保证与邻墙**重叠而不留缝**，且外扩量恰好停在邻墙外表面，不会戳出墙面。

### 3.3 几何只算一次，算在 Python 里

浏览器只做两件事：渲染、把用户操作翻译成 op。

```
浏览器                                   Python（模型服务）
  ├─ 拖墙角 → {op:"move_vertex", id, xy}
  ├─ 输入长度 → {op:"set_wall_length", id, length}
  └─ POST /api/edit {project, ops}  ───►  ops.apply_ops() → 重算派生
                                          ←──  {project, derived}（含网格 base64）
```

避免"前端一套几何、后端一套几何"逐渐不一致。代价是每次编辑一次本地 HTTP 往返
（localhost，实测 < 20ms），对交互无感。

---

## 4. 依赖图

```
geom.py            ← 无依赖（numpy）
topology.py        ← geom
wallbody.py        ← geom
model.py           ← geom, topology, wallbody
mesh.py            ← model, geom
derived.py         ← model, mesh
ops.py             ← model, geom
exporters/*.py     ← model, mesh（彼此独立）
reconstruct/*.py   ← model, geom（不依赖 mesh/exporters）
derived → server.py ← ops, derived, exporters
cli.py             ← 全部
web/*.js           ← 只通过 HTTP 与 Python 交互
```

没有循环依赖。`reconstruct` 与 `exporters` 互不依赖，可以单独使用。

---

## 5. 扩展点（怎么加东西）

### 加一种导出格式

```python
# scan2plan/exporters/stl.py
def export_stl(mesh, path, **kw) -> dict: ...
def validate_stl(path) -> dict: ...

# 注册
scan2plan/exporters/__init__.py   # 加 import + __all__
scan2plan/server.py               # _export() 里加一个 elif
scan2plan/cli.py                  # cmd_export 的 formats 列表
```

`mesh.Mesh` 提供 `vertices / faces / face_materials / object_names / _object_faces`，
以及 `add_prism / add_polygon`，构建导出用的网格很方便。

### 加一个编辑操作

```python
# scan2plan/ops.py 的 apply_op() 里加一个分支
elif name == "mirror_plan":
    for v in fp.vertices.values():
        v.xy = (-v.xy[0], v.xy[1])
```

前端 `app.js` 里 `applyOps([{op:"mirror_plan"}])` 即可使用，
**不需要改前端其他代码**。

### 加一个重建算法

`reconstruct/` 里每个模块都是纯函数风格：
`PointCloud → 中间结果 → FloorPlan`。只要最终产出 `list[WallSegment]`
（`reconstruct/walls.py` 里的数据类）就能复用整套矢量化与房间派生。

---

## 6. 服务端线程模型

`server.py` 用标准库 `ThreadingHTTPServer`：

- 每个请求一个线程，无状态（`project` 由客户端随请求带上）；
- 无全局可变状态 → 不会因为并发编辑互相踩；
- 导出文件写在 `build/`，通过 `/files/<name>` 下载。

无状态的代价是每个请求都要反序列化 + 重算派生。对单用户本地工具完全够用；
要做多人协作时需要引入会话/增量更新，不在当前范围内。

---

## 7. 前端结构

```
web/
├── index.html      布局：顶栏 / 2D 视口 / 分隔条 / 3D 视口 / 右侧属性栏
├── style.css       深色主题
├── editor2d.js     Canvas2D：网格、房间填充、墙体、门窗符号、尺寸标注、
│                   命中测试、拖拽、吸附、缩放平移、比例尺
├── viewer3d.js     手写 WebGL1：按材质批渲染、面法向、轨道相机
└── app.js          状态管理、HTTP 调用、撤销重做（快照栈）、面板渲染、导出
```

零 npm 依赖、零构建步骤。改完 JS 刷新浏览器即生效。
