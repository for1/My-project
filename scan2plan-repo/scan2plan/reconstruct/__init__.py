"""重建子包：点云 → 户型图。

    pointcloud  点云容器与 PLY/PCD/XYZ/NPY 读写
    planes      体素法向估计、曼哈顿主方向
    walls       竖直墙面提取 → 墙中线 + 实测厚度 + 门窗洞口
    vectorize   墙段矢量化 → 可编辑 FloorPlan（坐标归一/吸附/延伸/合并）
    pipeline    端到端管线
    synthetic   合成点云生成器（给精度评估提供真值）
    accuracy    重建结果与真值的逐墙误差统计
"""

from __future__ import annotations

from .accuracy import compare_plans, compare_projects, format_report
from .pipeline import project_report, reconstruct
from .pointcloud import PointCloud, load, save_ply
from .vectorize import VectorizeOptions, segments_to_floorplan
from .walls import ReconstructionOptions, WallExtraction, extract_walls

__all__ = [
    "PointCloud",
    "load",
    "save_ply",
    "ReconstructionOptions",
    "WallExtraction",
    "extract_walls",
    "VectorizeOptions",
    "segments_to_floorplan",
    "reconstruct",
    "project_report",
    "compare_plans",
    "compare_projects",
    "format_report",
]
