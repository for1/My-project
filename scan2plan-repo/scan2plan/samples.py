"""合成户型样例。

用途有二：
    1. 单元测试的基准真值（ground truth）。所有尺寸都是精确已知的，
       因此可以断言「恢复出来的净面积必须等于多少」。
    2. Web 编辑器与导出器的演示数据 —— 不需要手机、不需要重建，
       打开就能看到一套完整的两室户型。

坐标系：单位米，Y 向上。
"""
from __future__ import annotations

from .model import (
    DEFAULT_THICKNESS_EXT,
    DEFAULT_THICKNESS_INT,
    DEFAULT_WALL_HEIGHT,
    FloorPlan,
    Project,
    RoomLabel,
)

EXT = 0.24   # 外墙厚
INT = 0.12   # 内墙厚


def demo_apartment() -> Project:
    """一套 10m × 8m 的两室两厅户型（80 m² 建筑面积）。

    平面布局（中线坐标）::

        y=8  ┌──────────────┬──────────┐
             │              │          │
             │    主卧      │   餐厅   │
        y=4.8├──────────────┤          │
             │ 客厅 │ 卫 │厨 ├──────────┤ y=5.6
             │      │    │  │          │
             │      │    │  │  次卧    │
        y=0  └──────┴────┴──┴──────────┘
            x=0  3.6  6.0          10.0
    """
    fp = FloorPlan(name="1F", ceiling_height=DEFAULT_WALL_HEIGHT)
    fp.meta["source"] = "synthetic"
    fp.meta["truth"] = {
        "gross_outline": [10.0, 8.0],
        "built_area_m2": 80.0,
        "note": "所有墙中线位于给定坐标，可直接验算净面积",
    }

    # ---------------- 外墙（矩形环）
    A = fp.add_wall((0, 0), (10, 0), thickness=EXT, kind="exterior")
    B = fp.add_wall((10, 0), (10, 8), thickness=EXT, kind="exterior")
    C = fp.add_wall((10, 8), (0, 8), thickness=EXT, kind="exterior")
    D = fp.add_wall((0, 8), (0, 0), thickness=EXT, kind="exterior")

    # ---------------- 内墙：竖向主分隔 x=6.0
    v6 = fp.add_wall((6.0, 0), (6.0, 8.0), thickness=INT, kind="interior")
    # 左区：y=4.8 横墙（客厅/厨卫 与 主卧 分开）
    h48 = fp.add_wall((0, 4.8), (6.0, 4.8), thickness=INT, kind="interior")
    # 左区：x=3.6 竖墙（客厅 与 厨卫）
    v36 = fp.add_wall((3.6, 0), (3.6, 4.8), thickness=INT, kind="interior")
    # 厨卫之间：y=2.4 横墙（从 x=3.6 到 x=6.0）
    h24 = fp.add_wall((3.6, 2.4), (6.0, 2.4), thickness=INT, kind="interior")
    # 右区：y=5.6 横墙（餐厅 与 次卧）
    h56 = fp.add_wall((6.0, 5.6), (10.0, 5.6), thickness=INT, kind="interior")

    # ---------------- 房间命名锚点（放在各房间中心）
    fp.room_labels += [
        RoomLabel("主卧", (3.0, 6.4), kind="room"),
        RoomLabel("客厅", (1.8, 2.4), kind="room"),
        RoomLabel("卫生间", (4.8, 3.6), kind="bathroom"),
        RoomLabel("厨房", (4.8, 1.2), kind="kitchen"),
        RoomLabel("餐厅", (8.0, 6.8), kind="room"),
        RoomLabel("次卧", (8.0, 2.8), kind="room"),
    ]

    # ---------------- 门
    # 主卧门：开在 y=4.8 横墙上
    fp.add_opening(h48.id, "door", offset=1.6, width=0.90, height=2.10)
    # 厨房门：开在 x=3.6 竖墙下部
    fp.add_opening(v36.id, "door", offset=1.2, width=0.80, height=2.10)
    # 卫生间门：开在 x=3.6 竖墙上部
    fp.add_opening(v36.id, "door", offset=3.6, width=0.70, height=2.10)
    # 次卧（右）门：开在 x=6.0 竖墙下部
    fp.add_opening(v6.id, "door", offset=2.8, width=0.90, height=2.10)
    # 餐厅垭口：开在 x=6.0 竖墙上部
    fp.add_opening(v6.id, "passage", offset=6.8, width=1.60, height=2.40)

    # ---------------- 窗
    # 主卧北窗（y=8 外墙）
    fp.add_opening(C.id, "window", offset=4.0, width=1.80, height=1.50, sill=0.90)
    # 客厅西窗（x=0 外墙）
    fp.add_opening(D.id, "window", offset=3.6, width=1.60, height=1.50, sill=0.90)
    # 次卧南窗（y=0 外墙）
    fp.add_opening(A.id, "window", offset=8.0, width=1.80, height=1.50, sill=0.90)
    # 次卧东窗（x=10 外墙）
    fp.add_opening(B.id, "window", offset=2.8, width=1.20, height=1.50, sill=0.90)

    return Project(name="样例·两室两厅 80m²", plans=[fp])


def demo_single_room() -> Project:
    """单房间 4m × 3m，用于最小几何单元测试。"""
    fp = FloorPlan(name="1F")
    fp.add_wall((0, 0), (4, 0), thickness=0.20, kind="exterior")
    fp.add_wall((4, 0), (4, 3), thickness=0.20, kind="exterior")
    fp.add_wall((4, 3), (0, 3), thickness=0.20, kind="exterior")
    fp.add_wall((0, 3), (0, 0), thickness=0.20, kind="exterior")
    fp.room_labels.append(RoomLabel("房间", (2.0, 1.5)))
    return Project(name="单房间 4×3", plans=[fp])


def demo_L_room() -> Project:
    """L 形房间，用于验证非凸多边形的面积与偏移。"""
    fp = FloorPlan(name="1F")
    pts = [(0, 0), (6, 0), (6, 3), (3, 3), (3, 6), (0, 6)]
    for i in range(len(pts)):
        fp.add_wall(pts[i], pts[(i + 1) % len(pts)], thickness=0.20, kind="exterior")
    fp.room_labels.append(RoomLabel("L形空间", (1.5, 4.5)))
    return Project(name="L形房间", plans=[fp])


def demo_two_rooms() -> Project:
    """两个相邻房间共墙，用于验证共墙两侧房间面积。"""
    fp = FloorPlan(name="1F")
    fp.add_wall((0, 0), (8, 0), thickness=0.20, kind="exterior")
    fp.add_wall((8, 0), (8, 4), thickness=0.20, kind="exterior")
    fp.add_wall((8, 4), (0, 4), thickness=0.20, kind="exterior")
    fp.add_wall((0, 4), (0, 0), thickness=0.20, kind="exterior")
    fp.add_wall((5, 0), (5, 4), thickness=0.10, kind="interior")
    fp.room_labels.append(RoomLabel("左房", (2.5, 2.0)))
    fp.room_labels.append(RoomLabel("右房", (6.5, 2.0)))
    return Project(name="两房共墙", plans=[fp])
