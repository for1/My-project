"""点云法向估计与主方向（曼哈顿）估计。

全部用 numpy 向量化实现，不依赖 scipy / open3d：

    1. 体素化：把点云压到规则网格，每个体素取质心，去噪 + 降采样。
    2. 法向：对每个体素，在 3×3×3 邻域上做 PCA，
       最小特征值对应的特征向量就是局部平面法向；planarity 用来剔除棱边。
    3. 主方向：室内场景基本满足曼哈顿假设（墙互相垂直、多为 0/90°），
       把所有竖直面的法向投影到 XY 平面，做 90° 折叠的角度直方图，
       峰值就是整栋房子的主朝向（允许任意旋转的户型）。

用近似 1 米/秒的纯 Python 速度换零依赖，50 万点大约 3~5 秒。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .pointcloud import PointCloud


# ================================================================ 体素 + 法向


def _encode(keys: np.ndarray, lo: np.ndarray, dims: np.ndarray) -> np.ndarray:
    """把整数三维网格坐标编码成单调整数（便于 searchsorted 查邻居）。

    只对落在 [lo, lo+dims) 内的坐标保证唯一；越界坐标得到负数或超范围值，
    自然查不到邻居，正是我们想要的。
    """
    xyz = keys - lo
    return (xyz[:, 0] * dims[1] + xyz[:, 1]) * dims[2] + xyz[:, 2]


@dataclass
class VoxelData:
    points: np.ndarray       # (M,3) 体素质心
    normals: np.ndarray      # (M,3) 单位法向
    planarity: np.ndarray    # (M,) 0..1，越接近 1 越平
    counts: np.ndarray       # (M,) 每体素原始点数


def voxel_normals(points: np.ndarray, voxel: float, radius: int = 1) -> VoxelData:
    """体素化并估计法向。"""
    if len(points) == 0:
        return VoxelData(np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0), np.zeros(0))

    keys = np.floor(points / voxel).astype(np.int64)
    uniq, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    m = len(uniq)

    sums = np.zeros((m, 3))
    np.add.at(sums, inverse.ravel(), points)
    cent = sums / counts[:, None]

    lo = uniq.min(axis=0)
    dims = uniq.max(axis=0) - lo + 1
    code = _encode(uniq, lo, dims)
    order = np.argsort(code)
    sorted_code = code[order]

    s1 = np.zeros((m, 3))
    s2 = np.zeros((m, 3, 3))
    nn = np.zeros(m)

    r = int(max(1, radius))
    for ox in range(-r, r + 1):
        for oy in range(-r, r + 1):
            for oz in range(-r, r + 1):
                nb = uniq + np.array([ox, oy, oz], dtype=np.int64)
                ncode = _encode(nb, lo, dims)
                pos = np.searchsorted(sorted_code, ncode)
                pos_c = np.clip(pos, 0, len(sorted_code) - 1)
                valid = sorted_code[pos_c] == ncode
                if not valid.any():
                    continue
                idx = order[pos_c[valid]]
                src = np.nonzero(valid)[0]
                p = cent[idx]
                s1[src] += p
                s2[src] += np.einsum("ij,ik->ijk", p, p)
                nn[src] += 1.0

    nn_safe = np.maximum(nn, 1.0)[:, None]
    mean = s1 / nn_safe
    cov = s2 / nn_safe[:, :, None] - np.einsum("ij,ik->ijk", mean, mean)
    # 数值对称化
    cov = 0.5 * (cov + np.transpose(cov, (0, 2, 1)))
    w, v = np.linalg.eigh(cov)
    normals = v[:, :, 0]
    lam = np.clip(w, 0.0, None)
    total = lam.sum(axis=1)
    total[total < 1e-18] = 1.0
    planarity = 1.0 - lam[:, 0] / total
    return VoxelData(cent, normals, planarity, counts.astype(np.float64))


# ================================================================ 主方向


def estimate_yaw(
    normals: np.ndarray,
    planarity: np.ndarray,
    *,
    bin_deg: float = 0.5,
    max_deg: float = 90.0,
) -> tuple[float, float]:
    """从竖直面法向估计曼哈顿主方向（**四倍角圆周均值**）。

    原理：墙面的法向只可能是 θ、θ+90°、θ+180°、θ+270° 之一（曼哈顿假设）。
    把这四个角乘 4 之后全部重合到 4θ，于是可以直接做圆周平均，
    比取直方图峰值精确一个数量级，而且不用调 bin 参数。

    返回 (yaw_rad, confidence)：
        yaw_rad     把点云旋转 -yaw_rad 后墙体与坐标轴对齐
        confidence  圆周集中度 |R| ∈ [0,1]，>0.7 说明曼哈顿假设成立
    """
    if len(normals) == 0:
        return 0.0, 0.0

    vertical = np.abs(normals[:, 2]) < 0.35
    planar = planarity > 0.45
    sel = vertical & planar
    if sel.sum() < 20:
        sel = vertical
    if sel.sum() < 10:
        return 0.0, 0.0

    n = normals[sel]
    ang = np.arctan2(n[:, 1], n[:, 0])
    # 权重：法向越水平（越像竖墙）权重越高
    w = np.clip(1.0 - np.abs(n[:, 2]) / 0.35, 0.0, 1.0)
    z = np.sum(w * np.exp(4j * ang))
    total = float(np.sum(w))
    if total < 1e-9:
        return 0.0, 0.0
    z /= total
    conf = float(abs(z))
    yaw4 = float(np.angle(z))
    yaw = yaw4 / 4.0  # 落在 (-45°, 45°]
    return yaw, conf


def estimate_yaw_histogram(
    normals: np.ndarray, planarity: np.ndarray, *, bin_deg: float = 0.5
) -> tuple[float, float]:
    """直方图版本（保留作为对比/兜底）。"""
    if len(normals) == 0:
        return 0.0, 0.0
    vertical = np.abs(normals[:, 2]) < 0.35
    planar = planarity > 0.45
    sel = vertical & planar
    if sel.sum() < 10:
        sel = vertical
    if sel.sum() < 10:
        return 0.0, 0.0
    n = normals[sel]
    ang = np.degrees(np.arctan2(n[:, 1], n[:, 0])) % 90.0
    nbins = max(8, int(round(90.0 / bin_deg)))
    hist, edges = np.histogram(ang, bins=nbins, range=(0.0, 90.0))
    k = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    k /= k.sum()
    smooth = np.convolve(np.concatenate([hist[-2:], hist, hist[:2]]), k, mode="same")[2:-2]
    peak = int(np.argmax(smooth))
    center = 0.5 * (edges[peak] + edges[peak + 1])
    conf = float(smooth[peak] / max(smooth.sum(), 1e-9))
    return math.radians(center), conf


def dominant_axis_histogram(
    normals: np.ndarray, planarity: np.ndarray, bin_deg: float = 1.0
) -> list[tuple[float, float]]:
    """返回若干主方向的 (角度°, 权重)，用于非曼哈顿户型。"""
    vertical = np.abs(normals[:, 2]) < 0.35
    sel = vertical & (planarity > 0.45)
    if sel.sum() < 10:
        return []
    ang = np.degrees(np.arctan2(normals[sel, 1], normals[sel, 0])) % 180.0
    nbins = int(round(180.0 / bin_deg))
    hist, edges = np.histogram(ang, bins=nbins, range=(0.0, 180.0))
    out = []
    for i in np.argsort(-hist)[:6]:
        if hist[i] < 15:
            continue
        out.append((float(0.5 * (edges[i] + edges[i + 1])), float(hist[i])))
    return out


# ================================================================ 一维聚类


def cluster_1d(values: np.ndarray, gap: float) -> list[np.ndarray]:
    """把一维数值按「间隔大于 gap 就断开」聚成若干簇，返回索引数组列表。"""
    if len(values) == 0:
        return []
    order = np.argsort(values)
    v = values[order]
    breaks = np.nonzero(np.diff(v) > gap)[0] + 1
    return [g for g in np.split(order, breaks) if len(g)]


def peak_clusters(
    values: np.ndarray,
    *,
    bin_width: float = 0.01,
    smooth_kernel: int = 3,
    min_ratio: float = 0.12,
    min_sep: float = 0.05,
    assign_tol: float = 0.025,
    min_points: int = 40,
) -> list[tuple[float, np.ndarray]]:
    """直方图峰检测式一维聚类（墙面提取专用）。

    为什么不用简单的「间隔聚类」：
        门窗洞口两侧的侧壁（门套/窗洞侧边）会在墙面 A 与墙面 B 之间
        连续撒点，把两个本该分开的峰"桥"起来，导致一面墙的两个面被
        误并成一个（表现为 rms 40~50mm、点数严重偏少）。
        直方图峰检测只看「密度峰」，中间的低密度桥接点自然被忽略。

    返回 [(峰位置, 归属点索引数组), ...]，峰位置按升序。
    """
    if len(values) == 0:
        return []
    lo = float(values.min())
    hi = float(values.max())
    if hi - lo < bin_width:
        return [(float(np.mean(values)), np.arange(len(values)))]
    nbins = max(3, int(round((hi - lo) / bin_width)) + 1)
    hist, edges = np.histogram(values, bins=nbins, range=(lo - bin_width, hi + bin_width))

    if smooth_kernel >= 3:
        k = np.ones(smooth_kernel)
        k /= k.sum()
        sm = np.convolve(hist, k, mode="same")
    else:
        sm = hist.astype(float)

    thr = max(sm.max() * min_ratio, 2.0)
    peaks: list[int] = []
    for i in range(len(sm)):
        if sm[i] < thr:
            continue
        left = sm[i - 1] if i > 0 else -1.0
        right = sm[i + 1] if i + 1 < len(sm) else -1.0
        if sm[i] >= left and sm[i] >= right:
            peaks.append(i)
    if not peaks:
        return []

    centers = []
    for i in peaks:
        centers.append(0.5 * (edges[i] + edges[i + 1]))
    # 合并过近的峰
    merged: list[tuple[float, float]] = []
    for c, h in sorted(zip(centers, [sm[i] for i in peaks])):
        if merged and c - merged[-1][0] < min_sep:
            c0, h0 = merged[-1]
            merged[-1] = ((c * h + c0 * h0) / (h + h0), h + h0)
        else:
            merged.append((c, h))

    out: list[tuple[float, np.ndarray]] = []
    for c, _h in merged:
        idx = np.nonzero(np.abs(values - c) <= assign_tol)[0]
        if len(idx) < min_points:
            continue
        # 用截尾均值细化峰位置
        refined = robust_mean(values[idx])
        idx = np.nonzero(np.abs(values - refined) <= assign_tol)[0]
        if len(idx) < min_points:
            continue
        out.append((robust_mean(values[idx]), idx))
    out.sort(key=lambda t: t[0])
    return out


def robust_mean(values: np.ndarray, trim: float = 0.1) -> float:
    """截尾均值，抗离群点。"""
    if len(values) == 0:
        return 0.0
    v = np.sort(values)
    k = int(len(v) * trim)
    if len(v) - 2 * k < 1:
        return float(np.mean(v))
    return float(np.mean(v[k : len(v) - k]))


def trim_extent_by_height(
    along: np.ndarray,
    z: np.ndarray,
    *,
    bin_width: float = 0.06,
    top_tolerance: float = 0.45,
    max_hole: float = 2.2,
    min_bin_points: int = 1,
) -> tuple[float, float, str]:
    """按「竖直方向是否到底」求墙面的真实延伸段，砍掉被矮物体污染的尾巴。

    为什么用高度而不是密度：
        同一坐标上可能既有通高真墙，也有隔壁房间半人高家具的侧面。
        按密度裁剪会误伤——掠射角墙端的点本来就稀疏。
        但**墙一定通到墙顶，家具一定到不了**，这个判据不受采样密度影响。

    为什么要"填洞"：
        窗洞/门洞处，洞口范围内的点在墙顶附近没有（过梁可能超出取样带），
        会被判成"非墙"，把一面完整的墙从中间切断。
        洞口宽度有限（≤ max_hole），所以对夹在墙体之间的短缺口做填补；
        而墙端之外的家具尾巴是一侧悬空的，不会被填上。
    """
    if len(along) < 6:
        return (float(along.min()), float(along.max()), "too-few")
    lo = float(along.min())
    hi = float(along.max())
    if hi - lo < bin_width * 2:
        return (lo, hi, "short")
    z_top = float(np.percentile(z, 95.0))
    nb = max(3, int(math.ceil((hi - lo) / bin_width)))
    edges = lo + np.arange(nb + 1) * bin_width
    idx = np.clip(((along - lo) / bin_width).astype(np.int64), 0, nb - 1)
    bin_maxz = np.full(nb, -np.inf)
    bin_cnt = np.zeros(nb, dtype=np.int64)
    np.maximum.at(bin_maxz, idx, z)
    np.add.at(bin_cnt, idx, 1)
    is_wall = (bin_cnt >= min_bin_points) & (bin_maxz >= z_top - top_tolerance)
    if not is_wall.any():
        return (lo, hi, "no-wall")

    # 1) 取连续段
    runs: list[list[int]] = []
    i = 0
    while i < nb:
        if not is_wall[i]:
            i += 1
            continue
        j = i
        while j < nb and is_wall[j]:
            j += 1
        runs.append([i, j])
        i = j

    # 2) 填补被洞口切开的短缺口
    merged: list[list[int]] = []
    for r in runs:
        if merged and (r[0] - merged[-1][1]) * bin_width <= max_hole + bin_width:
            merged[-1][1] = r[1]
        else:
            merged.append([r[0], r[1]])

    # 3) 取最长段
    best = max(merged, key=lambda r: r[1] - r[0])
    start = float(edges[best[0]])
    end = float(edges[best[1]])
    note = "trimmed" if (start - lo > bin_width * 1.5 or hi - end > bin_width * 1.5) else "full"
    return (start, end, note)
