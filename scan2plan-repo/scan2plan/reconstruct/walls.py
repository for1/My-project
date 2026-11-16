"""从点云提取墙体：墙面平面 → 墙中线 + 厚度 + 门窗洞口。

流程（对应室内深度扫描的实际做法）：
    1. 体素化 + 法向估计（planes.voxel_normals）
    2. 估计曼哈顿主方向，把点云旋转到墙体与坐标轴对齐（planes.estimate_yaw）
    3. 竖直面按法向分成 X 向 / Y 向两组
    4. 同组内按「法向坐标」一维聚类 → 每簇是一个墙面平面
    5. 相邻、反向、间距在 [5cm, 45cm] 的两个平面配成一面墙
       → 中线 = 两面中点，厚度 = 两面间距（这是**量出来的**厚度，不是猜的）
    6. 只有单面的墙（外墙面只被拍到一侧）：向内偏移半个默认墙厚
    7. 沿墙长度方向检测「点覆盖空缺」：
         中段无点 + 底部无墙 → 门
         中段无点 + 底部有墙 → 窗
       从而自动识别门窗洞口
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .planes import (
    VoxelData,
    cluster_1d,
    estimate_yaw,
    peak_clusters,
    robust_mean,
    trim_extent_by_height,
    voxel_normals,
)
from .pointcloud import PointCloud


@dataclass
class ReconstructionOptions:
    voxel_size: float = 0.03
    normal_radius: int = 1
    height_min: float = 0.25
    height_max: float = 2.35
    planarity_min: float = 0.45
    axis_cos_min: float = 0.72          # 判定法向落在某坐标轴上的阈值
    # 墙面一维聚类（直方图峰检测）
    plane_bin: float = 0.01
    plane_peak_ratio: float = 0.12
    plane_min_sep: float = 0.05
    plane_assign_tol: float = 0.025
    extent_bin: float = 0.06             # 沿墙方向高度判据的 bin
    extent_top_tolerance: float = 0.45   # 离墙顶多少米以内算"到达墙顶"
    cluster_gap: float = 0.045          # 兼容旧参数（间隔聚类用）
    min_plane_points: int = 150
    min_plane_height: float = 1.20       # 墙面必须通高（家具面只有 0.5~0.9m，用这个剔除）
    min_wall_length: float = 0.35
    merge_coord_tol: float = 0.03
    merge_gap: float = 0.22
    pair_min_thickness: float = 0.05
    pair_max_thickness: float = 0.45
    overlap_min_ratio: float = 0.45
    thickness_ext: float = 0.24
    thickness_int: float = 0.12
    wall_height: float = 2.80
    manhattan: bool = True
    detect_openings: bool = True
    opening_min_width: float = 0.55
    opening_max_width: float = 3.20
    opening_coverage: float = 0.35
    sample_step: float = 0.05


@dataclass
class WallPlane:
    """一个墙面平面（点云里实际看到的一个面）。"""

    axis: int              # 0: 法向沿 X（即墙沿 Y 走向）; 1: 法向沿 Y
    coord: float           # 平面常量坐标
    start: float           # 沿墙轴向上的起点
    end: float             # 沿墙轴向上的终点
    count: int
    rms: float
    used: bool = False

    @property
    def along(self) -> int:
        return 1 - self.axis

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class WallSegment:
    """重建出来的一段墙（中线 + 厚度）。"""

    p0: np.ndarray
    p1: np.ndarray
    thickness: float
    kind: str = "interior"
    faces: tuple[float, float] | None = None   # 配对面坐标（调试用）
    openings: list[tuple[float, float, str]] = field(default_factory=list)

    @property
    def length(self) -> float:
        return float(np.hypot(*(self.p1 - self.p0)))

    @property
    def direction(self) -> np.ndarray:
        d = self.p1 - self.p0
        n = np.hypot(*d) or 1.0
        return d / n

    def offset_along(self, p) -> float:
        d = self.direction
        return float((np.asarray(p) - self.p0) @ d)


@dataclass
class WallExtraction:
    segments: list[WallSegment]
    planes: list[WallPlane]
    yaw_rad: float
    yaw_confidence: float
    cloud_aligned: np.ndarray      # 旋转后的点（轴对齐坐标系）
    voxel: VoxelData
    diagnostics: dict


# ================================================================ 平面提取


def _planes_of_axis(
    pts: np.ndarray,
    normals: np.ndarray,
    axis: int,
    opt: ReconstructionOptions,
    diag: dict,
) -> list[WallPlane]:
    other = 1 - axis
    sel = (
        (np.abs(normals[:, axis]) >= opt.axis_cos_min)
        & (np.abs(normals[:, 2]) < 0.45)
    )
    if sel.sum() < opt.min_plane_points:
        return []
    idx = np.nonzero(sel)[0]
    coord = pts[idx, axis]
    along = pts[idx, other]
    zvals = pts[idx, 2]

    clusters = peak_clusters(
        coord,
        bin_width=opt.plane_bin,
        min_ratio=opt.plane_peak_ratio,
        min_sep=opt.plane_min_sep,
        assign_tol=opt.plane_assign_tol,
        min_points=opt.min_plane_points,
    )
    planes: list[WallPlane] = []
    rejected_low = 0
    trimmed = 0
    for c, member in clusters:
        t = along[member]
        lo, hi, note = trim_extent_by_height(
            t,
            zvals[member],
            bin_width=opt.extent_bin,
            top_tolerance=opt.extent_top_tolerance,
        )
        if hi - lo < opt.min_wall_length:
            lo, hi = float(np.percentile(t, 1.0)), float(np.percentile(t, 99.0))
            if hi - lo < opt.min_wall_length:
                continue
        if note == "trimmed":
            trimmed += 1
        # 关键过滤：墙是通高的（>2m），家具/柜子/台面只有 0.5~0.9m 高。
        # 用平面的竖直跨度就能把家具垂直面干净地剔除，避免它们被当成"墙"。
        zz = zvals[member]
        z0, z1 = np.percentile(zz, [2.0, 98.0])
        if (z1 - z0) < opt.min_plane_height:
            rejected_low += 1
            continue
        vals = coord[member]
        rms = float(np.sqrt(np.mean((vals - c) ** 2)))
        planes.append(
            WallPlane(
                axis=axis,
                coord=float(c),
                start=float(lo),
                end=float(hi),
                count=int(len(member)),
                rms=rms,
            )
        )
    diag[f"rejected_low_planes_axis{axis}"] = rejected_low
    diag[f"trimmed_extents_axis{axis}"] = trimmed
    diag[f"raw_planes_axis{axis}"] = len(planes)

    # 合并被噪声切断的同一面墙
    planes.sort(key=lambda p: p.coord)
    merged: list[WallPlane] = []
    for p in planes:
        if merged and abs(p.coord - merged[-1].coord) <= opt.merge_coord_tol:
            q = merged[-1]
            gap = max(p.start - q.end, q.start - p.end)
            if gap <= opt.merge_gap:
                q.coord = (q.coord * q.count + p.coord * p.count) / (q.count + p.count)
                q.start = min(q.start, p.start)
                q.end = max(q.end, p.end)
                q.count += p.count
                q.rms = max(q.rms, p.rms)
                continue
        merged.append(p)
    diag[f"merged_planes_axis{axis}"] = len(merged)
    return merged


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


# ================================================================ 配对成墙


def _pair_planes(
    planes: list[WallPlane], opt: ReconstructionOptions, diag: dict
) -> list[WallSegment]:
    segments: list[WallSegment] = []
    for axis in (0, 1):
        group = [p for p in planes if p.axis == axis]
        group.sort(key=lambda p: p.coord)
        for i, a in enumerate(group):
            if a.used:
                continue
            best = None
            best_score = -1.0
            for b in group[i + 1 :]:
                if b.used:
                    continue
                d = b.coord - a.coord
                if d < opt.pair_min_thickness:
                    continue
                if d > opt.pair_max_thickness:
                    break
                ov = _overlap(a.start, a.end, b.start, b.end)
                ratio = ov / max(min(a.length, b.length), 1e-6)
                if ratio < opt.overlap_min_ratio:
                    continue
                score = ratio * (1.0 - abs(d - 0.15) / opt.pair_max_thickness)
                if score > best_score:
                    best_score = score
                    best = (b, d, ov)
            if best is None:
                continue
            b, d, ov = best
            a.used = b.used = True
            lo = max(a.start, b.start)
            hi = min(a.end, b.end)
            mid = 0.5 * (a.coord + b.coord)
            if axis == 0:
                p0 = np.array([mid, lo]); p1 = np.array([mid, hi])
            else:
                p0 = np.array([lo, mid]); p1 = np.array([hi, mid])
            segments.append(
                WallSegment(p0=p0, p1=p1, thickness=float(d), kind="paired", faces=(a.coord, b.coord))
            )
    diag["paired_walls"] = len(segments)

    # 单面墙：向内偏移半个默认厚度
    if len(segments):
        all_pts_x = [s.p0[0] for s in segments] + [s.p1[0] for s in segments]
        all_pts_y = [s.p0[1] for s in segments] + [s.p1[1] for s in segments]
        cx = float(np.mean(all_pts_x))
        cy = float(np.mean(all_pts_y))
    else:
        cx = cy = 0.0
    for p in planes:
        if p.used:
            continue
        if p.axis == 0:
            sign = -1.0 if cx > p.coord else 1.0
            thick = opt.thickness_ext
            mid = p.coord + sign * thick * 0.5
            p0 = np.array([mid, p.start]); p1 = np.array([mid, p.end])
        else:
            sign = -1.0 if cy > p.coord else 1.0
            thick = opt.thickness_ext
            mid = p.coord + sign * thick * 0.5
            p0 = np.array([p.start, mid]); p1 = np.array([p.end, mid])
        segments.append(
            WallSegment(p0=p0, p1=p1, thickness=thick, kind="single", faces=(p.coord, p.coord))
        )
    diag["single_face_walls"] = sum(1 for s in segments if s.kind == "single")
    return segments


# ================================================================ 门窗洞口


def _detect_openings(
    seg: WallSegment,
    pts: np.ndarray,
    opt: ReconstructionOptions,
) -> list[tuple[float, float, str]]:
    """沿墙长度方向扫描点覆盖，找到门/窗洞口。"""
    L = seg.length
    if L < opt.opening_min_width * 2:
        return []
    d = seg.direction
    n = np.array([-d[1], d[0]])
    rel = pts[:, :2] - seg.p0
    along = rel @ d
    across = rel @ n
    band = np.abs(across) <= max(seg.thickness * 1.6, 0.28)
    if band.sum() < 30:
        return []
    z = pts[:, 2]

    positions = np.arange(0.0, L, opt.sample_step)
    slots = np.zeros(len(positions))
    low = np.zeros(len(positions))
    mid = np.zeros(len(positions))
    for i, s in enumerate(positions):
        m = band & (np.abs(along - s) <= opt.sample_step * 1.1)
        if not m.any():
            continue
        zz = z[m]
        n_all = len(zz)
        slots[i] = n_all
        low[i] = np.count_nonzero((zz >= 0.05) & (zz <= 0.45)) / max(n_all, 1)
        mid[i] = np.count_nonzero((zz >= 0.75) & (zz <= 1.75)) / max(n_all, 1)

    if slots.max() <= 0:
        return []
    ref = np.percentile(slots[slots > 0], 75) if np.any(slots > 0) else 0
    if ref < 3:
        return []

    # 中段严重缺点的位置 = 洞口候选
    hole = (slots < ref * opt.opening_coverage) | (mid < 0.25)
    if not hole.any():
        return []

    # 把连续空缺切成区间
    idx = np.nonzero(hole)[0]
    groups: list[list[int]] = []
    cur = [idx[0]]
    for k in idx[1:]:
        if k == cur[-1] + 1:
            cur.append(k)
        else:
            groups.append(cur)
            cur = [k]
    groups.append(cur)

    out: list[tuple[float, float, str]] = []
    for g in groups:
        s0 = positions[g[0]]
        s1 = positions[g[-1]] + opt.sample_step
        w = s1 - s0
        if w < opt.opening_min_width or w > opt.opening_max_width:
            continue
        # 洞口底部有没有墙点：有 → 窗；没有 → 门
        m = band & (along >= s0) & (along <= s1)
        if not m.any():
            kind = "door"
        else:
            zz = z[m]
            low_ratio = np.count_nonzero((zz >= 0.02) & (zz <= 0.55)) / max(len(zz), 1)
            kind = "window" if low_ratio > 0.30 else "door"
        # 贴墙端的洞口多半是扫描边界，不是真洞口
        if s0 < 0.08 or s1 > L - 0.08:
            continue
        out.append((float((s0 + s1) / 2), float(w), kind))
    return out


# ================================================================ 主入口


def extract_walls(
    cloud: PointCloud, opt: ReconstructionOptions | None = None
) -> WallExtraction:
    opt = opt or ReconstructionOptions()
    diag: dict = {"input_points": len(cloud)}
    pts = cloud.points
    if len(pts) < 200:
        raise ValueError(f"点云过少（{len(pts)} 点），无法重建")

    vd = voxel_normals(pts, opt.voxel_size, opt.normal_radius)
    diag["voxels"] = len(vd.points)

    yaw, conf = estimate_yaw(vd.normals, vd.planarity)
    diag["yaw_deg"] = round(math.degrees(yaw), 4)
    diag["yaw_confidence"] = round(conf, 4)

    # 旋转到轴对齐坐标系
    c, s = math.cos(-yaw), math.sin(-yaw)
    R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    raw_pts = pts @ R.T
    vox_pts = vd.points @ R.T
    vox_nrm = vd.normals @ R.T
    aligned_vox = VoxelData(vox_pts, vox_nrm, vd.planarity, vd.counts)

    # 墙面用「体素质心」提（去噪后的代表点），门窗检测用原始点（要密度）
    z = vox_pts[:, 2]
    zmin = float(np.percentile(raw_pts[:, 2], 1.0))
    in_band = (z >= zmin + opt.height_min) & (z <= zmin + opt.height_max)
    diag["voxels_in_wall_band"] = int(in_band.sum())

    planes: list[WallPlane] = []
    for axis in (0, 1):
        if not in_band.any():
            continue
        sub_pts = vox_pts[in_band]
        sub_n = vox_nrm[in_band]
        sub_plan = vd.planarity[in_band]
        sel = (np.abs(sub_n[:, 2]) < 0.45) & (sub_plan > opt.planarity_min)
        if sel.sum() < opt.min_plane_points:
            sel = np.abs(sub_n[:, 2]) < 0.45
        if sel.sum() < opt.min_plane_points:
            continue
        planes.extend(_planes_of_axis(sub_pts[sel], sub_n[sel], axis, opt, diag))

    segments = _pair_planes(planes, opt, diag)

    # 门窗洞口：用原始（未下采样）点，沿墙扫描点覆盖
    if opt.detect_openings:
        zr = raw_pts[:, 2]
        raw_band = (zr >= zmin + opt.height_min) & (zr <= zmin + opt.height_max)
        band_pts = raw_pts[raw_band]
        for seg in segments:
            seg.openings = _detect_openings(seg, band_pts, opt)
        diag["openings"] = sum(len(s.openings) for s in segments)

    # 过滤掉太短的墙
    segments = [s for s in segments if s.length >= opt.min_wall_length]
    diag["segments"] = len(segments)
    diag["total_wall_length"] = round(sum(s.length for s in segments), 3)

    return WallExtraction(
        segments=segments,
        planes=planes,
        yaw_rad=yaw,
        yaw_confidence=conf,
        cloud_aligned=raw_pts,
        voxel=aligned_vox,
        diagnostics=diag,
    )
