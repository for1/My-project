"""平面直线图（Planar Straight-Line Graph）拓扑运算。

户型图本质上是一个平面图：
    顶点 = 墙角 / T 形接头
    边   = 墙体中线

本模块提供：
    * ``normalize_graph``  顶点焊接 + 在所有交点处打断边（T 型接头、十字交叉）
    * ``planar_faces``     半边遍历，取出所有面（房间候选）
    * ``wireup``           从原始边属性一路传递到拆分后的子边

为什么需要它：房间不需要做多边形布尔运算，只要把墙中线图的面取出来即可。
这是 CAD 里的经典做法，比布尔运算更稳、更快，而且天然支持"编辑一面墙，
相邻两个房间一起变"。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Hashable, Iterable, Sequence

import numpy as np

from .geom import EPS, angle_of, as_xy, cross, dedupe_ring, length, normalize_deg, unit

XY = tuple[float, float]


# ---------------------------------------------------------------- 数据结构


@dataclass
class Graph:
    """平面直线图。

    points:  顶点坐标，索引即顶点 id
    edges:   (u, v, payload) 有序元组，u < v 规范化
    """

    points: list[np.ndarray] = field(default_factory=list)
    edges: list[tuple[int, int, Hashable | None]] = field(default_factory=list)

    def copy(self) -> "Graph":
        return Graph([p.copy() for p in self.points], list(self.edges))

    @property
    def degree(self) -> dict[int, int]:
        d: dict[int, int] = {i: 0 for i in range(len(self.points))}
        for u, v, _ in self.edges:
            d[u] += 1
            d[v] += 1
        return d

    def neighbors(self, vi: int) -> list[tuple[int, Hashable | None]]:
        out = []
        for u, v, payload in self.edges:
            if u == vi:
                out.append((v, payload))
            elif v == vi:
                out.append((u, payload))
        return out


def make_graph(
    points: Iterable[Sequence[float]], edges: Iterable[tuple[int, int, Hashable | None]]
) -> Graph:
    return Graph([as_xy(p) for p in points], [(u, v, p) for u, v, p in edges])


# ---------------------------------------------------------------- 顶点焊接


def weld_vertices(graph: Graph, tol: float = 0.005) -> Graph:
    """把距离小于 tol 的顶点合并成一个（重建结果里同一条墙角常被切成两段）。"""
    n = len(graph.points)
    if n == 0:
        return graph
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    grid: dict[tuple[int, int], list[int]] = {}
    cell = max(tol, 1e-6)
    for i, p in enumerate(graph.points):
        key = (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)))
        grid.setdefault(key, []).append(i)

    for (cx, cy), bucket in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                other = grid.get((cx + dx, cy + dy))
                if not other:
                    continue
                for i in bucket:
                    for j in other:
                        if i < j and length(graph.points[i] - graph.points[j]) <= tol:
                            union(i, j)

    # 组内取平均作为新坐标
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    new_index: dict[int, int] = {}
    points: list[np.ndarray] = []
    for root, members in groups.items():
        new_index[root] = len(points)
        points.append(np.mean([graph.points[m] for m in members], axis=0))

    edges: list[tuple[int, int, Hashable | None]] = []
    for u, v, payload in graph.edges:
        nu, nv = new_index[find(u)], new_index[find(v)]
        if nu == nv:
            continue
        if nu > nv:
            nu, nv = nv, nu
        edges.append((nu, nv, payload))
    return Graph(points, _dedupe_edges(edges))


def _dedupe_edges(
    edges: list[tuple[int, int, Hashable | None]]
) -> list[tuple[int, int, Hashable | None]]:
    """去重：同一对顶点只保留一条边；同时丢掉零长度边。"""
    seen: dict[tuple[int, int], tuple[int, int, Hashable | None]] = {}
    for u, v, payload in edges:
        if u == v:
            continue
        key = (min(u, v), max(u, v))
        seen.setdefault(key, (key[0], key[1], payload))
    return list(seen.values())


# ---------------------------------------------------------------- 打断边


def _seg_param_intersections(
    p: np.ndarray, q: np.ndarray, a: np.ndarray, b: np.ndarray, tol: float
) -> tuple[float, float] | None:
    """线段 pq 与 ab 的交点参数 (s, t)；平行/无交返回 None。"""
    d1 = q - p
    d2 = b - a
    denom = cross(d1, d2)
    if abs(denom) < EPS:
        return None
    s = cross(a - p, d2) / denom
    t = cross(a - p, d1) / denom
    if -1e-9 <= s <= 1 + 1e-9 and -1e-9 <= t <= 1 + 1e-9:
        return s, t
    return None


def split_edges_at_intersections(graph: Graph, tol: float = 0.005) -> Graph:
    """在所有线段交点处插入顶点，使图成为真正的平面图。

    处理三种情况：
      1. 两条线段真正相交（X 型）
      2. 一个线段的端点落在另一个线段内部（T 型接头）
      3. 端点几乎重合 —— 已由 weld_vertices 处理
    """
    n = len(graph.edges)
    points = [p.copy() for p in graph.points]
    # 每条边收集要插入的参数 s 及对应点
    cuts: list[list[float]] = [[] for _ in range(n)]

    def add_cut(ei: int, s: float) -> None:
        if tol_param(ei, s):
            cuts[ei].append(s)

    def tol_param(ei: int, s: float) -> bool:
        u, v, _ = graph.edges[ei]
        L = length(points[v] - points[u])
        return (s * L > tol) and ((1.0 - s) * L > tol)

    for i in range(n):
        u1, v1, _ = graph.edges[i]
        p, q = points[u1], points[v1]
        for j in range(i + 1, n):
            u2, v2, _ = graph.edges[j]
            a, b = points[u2], points[v2]
            hit = _seg_param_intersections(p, q, a, b, tol)
            if hit is None:
                continue
            s, t = hit
            if tol_param(i, s):
                cuts[i].append(s)
            if tol_param(j, t):
                cuts[j].append(t)

    if not any(cuts):
        return graph

    edges: list[tuple[int, int, Hashable | None]] = []
    for ei, (u, v, payload) in enumerate(graph.edges):
        base = points[u]
        direction = points[v] - base
        L = length(direction)
        if L < EPS:
            continue
        params = sorted(set(round(s, 9) for s in cuts[ei] if 0 < s < 1))
        chain = [u]
        for s in params:
            pt = base + direction * s
            # 若已存在几乎重合的点就复用
            idx = _find_point(points, pt, tol)
            if idx is None:
                points.append(pt)
                idx = len(points) - 1
            chain.append(idx)
        chain.append(v)
        for k in range(len(chain) - 1):
            a, b = chain[k], chain[k + 1]
            if a == b:
                continue
            edges.append((min(a, b), max(a, b), payload))
    return Graph(points, _dedupe_edges(edges))


def _find_point(points: list[np.ndarray], pt: np.ndarray, tol: float) -> int | None:
    for i, p in enumerate(points):
        if length(p - pt) <= tol:
            return i
    return None


def normalize_graph(graph: Graph, tol: float = 0.005) -> Graph:
    """焊接顶点 → 打断每条边 → 再次焊接。"""
    return split_edges_at_intersections(weld_vertices(graph, tol), tol)


# ---------------------------------------------------------------- 半边遍历


def all_faces(graph: Graph) -> list[tuple[list[int], float]]:
    """返回全部面（含外部无界面）：(顶点索引环, 有符号面积)。"""
    out: dict[int, list[int]] = {i: [] for i in range(len(graph.points))}
    half: list[tuple[int, int]] = []
    for u, v, _ in graph.edges:
        h1 = len(half)
        half.append((u, v))
        half.append((v, u))
        out[u].append(h1)
        out[v].append(h1 + 1)

    for vi, hs in out.items():
        p = graph.points[vi]
        hs.sort(key=lambda h: math.atan2(*(graph.points[half[h][1]] - p)[::-1]))

    pos: dict[tuple[int, int], int] = {}
    for vi, hs in out.items():
        for k, h in enumerate(hs):
            pos[(vi, h)] = k

    nxt = [-1] * len(half)
    for h, (u, v) in enumerate(half):
        twin = h ^ 1
        hs = out[v]
        k = pos[(v, twin)]
        nxt[h] = hs[(k - 1) % len(hs)]

    faces: list[tuple[list[int], float]] = []
    visited = [False] * len(half)
    for h0 in range(len(half)):
        if visited[h0]:
            continue
        cycle: list[int] = []
        h = h0
        guard = 0
        while not visited[h]:
            visited[h] = True
            cycle.append(half[h][0])
            h = nxt[h]
            guard += 1
            if guard > len(half) + 2:
                break
        if len(cycle) < 3:
            continue
        ring = [graph.points[i] for i in cycle]
        faces.append((cycle, _signed_area(ring)))
    return faces


def planar_faces(graph: Graph, min_area: float = 1e-6) -> list[list[int]]:
    """取出平面图的全部有界面（房间候选），逆时针（有符号面积为正）。"""
    return [c for c, a in all_faces(graph) if a > min_area]


def planar_outer_face(graph: Graph) -> list[int] | None:
    """外轮廓面：面积最负的那个环（无界面的补集），返回时已反转为逆时针。

    用于求建筑外轮廓（再按外墙厚度向外偏移半个墙厚即为真实轮廓）。
    """
    faces = all_faces(graph)
    if not faces:
        return None
    cycle, area = min(faces, key=lambda t: t[1])
    if area >= 0:
        return None
    return list(reversed(cycle))


def _signed_area(ring: Sequence[Sequence[float]]) -> float:
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def dangling_vertices(graph: Graph) -> list[int]:
    """度为 1 的顶点 —— 重建时往往意味着墙没接上，应报警。"""
    d = graph.degree
    return [i for i, k in d.items() if k == 1]


def isolated_vertices(graph: Graph) -> list[int]:
    d = graph.degree
    return [i for i, k in d.items() if k == 0]


def loop_warnings(graph: Graph) -> list[str]:
    """检查无法围合的房间（悬空墙）。"""
    warns = []
    dang = dangling_vertices(graph)
    if dang:
        pts = [tuple(round(float(c), 3) for c in graph.points[i]) for i in dang[:8]]
        warns.append(f"{len(dang)} 个悬空墙端点（度为 1），房间无法闭合: {pts}")
    iso = isolated_vertices(graph)
    if iso:
        warns.append(f"{len(iso)} 个孤立点")
    return warns
