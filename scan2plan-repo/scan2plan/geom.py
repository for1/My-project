"""二维几何内核。

坐标约定（全工程统一，务必遵守）：
    * 单位：米（float）。仅在导出 CAD 时才换算成毫米。
    * 平面：X 向右（东），Y 向上（北）。与 CAD 世界坐标系一致。
    * 高程：Z 向上，楼板面 z=0。
    * 角度：度（deg），数学正方向（逆时针为正），0° 指向 +X。

本模块只依赖 numpy，不做任何 I/O。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

XY = tuple[float, float]

TWO_PI = 2.0 * math.pi
EPS = 1e-9


# ---------------------------------------------------------------- 基本向量


def as_xy(p: Sequence[float]) -> np.ndarray:
    """把任意长度≥2 的序列转成长度 2 的 float ndarray。"""
    a = np.asarray(p, dtype=float)
    if a.shape != (2,):
        raise ValueError(f"expected a 2D point, got {a.shape}")
    return a


def vec(x: float, y: float) -> np.ndarray:
    return np.array([x, y], dtype=float)


def length(v: Sequence[float]) -> float:
    return float(math.hypot(v[0], v[1]))


def unit(v: Sequence[float]) -> np.ndarray:
    n = length(v)
    if n < EPS:
        return np.zeros(2)
    return np.array([v[0] / n, v[1] / n], dtype=float)


def perp(v: Sequence[float]) -> np.ndarray:
    """逆时针旋转 90°。"""
    return np.array([-v[1], v[0]], dtype=float)


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0] * b[0] + a[1] * b[1])


def cross(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def rot(a_deg: float) -> np.ndarray:
    """旋转矩阵（逆时针为正）。"""
    r = math.radians(a_deg)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s], [s, c]], dtype=float)


def rot_about(p: Sequence[float], pivot: Sequence[float], a_deg: float) -> np.ndarray:
    p = as_xy(p)
    pivot = as_xy(pivot)
    return pivot + rot(a_deg) @ (p - pivot)


def move_along(p: Sequence[float], direction: Sequence[float], dist: float) -> np.ndarray:
    return as_xy(p) + unit(direction) * dist


# ---------------------------------------------------------------- 角度工具


def angle_of(v: Sequence[float]) -> float:
    """向量方向角，度，范围 (-180, 180]。"""
    return math.degrees(math.atan2(v[1], v[0]))


def angle_between(a: Sequence[float], b: Sequence[float]) -> float:
    """两个向量夹角，度，范围 [0, 180]。"""
    na, nb = length(a), length(b)
    if na < EPS or nb < EPS:
        return 0.0
    c = max(-1.0, min(1.0, dot(a, b) / (na * nb)))
    return math.degrees(math.acos(c))


def normalize_deg(a_deg: float) -> float:
    """归一到 (-180, 180]。"""
    a = math.fmod(a_deg, 360.0)
    if a <= -180.0:
        a += 360.0
    elif a > 180.0:
        a -= 360.0
    return a


def snap_angle(a_deg: float, step: float = 90.0) -> float:
    """把角度吸附到 step 的整数倍（默认 90° = 强制正交）。

    step 传 15 可得到建筑制图常用的 15° 吸附。
    """
    if step <= 0:
        return a_deg
    return normalize_deg(round(a_deg / step) * step)


def snap_angle_towards(a_deg: float, target_deg: float, tol: float) -> float:
    """若 a 与 target 相差在 tol 度内则吸附到 target。"""
    d = abs(normalize_deg(a_deg - target_deg))
    return target_deg if d <= tol else a_deg


def is_orthogonal(a_deg: float, tol: float = 1e-6) -> bool:
    return abs(normalize_deg(a_deg - round(a_deg / 90.0) * 90.0)) <= tol


# ---------------------------------------------------------------- 线段 / 多边形


def seg_closest_point(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> np.ndarray:
    """点 p 到线段 ab 的最近点。"""
    p, a, b = as_xy(p), as_xy(a), as_xy(b)
    ab = b - a
    L2 = float(dot(ab, ab))
    if L2 < EPS:
        return a.copy()
    t = max(0.0, min(1.0, float(dot(p - a, ab)) / L2))
    return a + ab * t


def seg_point_distance(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    return length(as_xy(p) - seg_closest_point(p, a, b))


def seg_intersect(
    a1: Sequence[float], a2: Sequence[float], b1: Sequence[float], b2: Sequence[float]
) -> np.ndarray | None:
    """线段求交；平行/共线/不相交返回 None。"""
    p, r = as_xy(a1), as_xy(a2) - as_xy(a1)
    q, s = as_xy(b1), as_xy(b2) - as_xy(b1)
    denom = cross(r, s)
    if abs(denom) < EPS:
        return None
    t = cross(q - p, s) / denom
    u = cross(q - p, r) / denom
    if -EPS <= t <= 1.0 + EPS and -EPS <= u <= 1.0 + EPS:
        return p + r * t
    return None


def line_intersect_infinite(
    a1: Sequence[float], a2: Sequence[float], b1: Sequence[float], b2: Sequence[float]
) -> np.ndarray | None:
    """无限长直线求交，平行返回 None。"""
    p, r = as_xy(a1), as_xy(a2) - as_xy(a1)
    q, s = as_xy(b1), as_xy(b2) - as_xy(b1)
    denom = cross(r, s)
    if abs(denom) < EPS:
        return None
    t = cross(q - p, s) / denom
    return p + r * t


def polygon_area(ring: Sequence[Sequence[float]]) -> float:
    """有符号面积（逆时针为正）。"""
    n = len(ring)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def polygon_perimeter(ring: Sequence[Sequence[float]]) -> float:
    n = len(ring)
    if n < 2:
        return 0.0
    return sum(length(as_xy(ring[i]) - as_xy(ring[(i + 1) % n])) for i in range(n))


def polygon_centroid(ring: Sequence[Sequence[float]]) -> np.ndarray:
    """面积质心（退化时回退到顶点平均）。"""
    a = polygon_area(ring)
    if abs(a) < EPS:
        pts = np.array([as_xy(p) for p in ring], dtype=float)
        return pts.mean(axis=0) if len(pts) else np.zeros(2)
    cx = cy = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return np.array([cx, cy], dtype=float) / (6.0 * a)


def point_in_ring(p: Sequence[float], ring: Sequence[Sequence[float]]) -> bool:
    """射线法判断点是否在简单多边形内部（不处理自交）。"""
    x, y = as_xy(p)
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-300) + x1
            if x < xint:
                inside = not inside
    return inside


def dedupe_ring(ring: Sequence[Sequence[float]], tol: float = 1e-6) -> list[XY]:
    """去掉重复点与共线点，返回逆时针方向的新环。"""
    pts = [as_xy(p) for p in ring]
    if len(pts) < 2:
        return [(float(p[0]), float(p[1])) for p in pts]
    out: list[np.ndarray] = []
    for p in pts:
        if not out or length(p - out[-1]) > tol:
            out.append(p)
    if len(out) > 1 and length(out[0] - out[-1]) <= tol:
        out.pop()
    if len(out) < 3:
        return [(float(p[0]), float(p[1])) for p in out]
    # 去掉共线点
    keep: list[np.ndarray] = []
    n = len(out)
    for i in range(n):
        prev, cur, nxt = out[i - 1], out[i], out[(i + 1) % n]
        if abs(cross(cur - prev, nxt - cur)) > tol * max(1.0, length(cur - prev)):
            keep.append(cur)
    if len(keep) < 3:
        keep = out
    if polygon_area(keep) < 0:
        keep.reverse()
    return [(float(p[0]), float(p[1])) for p in keep]


def ring_signed_area(ring: Sequence[Sequence[float]]) -> float:
    return polygon_area(ring)


def polyline_length(pts: Iterable[Sequence[float]]) -> float:
    seq = [as_xy(p) for p in pts]
    return sum(length(seq[i + 1] - seq[i]) for i in range(len(seq) - 1))


def bbox(pts: Iterable[Sequence[float]]) -> tuple[float, float, float, float]:
    a = np.array([as_xy(p) for p in pts], dtype=float)
    if a.size == 0:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max()))


# ---------------------------------------------------------------- 拟合


def fit_line_pca(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """对一组 2D 点做 PCA 直线拟合。

    返回 (点, 单位方向, 残差 RMS)。方向取主成分方向。
    """
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    c = pts.mean(axis=0)
    if len(pts) < 2:
        return c, np.array([1.0, 0.0]), 0.0
    d = pts - c
    cov = d.T @ d / len(pts)
    w, v = np.linalg.eigh(cov)
    direction = v[:, int(np.argmax(w))]
    resid = d @ np.array([-direction[1], direction[0]])
    return c, direction, float(np.sqrt(np.mean(resid**2)))


def fit_angle_histogram(angles_deg: Sequence[float], bin_deg: float = 0.5) -> float:
    """从一堆方向角里找出主方向（0~180 折叠，因为墙有方向性）。

    曼哈顿假设下这个值应接近 0/90 之一；返回主方向角度。
    """
    if not angles_deg:
        return 0.0
    folded = [math.fmod(a % 180.0, 180.0) for a in angles_deg]
    nbins = max(1, int(round(180.0 / bin_deg)))
    hist = np.zeros(nbins)
    for a in folded:
        hist[int(a / bin_deg) % nbins] += 1.0
    # 圆周平滑，避免 bin 边界抖动
    k = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    k /= k.sum()
    smooth = np.convolve(np.concatenate([hist[-2:], hist, hist[:2]]), k, mode="same")[2:-2]
    peak = int(np.argmax(smooth))
    # 用峰值邻域做加权平均，得到亚 bin 精度
    lo, hi = peak - 1, peak + 1
    idx = [(i % nbins) for i in range(lo, hi + 1)]
    w = np.array([smooth[i] for i in idx])
    a = np.array([(i % nbins) * bin_deg for i in idx])
    if w.sum() < EPS:
        return float(peak * bin_deg)
    return float((a * w).sum() / w.sum())


def manhattanize_ring(ring: Sequence[Sequence[float]], snap_deg: float = 90.0, origin_deg: float = 0.0) -> list[XY]:
    """把房间轮廓逐边吸附到正交网格，并保持顶点连通（交替投影）。

    这是"矢量化后处理"的关键一步：让恢复出来的墙体横平竖直。
    """
    pts = [as_xy(p) for p in ring]
    if len(pts) < 3:
        return [(float(p[0]), float(p[1])) for p in pts]
    # 每条边的方向吸附
    out: list[np.ndarray] = []
    n = len(pts)
    dirs: list[np.ndarray] = []
    for i in range(n):
        d = pts[(i + 1) % n] - pts[i]
        a = snap_angle(angle_of(d) - origin_deg, snap_deg) + origin_deg
        dirs.append(unit(vec(math.cos(math.radians(a)), math.sin(math.radians(a)))))
    # 交替投影：每轮重新求交相邻边得到顶点
    cur = [p.copy() for p in pts]
    for _ in range(8):
        moved = 0.0
        for i in range(n):
            p_prev, p_next = cur[i - 1], cur[(i + 1) % n]
            d_prev, d_next = dirs[i - 1], dirs[i]
            q = line_intersect_infinite(p_prev, p_prev + d_prev, p_next, p_next + d_next)
            if q is None:
                continue
            moved = max(moved, length(q - cur[i]))
            cur[i] = q
        if moved < 1e-4:
            break
    return dedupe_ring(cur)
