"""墙体实体几何。

设计要点（整个编辑器的地基）：
    墙体 = 中线 + 厚度。每片墙的平面轮廓用一个参数化四边形表示：
        p0L ─────────── p1L      (+) 左侧，法向 +n
        p0R ─────────── p1R      (-) 右侧
    沿长度 s∈[0,1] 线性插值即可得到任意位置的左右边界点，
    因此「在墙上开一个门洞」= 取 sub(s0, s1)，**不需要任何布尔运算**。

接头处理（决定 CAD 出图好不好看、房间面积准不准）：
    * 二度节点（两条墙相接的转角）：沿角平分线做斜接（miter），
      转角处两片墙正好拼合，无缺口、无重叠。
    * 其它节点（T 型接头、十字交叉、悬空端）：平头封口，并沿自身方向
      外扩一个「相邻墙最大半厚」，保证与邻墙实体重叠而不留缝。
      外扩量恰好停在邻墙外表面，因此不会戳出墙面。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .geom import (
    EPS,
    XY,
    as_xy,
    cross,
    dedupe_ring,
    dot,
    length,
    line_intersect_infinite,
    perp,
    unit,
)

# 斜接点相对端点的最大距离（以半厚为单位），超出则退化为平头，避免极小夹角的长尖刺
MITRE_LIMIT = 8.0
# 端点聚类容差（米）
NODE_TOL = 0.005
# 判定两墙近乎共线（继续延伸）的叉积阈值
COLLINEAR_EPS = 1e-6


@dataclass
class EndFrame:
    """墙体一端的封口：左右两个角点，外加外扩量。"""

    left: np.ndarray
    right: np.ndarray
    extend: float
    kind: str  # "miter" | "cap"
    partner: str | None = None


@dataclass
class WallBody:
    """一片墙的平面实体轮廓（参数化四边形）。"""

    wall_id: str
    p0: np.ndarray
    p1: np.ndarray
    thickness: float
    p0L: np.ndarray
    p1L: np.ndarray
    p0R: np.ndarray
    p1R: np.ndarray
    start_kind: str = "cap"
    end_kind: str = "cap"

    # ------------------------------------------------------------ 基本量

    @property
    def length(self) -> float:
        return float(length(self.p1 - self.p0))

    @property
    def direction(self) -> np.ndarray:
        return unit(self.p1 - self.p0)

    @property
    def normal(self) -> np.ndarray:
        return perp(self.direction)

    @property
    def angle_deg(self) -> float:
        return math.degrees(math.atan2(self.p1[1] - self.p0[1], self.p1[0] - self.p0[0]))

    def left_at(self, s: float) -> np.ndarray:
        return self.p0L + (self.p1L - self.p0L) * s

    def right_at(self, s: float) -> np.ndarray:
        return self.p0R + (self.p1R - self.p0R) * s

    def center_at(self, s: float) -> np.ndarray:
        return self.p0 + (self.p1 - self.p0) * s

    # ------------------------------------------------------------ 子段

    def sub(self, s0: float, s1: float) -> list[XY]:
        """沿长度方向取子段 [s0, s1] 的四边形（逆时针）。"""
        s0 = max(0.0, min(1.0, s0))
        s1 = max(0.0, min(1.0, s1))
        if s1 - s0 < 1e-9:
            return []
        return dedupe_ring(
            [self.right_at(s0), self.right_at(s1), self.left_at(s1), self.left_at(s0)]
        )

    def polygon(self) -> list[XY]:
        return dedupe_ring([self.p0R, self.p1R, self.p1L, self.p0L])

    def area(self) -> float:
        ring = self.polygon()
        s = 0.0
        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]
            s += x1 * y2 - x2 * y1
        return abs(0.5 * s)

    # ------------------------------------------------------------ 洞口

    def opening_frame(self, offset: float, width: float) -> tuple[float, float]:
        """「距起点 offset 米、宽 width 米」的洞口换算成参数区间。"""
        L = max(self.length, EPS)
        s0 = max(0.0, min(1.0, (offset - width * 0.5) / L))
        s1 = max(0.0, min(1.0, (offset + width * 0.5) / L))
        return s0, s1

    def boxes_with_openings(
        self,
        openings: Sequence[tuple[float, float, float, float]],
        wall_height: float,
    ) -> list[tuple[list[XY], float, float]]:
        """把墙体切成若干 (平面多边形, z0, z1) 盒体，门窗洞口成为真空。

        openings 每项 (offset, width, sill, head)。
        返回盒体互不重叠，拼起来就是带洞的墙。
        """
        cuts: list[tuple[float, float, float, float]] = []
        for offset, width, sill, head in sorted(openings):
            s0, s1 = self.opening_frame(offset, width)
            if s1 - s0 < 1e-9:
                continue
            cuts.append((s0, s1, sill, head))

        boxes: list[tuple[list[XY], float, float]] = []
        cursor = 0.0
        for s0, s1, sill, head in cuts:
            if s0 > cursor + 1e-9:
                poly = self.sub(cursor, s0)
                if poly:
                    boxes.append((poly, 0.0, wall_height))
            if sill > 1e-6:
                poly = self.sub(s0, s1)
                if poly:
                    boxes.append((poly, 0.0, min(sill, wall_height)))
            if head < wall_height - 1e-6:
                poly = self.sub(s0, s1)
                if poly:
                    boxes.append((poly, max(head, 0.0), wall_height))
            cursor = max(cursor, s1)
        if cursor < 1.0 - 1e-9:
            poly = self.sub(cursor, 1.0)
            if poly:
                boxes.append((poly, 0.0, wall_height))
        return boxes


# ---------------------------------------------------------------- 节点聚类


class _Node:
    __slots__ = ("point", "members")

    def __init__(self, point: np.ndarray) -> None:
        self.point = point
        # members: (wall_index, which_end)  which_end 0=start,1=end
        self.members: list[tuple[int, int]] = []


def _cluster_nodes(walls: list[tuple[str, np.ndarray, np.ndarray, float]]) -> tuple[list[_Node], list[tuple[int, int]]]:
    nodes: list[_Node] = []
    owner: list[tuple[int, int]] = []
    for k, (_, a, b, _t) in enumerate(walls):
        for side, p in ((0, a), (1, b)):
            found = None
            for ni, node in enumerate(nodes):
                if length(node.point - p) <= NODE_TOL:
                    found = ni
                    break
            if found is None:
                found = len(nodes)
                nodes.append(_Node(p.copy()))
            nodes[found].members.append((k, side))
            owner.append((found, -1))
    return nodes, owner


# ---------------------------------------------------------------- 端部封口


def _cap(end: np.ndarray, into: np.ndarray, half_t: float, extend: float) -> EndFrame:
    n = perp(into)
    d = into
    return EndFrame(
        left=end + n * half_t + d * extend,
        right=end - n * half_t + d * extend,
        extend=extend,
        kind="cap",
    )


def _miter(
    end: np.ndarray,
    into: np.ndarray,
    far: np.ndarray,
    half_t: float,
    partner_dir: np.ndarray,
    partner_half_t: float,
    partner_id: str,
) -> EndFrame | None:
    """按角平分线求斜接点。

    into          本墙从该端点指向墙内的方向
    far           本墙另一端点
    partner_dir   邻墙从同一节点指向其内部的单位方向
    """
    n = perp(into)
    if abs(cross(into, partner_dir)) < 1e-6:
        return None  # 共线，调用方退化为平头
    bn = perp(partner_dir)

    # 注意：侧面线的方向是「墙自身方向」，不是法向；法向只是用来把线平移半个墙厚。
    own = {1: (end + n * half_t, into), -1: (end - n * half_t, into)}
    other = {
        1: (end + bn * partner_half_t, partner_dir),
        -1: (end - bn * partner_half_t, partner_dir),
    }

    def hit(side_own: float, side_other: float) -> np.ndarray | None:
        op, on_ = own[side_own]
        tp, tn = other[side_other]
        return line_intersect_infinite(op, op + on_, tp, tp + tn)

    # 两种配对：同侧 / 交叉。正确的那一种，其连线平行于两条中线的角平分线。
    bisector = unit(into + partner_dir)
    best: tuple[float, EndFrame] | None = None
    for name, pairs in (
        ("cross", ((1, -1), (-1, 1))),
        ("same", ((1, 1), (-1, -1))),
    ):
        a = hit(*pairs[0])
        b = hit(*pairs[1])
        if a is None or b is None:
            continue
        seg = b - a
        if length(seg) < 1e-9:
            continue
        score = abs(float(dot(unit(seg), bisector)))
        if max(length(a - end), length(b - end)) > MITRE_LIMIT * max(half_t, partner_half_t, 0.02):
            continue
        if best is None or score > best[0]:
            best = (score, EndFrame(left=a, right=b, extend=0.0, kind="miter", partner=partner_id))
    return best[1] if best else None


def _end_frame(
    end: np.ndarray,
    into: np.ndarray,
    far: np.ndarray,
    half_t: float,
    neighbors: list[tuple[str, np.ndarray, float]],
    valence: int,
) -> EndFrame:
    """求一端的封口。

    neighbors: [(邻墙 id, 邻墙从本节点指向墙内的方向, 邻墙半厚)]
    """
    if valence <= 1 or not neighbors:
        return _cap(end, into, half_t, 0.0)

    if valence == 2 and len(neighbors) == 1:
        pid, pdir, phalf = neighbors[0]
        # 若是"继续往前"而非转角，就没有必要斜接
        if float(dot(pdir, -into)) > 0.999:
            return _cap(end, into, half_t, max(phalf, half_t))
        frame = _miter(end, into, far, half_t, pdir, phalf, pid)
        if frame is not None:
            return frame
        return _cap(end, into, half_t, max(phalf, half_t))

    # T 型 / 十字 / 多墙交汇：平头 + 外扩到邻墙外表面
    ext = max((h for _pid, _d, h in neighbors), default=0.0)
    return _cap(end, into, half_t, ext)


def build_wall_bodies(
    walls: Iterable[tuple[str, Sequence[float], Sequence[float], float]],
) -> list[WallBody]:
    """由 (id, p0, p1, thickness) 构造墙体实体轮廓。"""
    items = [(wid, as_xy(p0), as_xy(p1), float(t)) for wid, p0, p1, t in walls]
    items = [(w, a, b, t) for w, a, b, t in items if length(b - a) > 1e-6]
    if not items:
        return []

    nodes: list[_Node] = []
    end_node: list[tuple[int, int]] = []  # wall_index -> (node_start, node_end)
    for k, (_, a, b, _t) in enumerate(items):
        pair: list[int] = []
        for p in (a, b):
            found = None
            for ni, node in enumerate(nodes):
                if length(node.point - p) <= NODE_TOL:
                    found = ni
                    break
            if found is None:
                found = len(nodes)
                nodes.append(_Node(p.copy()))
            nodes[found].members.append((k, len(pair)))
            pair.append(found)
        end_node.append((pair[0], pair[1]))

    bodies: list[WallBody] = []
    for k, (wid, a, b, t) in enumerate(items):
        na, nb = end_node[k]
        half = t * 0.5
        d = unit(b - a)

        def neighbors_of(node_index: int) -> list[tuple[str, np.ndarray, float]]:
            out = []
            for wk, wside in nodes[node_index].members:
                if wk == k and wside == (0 if node_index == na else 1):
                    continue
                w_id, wa, wb, wt = items[wk]
                other = wb if wside == 0 else wa
                out.append((w_id, unit(other - nodes[node_index].point), wt * 0.5))
            return out

        nb_a = neighbors_of(na)
        nb_b = neighbors_of(nb)
        fa = _end_frame(a, d, b, half, nb_a, valence=len(nodes[na].members))
        fb = _end_frame(b, -d, a, half, nb_b, valence=len(nodes[nb].members))
        bodies.append(
            WallBody(
                wall_id=wid,
                p0=a,
                p1=b,
                thickness=t,
                p0L=fa.left,
                p1L=fb.right,  # 终点处 into=-d，其"左"是本墙的右
                p0R=fa.right,
                p1R=fb.left,
                start_kind=fa.kind,
                end_kind=fb.kind,
            )
        )
    return bodies


# ---------------------------------------------------------------- 汇总


def plan_outline(bodies: Sequence[WallBody]) -> list[list[XY]]:
    """所有墙体轮廓（用于 DXF 填充、2D 绘制）。"""
    return [b.polygon() for b in bodies]


def wall_volume(bodies: Sequence[WallBody], height: float) -> float:
    return sum(b.area() * height for b in bodies)
