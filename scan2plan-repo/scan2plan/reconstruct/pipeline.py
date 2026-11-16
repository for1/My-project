"""重建管线：点云 → 可编辑户型图。

整体流程::

    点云 (PLY/PCD/NPY/相机轨迹导出)
      │
      ├─[1] 体素化 + 法向估计            planes.voxel_normals
      ├─[2] 曼哈顿主方向估计 + 轴对齐     planes.estimate_yaw
      ├─[3] 竖直墙面提取（一维聚类）      walls._planes_of_axis
      ├─[4] 双面配对 → 墙中线 + 实测厚度  walls._pair_planes
      ├─[5] 沿墙扫描 → 自动识别门窗洞口    walls._detect_openings
      ├─[6] 坐标归一 / 端点吸附 / 延伸     vectorize
      └─[7] 墙中线图 → 房间自动派生        model.FloorPlan.rooms
      ▼
    可编辑 FloorPlan（DXF / IFC / GLB / OBJ / FBX 随便导）

尺度：深度相机 / ARCore Depth 输出的点云本身是**米制**，不需要额外标定。
如果漂移导致整体比例偏差，用 ``FloorPlan.calibrate_by_wall`` 拿一段实测距离
（卷尺/激光测距仪）做一次等比校正即可 —— 这就是把误差压到 1% 的工程手段。
"""
from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from ..model import Project
from .pointcloud import PointCloud, load
from .vectorize import VectorizeOptions, segments_to_floorplan
from .walls import ReconstructionOptions, WallExtraction, extract_walls


def reconstruct(
    source: str | Path | PointCloud,
    *,
    options: ReconstructionOptions | None = None,
    vectorize_options: VectorizeOptions | None = None,
    name: str | None = None,
    verbose: bool = False,
) -> Project:
    """从点云重建一套户型图。

    source 可以是文件路径，也可以是已加载的 PointCloud。
    """
    opt = options or ReconstructionOptions()
    vopt = vectorize_options or VectorizeOptions()

    if isinstance(source, (str, Path)):
        cloud = load(source)
    else:
        cloud = source
    if verbose:
        print(f"[1/7] 点云: {cloud.stats()}")

    ext = extract_walls(cloud, opt)
    if verbose:
        for k, v in ext.diagnostics.items():
            print(f"      {k}: {v}")

    ceiling = _estimate_ceiling(cloud, ext)
    if verbose:
        print(f"[2/7] 主方向 {math.degrees(ext.yaw_rad):.3f}° (置信度 {ext.yaw_confidence:.2f})")
        print(f"[3/7] 墙面 {len(ext.planes)} 个 → 墙段 {len(ext.segments)} 条")
        print(f"[4/7] 估算层高 {ceiling:.2f} m")

    plan, vdiag = segments_to_floorplan(
        ext.segments,
        opt=opt,
        vopt=vopt,
        ceiling_height=ceiling,
        name=name or "1F",
    )

    proj = Project(name=name or "扫描户型", plans=[plan])
    proj.meta["source"] = "reconstruction"
    proj.meta["pipeline"] = {
        "options": asdict(opt),
        "vectorize": asdict(vopt),
        "pointcloud": cloud.stats(),
        "extract": ext.diagnostics,
        "vectorize_diag": vdiag,
        "yaw_deg": round(math.degrees(ext.yaw_rad), 4),
        "yaw_confidence": round(ext.yaw_confidence, 4),
        "ceiling_height_estimate": round(ceiling, 3),
    }
    proj.meta["accuracy_note"] = (
        "几何为米制绝对尺度；如需消除整体比例漂移，请用一段实测墙长调用 "
        "calibrate_by_wall() 做一次等比校正。"
    )
    if verbose:
        print(f"[5/7] 墙 {len(plan.walls)} 条 / 顶点 {len(plan.vertices)} 个")
        print(f"[6/7] 房间 {len(plan.rooms())} 间")
        print(f"[7/7] 完成")
    return proj


def _estimate_ceiling(cloud: PointCloud, ext: WallExtraction) -> float:
    """从点云估计层高：优先用天花的 z 分位数，否则用墙面上沿 + 推定。"""
    z = cloud.points[:, 2]
    if not len(z):
        return 2.8
    zlo = float(np.percentile(z, 0.5))
    zhi = float(np.percentile(z, 99.5))
    span = zhi - zlo
    # 如果点云里含天花（顶部有大量点），span 就接近层高
    if span > 2.0 and span < 6.0:
        return round(min(span, 4.5), 2)
    return 2.8


def project_report(project: Project) -> dict[str, Any]:
    """重建结果的诊断报告（可用于日志/接口）。"""
    fp = project.active
    rooms = fp.rooms()
    return {
        "walls": len(fp.walls),
        "vertices": len(fp.vertices),
        "openings": len(fp.openings),
        "rooms": [
            {
                "label": r.label,
                "net_area": round(r.net_area, 3),
                "perimeter": round(r.perimeter, 3),
                "centroid": [round(v, 3) for v in r.centroid],
            }
            for r in rooms
        ],
        "stats": fp.stats(),
        "problems": fp.validate(),
        "pipeline": project.meta.get("pipeline", {}),
    }
