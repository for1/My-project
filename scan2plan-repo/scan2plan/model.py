"""户型图核心数据模型。

层级：
    Project ── FloorPlan(一层) ── Vertex / Wall / Opening / RoomLabel
                              └── 派生物：WallBody、Graph、Room、Outline

设计原则（为「可编辑」服务）：
    1. **只存一份真相**：只有顶点、墙（引用顶点 id）、门窗（挂在墙上）。
       墙实体轮廓、房间多边形、面积、周长全部按需派生，永不存储。
       → 改一个墙角，相邻两个房间的面积、周长、墙面积立刻全对。
    2. **拓扑共享**：墙引用顶点 id，所以拖动一个墙角会同时带动所有相连的墙。
    3. **单位统一为米**；角度为度；坐标 Y 向上（CAD 习惯）。

本模块只依赖 numpy 与本仓库的 geom/topology/wallbody。
"""
from __future__ import annotations

import copy
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence

import numpy as np

from . import geom
from .geom import XY, as_xy, unit
from .topology import (
    Graph,
    dangling_vertices,
    loop_warnings,
    normalize_graph,
    planar_faces,
    planar_outer_face,
)
from .wallbody import WallBody, build_wall_bodies

FORMAT = "scan2plan.floorplan"
SCHEMA_VERSION = 1

# 默认构造参数（米）
DEFAULT_THICKNESS_EXT = 0.20   # 外墙
DEFAULT_THICKNESS_INT = 0.10   # 内墙
DEFAULT_WALL_HEIGHT = 2.80
DEFAULT_DOOR = {"width": 0.90, "height": 2.10, "sill": 0.0}
DEFAULT_WINDOW = {"width": 1.50, "height": 1.40, "sill": 0.90}
DEFAULT_PASSAGE = {"width": 1.60, "height": 2.30, "sill": 0.0}
OPENING_DEFAULTS = {
    "door": DEFAULT_DOOR,
    "window": DEFAULT_WINDOW,
    "passage": DEFAULT_PASSAGE,
    "bay": DEFAULT_WINDOW,
}


def _nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ================================================================ 图元


@dataclass
class Vertex:
    """墙角 / T 型接头。多面墙共享同一个顶点即形成连接。"""

    id: str
    xy: XY

    @property
    def x(self) -> float:
        return self.xy[0]

    @property
    def y(self) -> float:
        return self.xy[1]

    def to_dict(self) -> dict:
        return {"id": self.id, "xy": [round(self.xy[0], 6), round(self.xy[1], 6)]}

    @staticmethod
    def from_dict(d: dict) -> "Vertex":
        return Vertex(str(d["id"]), (float(d["xy"][0]), float(d["xy"][1])))


@dataclass
class Wall:
    """墙体：中线由两个顶点定义，厚度沿法向。"""

    id: str
    start: str
    end: str
    thickness: float = DEFAULT_THICKNESS_INT
    height: float = DEFAULT_WALL_HEIGHT
    kind: str = "interior"  # exterior | interior | partition | virtual
    source: str = "manual"  # manual | reconstruction | imported
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "thickness": round(self.thickness, 6),
            "height": round(self.height, 6),
            "kind": self.kind,
            "source": self.source,
            "confidence": round(self.confidence, 4),
        }

    @staticmethod
    def from_dict(d: dict) -> "Wall":
        return Wall(
            id=str(d["id"]),
            start=str(d["start"]),
            end=str(d["end"]),
            thickness=float(d.get("thickness", DEFAULT_THICKNESS_INT)),
            height=float(d.get("height", DEFAULT_WALL_HEIGHT)),
            kind=str(d.get("kind", "interior")),
            source=str(d.get("source", "manual")),
            confidence=float(d.get("confidence", 1.0)),
        )


@dataclass
class Opening:
    """门窗洞口。挂在墙上，用「沿墙中线距 start 的距离」定位。

    这样墙被移动/旋转后，洞口自动跟着走，不需要重新计算。
    """

    id: str
    wall: str
    kind: str = "door"  # door | window | passage | bay
    offset: float = 0.0  # 洞口中心距墙 start 端的距离（米）
    width: float = 0.90
    height: float = 2.10
    sill: float = 0.0
    swing: str = "left"  # left | right | none；门的开启方向
    swing_in: bool = True  # 开向房间内侧

    @property
    def head(self) -> float:
        return self.sill + self.height

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "wall": self.wall,
            "kind": self.kind,
            "offset": round(self.offset, 6),
            "width": round(self.width, 6),
            "height": round(self.height, 6),
            "sill": round(self.sill, 6),
            "swing": self.swing,
            "swing_in": self.swing_in,
        }

    @staticmethod
    def from_dict(d: dict) -> "Opening":
        return Opening(
            id=str(d["id"]),
            wall=str(d["wall"]),
            kind=str(d.get("kind", "door")),
            offset=float(d.get("offset", 0.0)),
            width=float(d.get("width", 0.90)),
            height=float(d.get("height", 2.10)),
            sill=float(d.get("sill", 0.0)),
            swing=str(d.get("swing", "left")),
            swing_in=bool(d.get("swing_in", True)),
        )


@dataclass
class RoomLabel:
    """房间命名锚点：落在哪个派生房间里，哪个房间就用这个名字。

    几何变化导致房间重建时名字仍然跟着房间走（按点定位），不会丢。
    """

    name: str
    xy: XY
    kind: str = "room"  # room | balcony | corridor | bathroom | kitchen
    floor_material: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "xy": [round(self.xy[0], 6), round(self.xy[1], 6)],
            "kind": self.kind,
            "floor_material": self.floor_material,
        }

    @staticmethod
    def from_dict(d: dict) -> "RoomLabel":
        return RoomLabel(
            name=str(d["name"]),
            xy=(float(d["xy"][0]), float(d["xy"][1])),
            kind=str(d.get("kind", "room")),
            floor_material=str(d.get("floor_material", "")),
        )


# ================================================================ 派生房间


@dataclass
class Room:
    """派生房间（不存储，每次按墙体重算）。"""

    index: int
    label: str
    kind: str
    polygon: list[XY]          # 净尺寸轮廓（内墙面），逆时针
    centerline_polygon: list[XY]  # 墙中线围合的面（毛尺寸）
    net_area: float            # 净面积 m²
    gross_area: float          # 毛面积 m²
    perimeter: float           # 净周长 m
    centroid: XY
    wall_ids: list[str] = field(default_factory=list)
    opening_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "label": self.label,
            "kind": self.kind,
            "polygon": [[round(x, 4), round(y, 4)] for x, y in self.polygon],
            "net_area": round(self.net_area, 4),
            "gross_area": round(self.gross_area, 4),
            "perimeter": round(self.perimeter, 4),
            "centroid": [round(self.centroid[0], 4), round(self.centroid[1], 4)],
            "wall_ids": list(self.wall_ids),
            "opening_ids": list(self.opening_ids),
        }


# ================================================================ 一层户型


@dataclass
class FloorPlan:
    name: str = "1F"
    elevation: float = 0.0
    ceiling_height: float = DEFAULT_WALL_HEIGHT
    slab_thickness: float = 0.12
    vertices: dict[str, Vertex] = field(default_factory=dict)
    walls: dict[str, Wall] = field(default_factory=dict)
    openings: dict[str, Opening] = field(default_factory=dict)
    room_labels: list[RoomLabel] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ 基本操作

    def add_vertex(self, xy: Sequence[float], *, weld_tol: float = 0.005) -> str:
        """加入顶点；若附近已有顶点则直接复用（保证拓扑连接）。"""
        p = as_xy(xy)
        for vid, v in self.vertices.items():
            if geom.length(np.array(v.xy) - p) <= weld_tol:
                return vid
        vid = _nid("v")
        self.vertices[vid] = Vertex(vid, (float(p[0]), float(p[1])))
        return vid

    def add_wall(
        self,
        p0: Sequence[float],
        p1: Sequence[float],
        *,
        thickness: float = DEFAULT_THICKNESS_INT,
        height: float | None = None,
        kind: str = "interior",
        source: str = "manual",
        confidence: float = 1.0,
        weld_tol: float = 0.005,
    ) -> Wall:
        a = self.add_vertex(p0, weld_tol=weld_tol)
        b = self.add_vertex(p1, weld_tol=weld_tol)
        if a == b:
            raise ValueError("wall endpoints coincide")
        w = Wall(
            id=_nid("w"),
            start=a,
            end=b,
            thickness=thickness,
            height=self.ceiling_height if height is None else height,
            kind=kind,
            source=source,
            confidence=confidence,
        )
        self.walls[w.id] = w
        return w

    def wall_endpoints(self, wall_id: str) -> tuple[np.ndarray, np.ndarray]:
        w = self.walls[wall_id]
        return as_xy(self.vertices[w.start].xy), as_xy(self.vertices[w.end].xy)

    def wall_length(self, wall_id: str) -> float:
        a, b = self.wall_endpoints(wall_id)
        return float(geom.length(b - a))

    def wall_angle(self, wall_id: str) -> float:
        a, b = self.wall_endpoints(wall_id)
        return geom.angle_of(b - a)

    def _attached_endpoints(
        self, wall_id: str, tol: float = 0.012
    ) -> list[tuple[str, float]]:
        """找出「其它墙的端点正好落在这面墙中间」的情况（T 型接头）。

        模型只存墙的端点，T 型接头是靠 normalize_graph 在建图时自动打断的。
        所以编辑这面墙时，必须把这些贴着它的端点一起带着动，
        否则墙一转，T 接头就脱开了 —— 表现为房间突然闭合不了。

        返回 [(顶点 id, 该点在墙上的参数 t), ...]
        """
        w = self.walls.get(wall_id)
        if w is None:
            return []
        a, b = self.wall_endpoints(wall_id)
        d = b - a
        L = float(geom.length(d))
        if L < 1e-9:
            return []
        out: list[tuple[str, float]] = []
        seen: set[str] = set()
        for wid, other in self.walls.items():
            if wid == wall_id:
                continue
            for vid in (other.start, other.end):
                if vid in (w.start, w.end) or vid in seen:
                    continue
                p = as_xy(self.vertices[vid].xy)
                dist = geom.seg_point_distance(p, a, b)
                if dist > tol:
                    continue
                t = float(geom.dot(p - a, d) / (L * L))
                # 必须落在墙身中间，且不能贴着两端（两端由拓扑共享处理）
                if 0.01 < t < 0.99:
                    out.append((vid, t))
                    seen.add(vid)
        return out

    def _transform_attached(
        self,
        wall_id: str,
        pivot: np.ndarray,
        angle_deg: float = 0.0,
        delta: np.ndarray | None = None,
    ) -> None:
        """把贴在墙身上的 T 型接头端点跟着墙一起做刚体变换。"""
        for vid, _t in self._attached_endpoints(wall_id):
            p = as_xy(self.vertices[vid].xy)
            if delta is not None:
                np_ = p + delta
            else:
                np_ = geom.rot_about(p, pivot, angle_deg)
            self.vertices[vid].xy = (float(np_[0]), float(np_[1]))

    def set_wall_angle(
        self, wall_id: str, angle_deg: float, *, adjust_neighbors: bool = True
    ) -> None:
        """以 start 端为支点旋转墙体，使角度变成 angle_deg。

        adjust_neighbors=True 时：
          * 相邻墙保持各自方向（只改长度），不会顺带被拧歪；
          * 落在墙身上的 T 型接头端点跟着一起转，保证房间不脱开。
        """
        w = self.walls[wall_id]
        a, b = self.wall_endpoints(wall_id)
        L = float(geom.length(b - a))
        if L < 1e-9:
            return
        old_angle = geom.angle_of(b - a)
        r = math.radians(angle_deg)
        new_b = a + np.array([math.cos(r), math.sin(r)]) * L
        # 先带着 T 接头转（要用老角度算差值）
        d_angle = float(geom.normalize_deg(angle_deg - old_angle))
        self._transform_attached(wall_id, a, angle_deg=d_angle)
        self.vertices[w.end].xy = (float(new_b[0]), float(new_b[1]))
        if adjust_neighbors:
            self._shift_neighbor_fars(w.end, new_b - b, b, exclude=wall_id)

    def set_wall_length(
        self,
        wall_id: str,
        length_m: float,
        pivot: str = "start",
        *,
        adjust_neighbors: bool = True,
    ) -> None:
        """改变墙长。

        pivot="start" 移动 end 端；"end" 移动 start 端。
        adjust_neighbors=True 时，相邻墙会沿自身法向平移以保持垂直/平行关系，
        而不是被拉歪 —— 这是户型编辑器里"输入尺寸"应有的手感。
        """
        w = self.walls[wall_id]
        a, b = self.wall_endpoints(wall_id)
        L = float(geom.length(b - a))
        if L < 1e-9:
            return
        d = unit(b - a)
        if pivot == "start":
            old = b
            new = a + d * float(length_m)
            self.vertices[w.end].xy = (float(new[0]), float(new[1]))
            if adjust_neighbors:
                self._shift_neighbor_fars(w.end, new - old, old, exclude=wall_id)
        else:
            old = a
            new = b - d * float(length_m)
            self.vertices[w.start].xy = (float(new[0]), float(new[1]))
            if adjust_neighbors:
                self._shift_neighbor_fars(w.start, new - old, old, exclude=wall_id)

    def _shift_neighbor_fars(
        self,
        vertex_id: str,
        delta: np.ndarray,
        old_xy: np.ndarray,
        *,
        exclude: str | None = None,
    ) -> None:
        """顶点从 old_xy 移动 delta 后，让相连的其它墙保持自身方向（只平移不转）。

        方向必须用**移动前**的位置算，否则邻墙已经被带歪，投影就错了。
        """
        for wid, w in self.walls.items():
            if wid == exclude:
                continue
            if vertex_id not in (w.start, w.end):
                continue
            other = w.end if w.start == vertex_id else w.start
            far = as_xy(self.vertices[other].xy)
            d = unit(old_xy - far)  # 从远端指向被移动顶点的旧方向
            if geom.length(d) < 1e-9:
                continue
            perp_comp = delta - d * geom.dot(delta, d)
            if geom.length(perp_comp) < 1e-9:
                continue
            self.vertices[other].xy = (
                float(far[0] + perp_comp[0]),
                float(far[1] + perp_comp[1]),
            )

    def move_vertex(self, vertex_id: str, xy: Sequence[float]) -> None:
        p = as_xy(xy)
        self.vertices[vertex_id].xy = (float(p[0]), float(p[1]))

    def translate_wall(self, wall_id: str, delta: Sequence[float]) -> None:
        """整体平移一面墙（端点一起动，相连的墙会跟着拉伸）。"""
        d = as_xy(delta)
        w = self.walls[wall_id]
        self._transform_attached(wall_id, np.zeros(2), delta=d)
        for vid in (w.start, w.end):
            v = self.vertices[vid]
            self.vertices[vid].xy = (v.x + float(d[0]), v.y + float(d[1]))

    def set_wall_thickness(self, wall_id: str, thickness_m: float) -> None:
        self.walls[wall_id].thickness = max(0.01, float(thickness_m))

    def delete_wall(self, wall_id: str, *, prune: bool = True) -> None:
        w = self.walls.pop(wall_id, None)
        if w is None:
            return
        for oid in [o.id for o in self.openings.values() if o.wall == wall_id]:
            self.openings.pop(oid, None)
        if prune:
            self.prune_orphan_vertices()

    def split_wall(self, wall_id: str, at: Sequence[float] | float) -> tuple[str, str]:
        """在指定位置把墙切成两段，返回 (新墙1, 新墙2) 的 id。

        at 可以是 2D 点，也可以是 0~1 的参数。
        """
        w = self.walls[wall_id]
        a, b = self.wall_endpoints(wall_id)
        L = float(geom.length(b - a))
        if L < 1e-9:
            raise ValueError("zero-length wall")
        if isinstance(at, (int, float)):
            p = a + (b - a) * float(at)
        else:
            p = geom.seg_closest_point(at, a, b)
        d0 = float(geom.length(p - a))
        d1 = float(geom.length(b - p))
        if min(d0, d1) < 1e-4:
            raise ValueError("split point too close to an endpoint")

        # 洞口按原位置迁移到两段上
        moved: list[tuple[str, float]] = []
        for o in self.openings.values():
            if o.wall != wall_id:
                continue
            if o.offset - o.width * 0.5 < d0:
                moved.append((o.id, o.offset))
            else:
                moved.append((o.id, o.offset - d0))

        mid = self.add_vertex(p, weld_tol=0.0)
        w1 = Wall(
            id=_nid("w"),
            start=w.start,
            end=mid,
            thickness=w.thickness,
            height=w.height,
            kind=w.kind,
            source=w.source,
            confidence=w.confidence,
        )
        w2 = Wall(
            id=_nid("w"),
            start=mid,
            end=w.end,
            thickness=w.thickness,
            height=w.height,
            kind=w.kind,
            source=w.source,
            confidence=w.confidence,
        )
        del self.walls[wall_id]
        self.walls[w1.id] = w1
        self.walls[w2.id] = w2
        for oid, new_off in moved:
            o = self.openings[oid]
            o.wall = w1.id if new_off < d0 else w2.id
            o.offset = new_off if new_off < d0 else new_off - d0
        return w1.id, w2.id

    def prune_orphan_vertices(self) -> int:
        used = set()
        for w in self.walls.values():
            used.add(w.start)
            used.add(w.end)
        dead = [vid for vid in self.vertices if vid not in used]
        for vid in dead:
            self.vertices.pop(vid, None)
        return len(dead)

    # ------------------------------------------------------------ 门窗

    def add_opening(
        self,
        wall_id: str,
        kind: str = "door",
        *,
        offset: float | None = None,
        width: float | None = None,
        height: float | None = None,
        sill: float | None = None,
        swing: str = "left",
        swing_in: bool = True,
    ) -> Opening:
        if wall_id not in self.walls:
            raise KeyError(wall_id)
        L = self.wall_length(wall_id)
        defaults = OPENING_DEFAULTS.get(kind, DEFAULT_DOOR)
        w = float(width if width is not None else defaults["width"])
        s = float(sill if sill is not None else defaults["sill"])
        h = float(height if height is not None else defaults["height"])
        w = min(w, max(L - 0.02, 0.1))
        o = float(offset if offset is not None else L * 0.5)
        o = max(w * 0.5 + 0.01, min(L - w * 0.5 - 0.01, o))
        op = Opening(
            id=_nid("o"),
            wall=wall_id,
            kind=kind,
            offset=o,
            width=w,
            height=h,
            sill=s,
            swing=swing,
            swing_in=swing_in,
        )
        self.openings[op.id] = op
        return op

    def delete_opening(self, opening_id: str) -> None:
        self.openings.pop(opening_id, None)

    def openings_of(self, wall_id: str) -> list[Opening]:
        return sorted(
            (o for o in self.openings.values() if o.wall == wall_id),
            key=lambda o: o.offset,
        )

    # ------------------------------------------------------------ 派生：图

    def to_graph(self) -> Graph:
        index = {vid: i for i, vid in enumerate(self.vertices)}
        points = [as_xy(self.vertices[vid].xy) for vid in self.vertices]
        edges = [
            (index[w.start], index[w.end], w.id)
            for w in self.walls.values()
            if w.kind != "virtual"
        ]
        return Graph(points, edges)

    def normalized_graph(self, tol: float = 0.005) -> Graph:
        return normalize_graph(self.to_graph(), tol)

    def wall_bodies(self) -> list[WallBody]:
        items = [
            (w.id, self.vertices[w.start].xy, self.vertices[w.end].xy, w.thickness)
            for w in self.walls.values()
            if w.kind != "virtual"
        ]
        return build_wall_bodies(items)

    def wall_body_map(self) -> dict[str, WallBody]:
        return {b.wall_id: b for b in self.wall_bodies()}

    # ------------------------------------------------------------ 派生：房间

    def _thickness_lookup(self) -> dict[tuple[int, int], float]:
        return {w.id: w.thickness for w in self.walls.values()}

    def rooms(self, *, tol: float = 0.005) -> list[Room]:
        """从墙中线图的面导出房间。

        净面积 = 中线围合面按各边所属墙厚向内偏移半个墙厚。
        这是建筑制图里的标准「净面积」定义。
        """
        g = self.normalized_graph(tol)
        faces = planar_faces(g)
        thickness_by_wall = self._thickness_lookup()

        rooms: list[Room] = []
        for face in faces:
            ring = [g.points[i] for i in face]
            if len(ring) < 3:
                continue
            # 每条边的墙厚
            halves: list[float] = []
            wall_ids: list[str] = []
            ok = True
            n = len(face)
            for k in range(n):
                u, v = face[k], face[(k + 1) % n]
                payload = None
                for a, b, p in g.edges:
                    if (a == u and b == v) or (a == v and b == u):
                        payload = p
                        break
                if payload is None:
                    ok = False
                    break
                wall_ids.append(payload)
                halves.append(thickness_by_wall.get(payload, DEFAULT_THICKNESS_INT) * 0.5)
            if not ok or len(set(wall_ids)) == 0:
                continue
            net = offset_ring(ring, halves)
            if net is None or len(net) < 3:
                continue
            gross_a = abs(geom.polygon_area(ring))
            net_a = abs(geom.polygon_area(net))
            if net_a <= 0.01:
                continue
            centroid = geom.polygon_centroid(net)
            label, kind = "", "room"
            for lab in self.room_labels:
                if geom.point_in_ring(lab.xy, net) or geom.point_in_ring(lab.xy, ring):
                    label, kind = lab.name, lab.kind
                    break
            if not label:
                label = f"房间{len(rooms) + 1}"
            opening_ids = [
                o.id
                for o in self.openings.values()
                if o.wall in wall_ids
            ]
            rooms.append(
                Room(
                    index=len(rooms),
                    label=label,
                    kind=kind,
                    polygon=[(float(p[0]), float(p[1])) for p in net],
                    centerline_polygon=[(float(p[0]), float(p[1])) for p in ring],
                    net_area=net_a,
                    gross_area=gross_a,
                    perimeter=geom.polygon_perimeter(net),
                    centroid=(float(centroid[0]), float(centroid[1])),
                    wall_ids=list(dict.fromkeys(wall_ids)),
                    opening_ids=opening_ids,
                )
            )
        # 面积大的排前面，编号稳定
        rooms.sort(key=lambda r: -r.net_area)
        for i, r in enumerate(rooms):
            r.index = i
        return rooms

    def room_at(self, xy: Sequence[float]) -> Room | None:
        p = as_xy(xy)
        for r in self.rooms():
            if geom.point_in_ring(p, r.polygon):
                return r
        return None

    def outline(self, *, tol: float = 0.005) -> list[XY]:
        """建筑外轮廓（真实外墙面）。

        取平面图的「外部面」环，再按每条边所属外墙的厚度向外偏移半个墙厚。
        L 形、凹字形户型都能正确出轮廓（不是凸包近似）。
        """
        g = self.normalized_graph(tol)
        cycle = planar_outer_face(g)
        if not cycle:
            return []
        ring: list[np.ndarray] = [g.points[i] for i in cycle]
        # 归一化为逆时针（外部面环是顺时针，planar_outer_face 已反转）
        if geom.polygon_area(ring) < 0:
            ring.reverse()
        # 边 -> 墙厚
        thickness: dict[tuple[int, int], float] = {}
        for u, v, payload in g.edges:
            t = self.walls[payload].thickness if payload in self.walls else DEFAULT_THICKNESS_EXT
            thickness[(min(u, v), max(u, v))] = t

        n = len(cycle)
        dists: list[float] = []
        for k in range(n):
            u, v = cycle[k], cycle[(k + 1) % n]
            t = thickness.get((min(u, v), max(u, v)), DEFAULT_THICKNESS_EXT)
            dists.append(-t * 0.5)  # 负值 = 向外
        out = offset_ring(ring, dists)
        if out is None:
            return [(float(p[0]), float(p[1])) for p in ring]
        return out

    # ------------------------------------------------------------ 统计

    def stats(self) -> dict[str, Any]:
        rooms = self.rooms()
        net = sum(r.net_area for r in rooms)
        gross = sum(r.gross_area for r in rooms)
        wall_len = sum(self.wall_length(w.id) for w in self.walls.values())
        bodies = self.wall_bodies()
        wall_area = sum(b.area() for b in bodies) * self.ceiling_height
        door_n = sum(1 for o in self.openings.values() if o.kind == "door")
        win_n = sum(1 for o in self.openings.values() if o.kind == "window")
        return {
            "rooms": len(rooms),
            "net_area_m2": round(net, 3),
            "gross_area_m2": round(gross, 3),
            "wall_length_m": round(wall_len, 3),
            "wall_area_m2": round(wall_area, 3),
            "walls": len(self.walls),
            "vertices": len(self.vertices),
            "openings": len(self.openings),
            "doors": door_n,
            "windows": win_n,
            "unclosed_hints": loop_warnings(self.normalized_graph()),
        }

    def validate(self) -> list[str]:
        problems: list[str] = []
        for wid, w in self.walls.items():
            if w.start not in self.vertices:
                problems.append(f"wall {wid} 引用不存在的顶点 {w.start}")
            if w.end not in self.vertices:
                problems.append(f"wall {wid} 引用不存在的顶点 {w.end}")
            if w.start == w.end:
                problems.append(f"wall {wid} 起终点相同")
            if w.thickness <= 0:
                problems.append(f"wall {wid} 厚度非正")
        for oid, o in self.openings.items():
            if o.wall not in self.walls:
                problems.append(f"opening {oid} 引用不存在的墙 {o.wall}")
                continue
            L = self.wall_length(o.wall)
            if o.width > L + 1e-6:
                problems.append(f"opening {oid} 宽于所在墙 ({o.width:.3f} > {L:.3f})")
            if o.offset - o.width / 2 < -1e-6 or o.offset + o.width / 2 > L + 1e-6:
                problems.append(f"opening {oid} 伸出墙端")
            if o.sill + o.height > self.ceiling_height + 1e-6:
                problems.append(f"opening {oid} 高过墙顶")
        for msg in loop_warnings(self.normalized_graph()):
            problems.append(msg)
        return problems

    # ------------------------------------------------------------ 标定 / 清理

    def scale_all(self, factor: float) -> None:
        """整体缩放（用于标定真实尺度：视频重建出来的图往往是相对尺度）。"""
        f = float(factor)
        if f <= 0:
            raise ValueError("scale factor must be positive")
        for v in self.vertices.values():
            v.xy = (v.x * f, v.y * f)
        for o in self.openings.values():
            o.offset *= f
            o.width *= f
            o.height *= f
            o.sill *= f

    def calibrate_by_wall(self, wall_id: str, measured_length_m: float) -> float:
        """用一段实测距离标定整图比例，返回缩放系数。

        典型用法：现场用激光测距仪量一面墙 = 3.245m，输入后全图等比缩放，
        把相对尺度变成真实尺度。这正是「1% 相对精度」落地的方式。
        """
        L = self.wall_length(wall_id)
        if L < 1e-6:
            raise ValueError("标定墙长度为零")
        f = float(measured_length_m) / L
        self.scale_all(f)
        return f

    def snap_orthogonal(
        self, *, origin_deg: float = 0.0, step_deg: float = 90.0, tol_deg: float = 8.0
    ) -> dict[str, Any]:
        """把所有墙吸附到正交（或任意 step）网格。

        做法：按当前墙长绕 start 端旋转 end 顶点，然后重新焊接顶点。
        会尽量保住墙长，改变的是方向。
        """
        moved = 0
        for w in self.walls.values():
            a = as_xy(self.vertices[w.start].xy)
            b = as_xy(self.vertices[w.end].xy)
            L = float(geom.length(b - a))
            if L < 1e-6:
                continue
            ang = geom.angle_of(b - a) - origin_deg
            snapped = geom.snap_angle(ang, step_deg) + origin_deg
            if abs(geom.normalize_deg(snapped - geom.angle_of(b - a))) > tol_deg:
                continue
            r = math.radians(snapped)
            nb = a + np.array([math.cos(r), math.sin(r)]) * L
            self.vertices[w.end].xy = (float(nb[0]), float(nb[1]))
            moved += 1
        welded = self.weld(self._weld_tol_auto())
        return {"rotated_walls": moved, "welded_vertices": welded}

    def _weld_tol_auto(self) -> float:
        return 0.02

    def weld(self, tol: float = 0.02) -> int:
        """把距离小于 tol 的顶点合并，修复重建产生的微小裂缝。"""
        if tol <= 0 or len(self.vertices) < 2:
            return 0
        ids = list(self.vertices)
        parent = {vid: vid for vid in ids}

        def find(i: str) -> str:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        pts = {vid: as_xy(self.vertices[vid].xy) for vid in ids}
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if geom.length(pts[ids[i]] - pts[ids[j]]) <= tol:
                    ri, rj = find(ids[i]), find(ids[j])
                    if ri != rj:
                        parent[rj] = ri

        groups: dict[str, list[str]] = {}
        for vid in ids:
            groups.setdefault(find(vid), []).append(vid)
        merged = 0
        remap: dict[str, str] = {}
        for root, members in groups.items():
            if len(members) == 1:
                remap[members[0]] = members[0]
                continue
            cx = float(np.mean([pts[m][0] for m in members]))
            cy = float(np.mean([pts[m][1] for m in members]))
            keep = members[0]
            remap.update({m: keep for m in members})
            self.vertices[keep].xy = (cx, cy)
            for m in members[1:]:
                self.vertices.pop(m, None)
            merged += len(members) - 1
        for w in self.walls.values():
            w.start = remap.get(w.start, w.start)
            w.end = remap.get(w.end, w.end)
        # 合并后可能出现自环，去掉
        dead = [wid for wid, w in self.walls.items() if w.start == w.end]
        for wid in dead:
            self.delete_wall(wid, prune=False)
        self.prune_orphan_vertices()
        return merged

    def merge_collinear(self, tol_deg: float = 1.0, gap_tol: float = 0.05) -> int:
        """合并二度节点上共线的两段墙（重建常把一面墙拆成好几段）。

        把 w1(共享点→far1) 与 w2(共享点→far2) 合成 far1—far2 一条，
        并把两侧门窗按新的起算点重新编号。
        """
        merged = 0
        while True:
            incident: dict[str, list[str]] = {vid: [] for vid in self.vertices}
            for wid, w in self.walls.items():
                incident.setdefault(w.start, []).append(wid)
                incident.setdefault(w.end, []).append(wid)
            target = None
            for vid, wids in incident.items():
                if len(wids) != 2:
                    continue
                w1 = self.walls.get(wids[0])
                w2 = self.walls.get(wids[1])
                if w1 is None or w2 is None:
                    continue
                if abs(w1.thickness - w2.thickness) > 1e-6:
                    continue
                if abs(w1.height - w2.height) > 1e-6:
                    continue
                a1, b1 = self.wall_endpoints(w1.id)
                a2, b2 = self.wall_endpoints(w2.id)
                v = as_xy(self.vertices[vid].xy)
                far1 = b1 if w1.start == vid else a1
                far2 = b2 if w2.start == vid else a2
                if geom.angle_between(far1 - v, far2 - v) < 180.0 - tol_deg:
                    continue
                if geom.seg_point_distance(far2, v, far1) > gap_tol:
                    continue
                target = (vid, w1, w2, far1, far2)
                break
            if target is None:
                break
            vid, w1, w2, far1, far2 = target
            # 记录两个旧墙上各洞口的世界坐标中心（删墙前必须先取出来）
            old_centers: list[tuple[str, np.ndarray]] = []
            for wid in (w1.id, w2.id):
                a, b = self.wall_endpoints(wid)
                d = unit(b - a)
                for o in self.openings.values():
                    if o.wall == wid:
                        old_centers.append((o.id, a + d * o.offset))
            base = w1 if geom.length(far1 - v) >= geom.length(far2 - v) else w2
            keep_start = self.add_vertex(far1, weld_tol=0.0)
            keep_end = self.add_vertex(far2, weld_tol=0.0)
            new_wall = Wall(
                id=_nid("w"),
                start=keep_start,
                end=keep_end,
                thickness=base.thickness,
                height=base.height,
                kind=base.kind,
                source=base.source,
                confidence=min(w1.confidence, w2.confidence),
            )
            del self.walls[w1.id]
            del self.walls[w2.id]
            self.walls[new_wall.id] = new_wall
            self.vertices.pop(vid, None)

            a = as_xy(self.vertices[new_wall.start].xy)
            b = as_xy(self.vertices[new_wall.end].xy)
            L = float(geom.length(b - a))
            d = unit(b - a)
            for oid, center in old_centers:
                o = self.openings[oid]
                o.wall = new_wall.id
                o.offset = max(
                    o.width * 0.5, min(L - o.width * 0.5, float(geom.dot(center - a, d)))
                )
                o.width = min(o.width, max(L - 0.02, 0.1))
            merged += 1
        return merged

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "elevation": self.elevation,
            "ceiling_height": self.ceiling_height,
            "slab_thickness": self.slab_thickness,
            "vertices": [v.to_dict() for v in self.vertices.values()],
            "walls": [w.to_dict() for w in self.walls.values()],
            "openings": [o.to_dict() for o in self.openings.values()],
            "room_labels": [r.to_dict() for r in self.room_labels],
            "meta": self.meta,
        }

    @staticmethod
    def from_dict(d: dict) -> "FloorPlan":
        fp = FloorPlan(
            name=str(d.get("name", "1F")),
            elevation=float(d.get("elevation", 0.0)),
            ceiling_height=float(d.get("ceiling_height", DEFAULT_WALL_HEIGHT)),
            slab_thickness=float(d.get("slab_thickness", 0.12)),
            meta=dict(d.get("meta", {})),
        )
        for vd in d.get("vertices", []):
            v = Vertex.from_dict(vd)
            fp.vertices[v.id] = v
        for wd in d.get("walls", []):
            w = Wall.from_dict(wd)
            fp.walls[w.id] = w
        for od in d.get("openings", []):
            o = Opening.from_dict(od)
            fp.openings[o.id] = o
        for rd in d.get("room_labels", []):
            fp.room_labels.append(RoomLabel.from_dict(rd))
        return fp

    def clone(self) -> "FloorPlan":
        return FloorPlan.from_dict(copy.deepcopy(self.to_dict()))


# ================================================================ 工程


@dataclass
class Project:
    name: str = "未命名户型"
    units: str = "m"
    plans: list[FloorPlan] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> FloorPlan:
        if not self.plans:
            self.plans.append(FloorPlan(name="1F"))
        return self.plans[0]

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "version": SCHEMA_VERSION,
            "name": self.name,
            "units": self.units,
            "plans": [p.to_dict() for p in self.plans],
            "meta": self.meta,
        }

    @staticmethod
    def from_dict(d: dict) -> "Project":
        if d.get("format") not in (None, FORMAT):
            raise ValueError(f"不是 scan2plan 户型文件: {d.get('format')!r}")
        return Project(
            name=str(d.get("name", "未命名户型")),
            units=str(d.get("units", "m")),
            plans=[FloorPlan.from_dict(p) for p in d.get("plans", [])],
            meta=dict(d.get("meta", {})),
        )

    def clone(self) -> "Project":
        return Project.from_dict(copy.deepcopy(self.to_dict()))


# ================================================================ 几何辅助


def offset_ring(ring: Sequence[Sequence[float]], dists: Sequence[float]) -> list[XY] | None:
    """把多边形每条边沿内法向偏移 dists[i]，再求相邻偏移线交点。

    ring 必须逆时针（内法向 = 左法向 = perp(边方向)）。
    当偏移量超过房间局部宽度时会产生自交，返回 None 由调用方丢弃。
    """
    n = len(ring)
    if n < 3 or len(dists) != n:
        return None
    lines: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(n):
        a = as_xy(ring[i])
        b = as_xy(ring[(i + 1) % n])
        d = unit(b - a)
        if geom.length(d) < 1e-9:
            return None
        nrm = geom.perp(d)
        lines.append((a + nrm * float(dists[i]), d))

    out: list[np.ndarray] = []
    for i in range(n):
        p1, d1 = lines[i - 1]
        p2, d2 = lines[i]
        q = geom.line_intersect_infinite(p1, p1 + d1, p2, p2 + d2)
        if q is None:
            # 相邻边共线：直接沿法向平移
            out.append(as_xy(ring[i]) + geom.perp(unit(as_xy(ring[i]) - as_xy(ring[i - 1]))) * dists[i])
            continue
        if geom.length(q - as_xy(ring[i])) > 20.0 * max(max(dists), 0.05):
            return None
        out.append(q)
    # 自交检测：面积符号翻转 / 出现重复点
    if abs(geom.polygon_area(out)) < 1e-9:
        return None
    if geom.polygon_area(out) < 0:
        return None
    return geom.dedupe_ring(out)


def _convex_hull(points: np.ndarray) -> list[np.ndarray]:
    """Andrew monotone chain 凸包。"""
    pts = sorted({(round(float(p[0]), 6), round(float(p[1]), 6)) for p in points})
    if len(pts) <= 2:
        return [np.array(p) for p in pts]

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and (
                (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])
            ) <= 0:
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(list(reversed(pts)))
    return [np.array(p) for p in (lower[:-1] + upper[:-1])]
