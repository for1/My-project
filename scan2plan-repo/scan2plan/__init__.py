"""scan2plan —— 手机室内拍摄 → 可编辑户型图 → CAD / 3D 模型。

模块地图::

    geom        二维几何内核（向量、多边形、拟合、吸附）
    topology    平面直线图：顶点焊接、交点打断、半边遍历取面
    wallbody    墙体实体轮廓：斜接接头、洞口切分
    model       FloorPlan / Project 数据模型、编辑操作、房间派生、JSON
    samples     合成户型（测试真值 + 演示数据）
    exporters   DXF / OBJ / GLB / IFC / FBX 导出
    reconstruct 点云 → 平面 → 墙中线 → 户型 → 整户拼接
"""

from __future__ import annotations

__version__ = "0.1.0"

from .model import (
    DEFAULT_THICKNESS_EXT,
    DEFAULT_THICKNESS_INT,
    DEFAULT_WALL_HEIGHT,
    FORMAT,
    SCHEMA_VERSION,
    FloorPlan,
    Opening,
    Project,
    Room,
    RoomLabel,
    Vertex,
    Wall,
    offset_ring,
)
from .wallbody import WallBody, build_wall_bodies

__all__ = [
    "FloorPlan",
    "Project",
    "Room",
    "Wall",
    "Opening",
    "Vertex",
    "RoomLabel",
    "WallBody",
    "build_wall_bodies",
    "offset_ring",
    "DEFAULT_THICKNESS_EXT",
    "DEFAULT_THICKNESS_INT",
    "DEFAULT_WALL_HEIGHT",
    "FORMAT",
    "SCHEMA_VERSION",
    "__version__",
]
