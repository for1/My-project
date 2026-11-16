"""合成点云生成器 —— 重建管线的基准真值来源。

为什么要这个：
    「精度 1%」这句话必须能被验证。真机数据无法给出精确真值，
    而合成数据可以：先有已知户型 → 渲染成带噪声、带遮挡、带家具干扰的点云
    → 跑重建 → 与真值逐墙比对。误差是算出来的，不是吹出来的。

模拟的内容（都是真机上的主要误差源）：
    * 深度噪声：高斯噪声，随距离增大
    * 遮挡：只有朝向某个相机视点的表面才被采到（模拟走动拍摄）
    * 缺失：随机丢弃一部分点（反光、玻璃、暗角）
    * 家具：房间里放几个盒子，制造非墙面的垂直平面干扰
    * 地面/天花：可选，用于估计层高
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..geom import as_xy, length, perp, unit
from ..model import FloorPlan, Project
from ..wallbody import build_wall_bodies
from .pointcloud import PointCloud


@dataclass
class SynthOptions:
    point_spacing: float = 0.035      # 墙面上点的平均间距（米）
    noise: float = 0.004             # 深度噪声标准差（米）
    dropout: float = 0.10            # 随机丢弃比例
    occluded: bool = True            # 是否按相机视点做遮挡剔除
    furniture: bool = True           # 是否加入家具盒子
    floor: bool = True               # 是否加入地面点
    ceiling: bool = True             # 是否加入天花点
    height_min: float = 0.12         # 采样高度下界（模拟设备略高于地面）
    height_max: float = 2.55         # 采样高度上界
    seed: int = 0


def _surface_points(
    a, b, z0: float, z1: float, spacing: float
) -> np.ndarray:
    """在竖直面（线段 a-b 沿高度 z0..z1）上均匀撒点。a/b 允许 2D 或 3D。"""
    a3 = np.array([float(a[0]), float(a[1]), 0.0])
    b3 = np.array([float(b[0]), float(b[1]), 0.0])
    L = float(np.hypot(*(b3 - a3)[:2]))
    if L < 1e-6:
        return np.zeros((0, 3))
    n_along = max(2, int(L / spacing))
    n_up = max(2, int((z1 - z0) / spacing))
    ts = (np.arange(n_along) + 0.5) / n_along
    zs = z0 + (np.arange(n_up) + 0.5) / n_up * (z1 - z0)
    p = a3[None, :] + (b3 - a3)[None, :] * ts[:, None]
    grid = np.repeat(p, n_up, axis=0)
    grid[:, 2] = np.tile(zs, n_along)
    return grid


def _sample_wall_face(
    body, side: int, opt: SynthOptions
) -> np.ndarray:
    """取墙体某一侧表面（side=+1 左面, -1 右面）的采样点。"""
    left0, left1 = body.p0L, body.p1L
    right0, right1 = body.p0R, body.p1R
    a, b = (left0, left1) if side > 0 else (right0, right1)
    return _surface_points(a, b, opt.height_min, opt.height_max, opt.point_spacing)


def _visible(points: np.ndarray, eye: np.ndarray, normal: np.ndarray, tol: float = 0.35) -> np.ndarray:
    """粗略可见性：点法向与「点→相机」方向夹角小于 ~70° 视为可见。"""
    if len(points) == 0:
        return np.zeros(0, dtype=bool)
    v = eye[None, :] - points
    vn = np.linalg.norm(v, axis=1, keepdims=True)
    vn[vn < 1e-9] = 1.0
    cosang = (v / vn) @ normal
    return cosang > tol


def generate_point_cloud(plan: FloorPlan, opt: SynthOptions | None = None) -> PointCloud:
    """由已知户型生成带噪声的室内点云。"""
    opt = opt or SynthOptions()
    rng = np.random.default_rng(opt.seed)
    base = float(plan.elevation)
    bodies = build_wall_bodies(
        [
            (w.id, plan.vertices[w.start].xy, plan.vertices[w.end].xy, w.thickness)
            for w in plan.walls.values()
        ]
    )

    # 相机视点：每个房间中心 + 一点高度（模拟人站着拍）
    eyes: list[np.ndarray] = []
    for room in plan.rooms():
        eyes.append(np.array([room.centroid[0], room.centroid[1], base + 1.45]))
    if not eyes:
        eyes.append(np.array([0.0, 0.0, base + 1.45]))

    chunks: list[np.ndarray] = []
    for body in bodies:
        d = body.direction
        if float(np.hypot(d[0], d[1])) < 1e-9:
            continue
        n = perp(d)
        wall = plan.walls.get(body.wall_id)
        H = float(wall.height) if wall else plan.ceiling_height
        L = body.length
        ops = plan.openings_of(body.wall_id) if wall else []
        p0_3d = np.array([body.p0[0], body.p0[1], 0.0])
        d3 = np.array([d[0], d[1], 0.0])
        n3 = np.array([n[0], n[1], 0.0])
        for side in (1, -1):
            pts = _sample_wall_face(body, side, opt)
            if not len(pts):
                continue
            # 按门窗洞口在墙面上"挖洞"（真实扫描里洞口当然没有点）
            if ops:
                along = (pts[:, :2] - body.p0) @ d
                keep = np.ones(len(pts), dtype=bool)
                for o in ops:
                    s0, s1 = body.opening_frame(o.offset, o.width)
                    a0, a1 = s0 * L, s1 * L
                    z0 = float(o.sill)
                    z1 = float(min(o.sill + o.height, H))
                    inside = (
                        (along >= a0) & (along <= a1) & (pts[:, 2] >= z0) & (pts[:, 2] <= z1)
                    )
                    keep &= ~inside
                pts = pts[keep]
            if opt.occluded:
                nrm = np.array([n[0] * side, n[1] * side, 0.0])
                keep = np.zeros(len(pts), dtype=bool)
                for eye in eyes:
                    keep |= _visible(pts, eye, nrm)
                pts = pts[keep]
            if len(pts):
                chunks.append(pts)

            # 洞口侧壁（门套/窗洞侧边）——这是重建时最容易误导聚类的点
            for o in ops:
                s0, s1 = body.opening_frame(o.offset, o.width)
                z0 = float(o.sill)
                z1 = float(min(o.sill + o.height, H))
                if z1 - z0 < 0.05:
                    continue
                for a in (s0 * L, s1 * L):
                    base_pt = p0_3d + d3 * a
                    p_left = base_pt + n3 * (body.thickness * 0.5)
                    p_right = base_pt - n3 * (body.thickness * 0.5)
                    reveal = _surface_points(
                        p_left, p_right, z0, z1, opt.point_spacing * 1.2
                    )
                    if len(reveal):
                        chunks.append(reveal)

    # 地面 / 天花
    if opt.floor or opt.ceiling:
        for room in plan.rooms():
            ring = np.array(room.polygon, dtype=float)
            lo = ring.min(axis=0)
            hi = ring.max(axis=0)
            n = max(4, int(np.hypot(*(hi - lo)) / opt.point_spacing))
            xs = np.linspace(lo[0], hi[0], n)
            ys = np.linspace(lo[1], hi[1], n)
            gx, gy = np.meshgrid(xs, ys)
            flat = np.stack([gx.ravel(), gy.ravel()], axis=1)
            # 只保留房间内部点（用射线法）
            from ..geom import point_in_ring

            mask = np.array([point_in_ring(p, room.polygon) for p in flat])
            flat = flat[mask]
            if opt.floor:
                f = np.column_stack(
                    [flat, np.full(len(flat), base + 0.01)]
                )
                chunks.append(f)
            if opt.ceiling:
                c = np.column_stack(
                    [flat, np.full(len(flat), base + plan.ceiling_height - 0.01)]
                )
                chunks.append(c)

    # 家具：房间里放几个盒子，制造额外垂直面
    if opt.furniture:
        for i, room in enumerate(plan.rooms()):
            cx, cy = room.centroid
            for k in range(rng.integers(1, 3)):
                w = float(rng.uniform(0.4, 1.2))
                dd = float(rng.uniform(0.35, 0.7))
                h = float(rng.uniform(0.4, 0.9))
                ang = float(rng.uniform(0, np.pi))
                c, s = np.cos(ang), np.sin(ang)
                ox = cx + float(rng.uniform(-0.25, 0.25)) * np.sqrt(max(room.net_area, 1))
                oy = cy + float(rng.uniform(-0.25, 0.25)) * np.sqrt(max(room.net_area, 1))
                corners = [
                    (-w / 2, -dd / 2), (w / 2, -dd / 2), (w / 2, dd / 2), (-w / 2, dd / 2)
                ]
                pts2 = [(ox + c * x - s * y, oy + s * x + c * y) for x, y in corners]
                for e in range(4):
                    p0 = np.array([pts2[e][0], pts2[e][1], 0.0])
                    p1 = np.array([pts2[(e + 1) % 4][0], pts2[(e + 1) % 4][1], 0.0])
                    seg = _surface_points(p0, p1, base + 0.02, base + h, opt.point_spacing * 1.4)
                    chunks.append(seg)
                # 顶面
                top = np.array([[p[0], p[1], base + h] for p in pts2])
                chunks.append(top)

    if not chunks:
        return PointCloud(np.zeros((0, 3)))
    pts = np.vstack(chunks)

    # 噪声
    if opt.noise > 0:
        pts += rng.normal(0.0, opt.noise, pts.shape)
    # 随机丢弃（模拟反光/玻璃/缺失）
    if opt.dropout > 0:
        keep = rng.random(len(pts)) > opt.dropout
        pts = pts[keep]

    meta = {
        "synthetic": True,
        "seed": opt.seed,
        "source": "generate_point_cloud",
        "params": opt.__dict__,
    }
    return PointCloud(pts, meta=meta)


def generate_truth_project() -> Project:
    """生成一套"真值户型"，供精度评估使用。"""
    from .. import samples

    return samples.demo_apartment()
