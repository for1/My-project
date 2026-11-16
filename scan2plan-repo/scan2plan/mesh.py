"""3D 网格层：把户型图变成三角网格。

这是 OBJ / GLB / FBX / IFC 导出的公共底座。设计要点：
    * 墙体直接用 ``WallBody.boxes_with_openings`` 切成盒体再拉伸，
      门窗洞口天然成为真空，全程不做布尔运算。
    * 地面 / 天花 / 楼板按房间净多边形三角剖分。
    * 所有面都带材质名，便于导出 OBJ(mtl) / GLB(material) / IFC(元素类型)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from .geom import XY, as_xy, polygon_area
from .model import FloorPlan, Opening, Project

Vec3 = tuple[float, float, float]

# 材质名（同时作为 OBJ 的 usemtl 与 GLB 的 material name）
MAT_WALL_EXT = "wall_exterior"
MAT_WALL_INT = "wall_interior"
MAT_FLOOR = "floor"
MAT_SLAB = "slab"
MAT_CEILING = "ceiling"
MAT_DOOR = "door"
MAT_WINDOW = "window"
MAT_FRAME = "frame"

MATERIAL_COLORS: dict[str, tuple[float, float, float, float]] = {
    MAT_WALL_EXT: (0.92, 0.90, 0.86, 1.0),
    MAT_WALL_INT: (0.96, 0.95, 0.93, 1.0),
    MAT_FLOOR: (0.62, 0.52, 0.42, 1.0),
    MAT_SLAB: (0.70, 0.70, 0.72, 1.0),
    MAT_CEILING: (0.98, 0.98, 0.98, 1.0),
    MAT_DOOR: (0.55, 0.38, 0.24, 1.0),
    MAT_WINDOW: (0.55, 0.75, 0.85, 0.45),
    MAT_FRAME: (0.35, 0.35, 0.38, 1.0),
}


# ================================================================ 三角剖分


def triangulate(ring: Sequence[Sequence[float]]) -> list[tuple[int, int, int]]:
    """耳切法（ear clipping）三角剖分简单多边形。

    输入需为逆时针；返回顶点索引三元组列表。
    对自交多边形不保证正确（户型房间不应自交）。
    """
    pts = [as_xy(p) for p in ring]
    n = len(pts)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]

    idx = list(range(n))
    if polygon_area(pts) < 0:
        idx.reverse()

    def is_convex(a: int, b: int, c: int) -> bool:
        return (pts[b][0] - pts[a][0]) * (pts[c][1] - pts[a][1]) - (
            pts[b][1] - pts[a][1]
        ) * (pts[c][0] - pts[a][0]) > 0

    def in_triangle(p, a, b, c) -> bool:
        def sign(p1, p2, p3):
            return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

        d1, d2, d3 = sign(p, a, b), sign(p, b, c), sign(p, c, a)
        has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
        has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
        return not (has_neg and has_pos)

    tris: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n * n:
        guard += 1
        found = False
        for k in range(len(idx)):
            i0 = idx[k - 1]
            i1 = idx[k]
            i2 = idx[(k + 1) % len(idx)]
            if not is_convex(i0, i1, i2):
                continue
            a, b, c = pts[i0], pts[i1], pts[i2]
            bad = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                if in_triangle(pts[j], a, b, c):
                    bad = True
                    break
            if bad:
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            found = True
            break
        if not found:
            # 退化多边形：退化为扇形剖分，保证不无限循环
            for k in range(1, len(idx) - 1):
                tris.append((idx[0], idx[k], idx[k + 1]))
            return tris
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


# ================================================================ 网格


@dataclass
class Mesh:
    vertices: list[Vec3] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    face_materials: list[str] = field(default_factory=list)
    object_names: list[str] = field(default_factory=list)  # 每个面所属对象名
    _object_faces: dict[str, list[int]] = field(default_factory=dict)

    # ------------------------------------------------------------ 构建

    def _add_vertex(self, p: Sequence[float]) -> int:
        self.vertices.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.vertices) - 1

    def add_face(self, pts: Sequence[Sequence[float]], material: str, obj: str) -> None:
        base = len(self.vertices)
        for p in pts:
            self._add_vertex(p)
        for k in range(1, len(pts) - 1):
            self.faces.append((base, base + k, base + k + 1))
            self.face_materials.append(material)
            self.object_names.append(obj)
            self._object_faces.setdefault(obj, []).append(len(self.faces) - 1)

    def add_prism(
        self,
        ring: Sequence[Sequence[float]],
        z0: float,
        z1: float,
        material: str,
        obj: str,
        *,
        bottom: bool = True,
        top: bool = True,
        sides: bool = True,
    ) -> None:
        """把一个逆时针多边形沿 Z 拉伸成棱柱。"""
        ring = list(ring)
        if len(ring) < 3:
            return
        if polygon_area(ring) < 0:
            ring = list(reversed(ring))
        n = len(ring)
        if sides:
            for i in range(n):
                a = ring[i]
                b = ring[(i + 1) % n]
                self.add_face(
                    [
                        (a[0], a[1], z0),
                        (b[0], b[1], z0),
                        (b[0], b[1], z1),
                        (a[0], a[1], z1),
                    ],
                    material,
                    obj,
                )
        if bottom:
            tri = triangulate(ring)
            for i, j, k in tri:
                # 底面朝下，需反向
                self.add_face(
                    [
                        (ring[k][0], ring[k][1], z0),
                        (ring[j][0], ring[j][1], z0),
                        (ring[i][0], ring[i][1], z0),
                    ],
                    material,
                    obj,
                )
        if top:
            tri = triangulate(ring)
            for i, j, k in tri:
                self.add_face(
                    [
                        (ring[i][0], ring[i][1], z1),
                        (ring[j][0], ring[j][1], z1),
                        (ring[k][0], ring[k][1], z1),
                    ],
                    material,
                    obj,
                )

    def add_polygon(self, ring: Sequence[Sequence[float]], z: float, material: str, obj: str, up: bool = True) -> None:
        ring = list(ring)
        if len(ring) < 3:
            return
        if polygon_area(ring) < 0:
            ring = list(reversed(ring))
        if not up:
            ring = list(reversed(ring))
        tri = triangulate(ring)
        for i, j, k in tri:
            self.add_face(
                [
                    (ring[i][0], ring[i][1], z),
                    (ring[j][0], ring[j][1], z),
                    (ring[k][0], ring[k][1], z),
                ],
                material,
                obj,
            )

    # ------------------------------------------------------------ 统计

    @property
    def triangle_count(self) -> int:
        return len(self.faces)

    @property
    def object_names_all(self) -> list[str]:
        return list(self._object_faces.keys())

    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.vertices:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        a = np.array(self.vertices)
        mn = a.min(axis=0)
        mx = a.max(axis=0)
        return (tuple(mn.tolist()), tuple(mx.tolist()))

    def volume(self) -> float:
        """有符号体积（散度定理），可用来校验网格封闭性。正值表示法线朝外。"""
        v = 0.0
        for i, j, k in self.faces:
            p, q, r = self.vertices[i], self.vertices[j], self.vertices[k]
            v += (
                p[0] * (q[1] * r[2] - q[2] * r[1])
                - p[1] * (q[0] * r[2] - q[2] * r[0])
                + p[2] * (q[0] * r[1] - q[1] * r[0])
            ) / 6.0
        return v


# ================================================================ 由户型生成网格


def build_plan_mesh(
    plan: FloorPlan,
    *,
    include_floor: bool = True,
    include_slab: bool = True,
    include_ceiling: bool = False,
    include_openings: bool = True,
    opening_frame: bool = True,
) -> Mesh:
    """把一层户型图构建成三角网格。"""
    mesh = Mesh()
    base_z = float(plan.elevation)
    H = float(plan.ceiling_height)
    slab = float(plan.slab_thickness)

    # ---------------- 墙体（含门窗洞口）
    for body in plan.wall_bodies():
        wall = plan.walls.get(body.wall_id)
        if wall is None:
            continue
        mat = MAT_WALL_EXT if wall.kind == "exterior" else MAT_WALL_INT
        ops = plan.openings_of(wall.id)
        rects = [
            (o.offset, o.width, o.sill, min(o.sill + o.height, wall.height))
            for o in ops
        ]
        boxes = body.boxes_with_openings(rects, wall.height)
        for poly, z0, z1 in boxes:
            if z1 - z0 < 1e-6:
                continue
            mesh.add_prism(
                poly,
                base_z + z0,
                base_z + z1,
                mat,
                f"Wall:{wall.id}",
            )

    # ---------------- 门窗面板
    if include_openings:
        body_map = plan.wall_body_map()
        for o in plan.openings.values():
            body = body_map.get(o.wall)
            if body is None:
                continue
            s0, s1 = body.opening_frame(o.offset, o.width)
            quad = body.sub(s0, s1)
            if len(quad) < 3:
                continue
            z0 = base_z + float(o.sill)
            z1 = base_z + float(min(o.sill + o.height, plan.ceiling_height))
            if o.kind == "passage":
                # 垭口不装面板，只画洞口边框
                continue
            mat = MAT_DOOR if o.kind == "door" else MAT_WINDOW
            obj = f"{'Door' if o.kind == 'door' else 'Window'}:{o.id}"
            if opening_frame:
                t = 0.05
                # 四周框：沿洞口边界做一圈细棱柱
                for frac0, frac1, top in (
                    (0.0, t / max(o.width, 0.01), False),
                    (1.0 - t / max(o.width, 0.01), 1.0, False),
                ):
                    q0, q1 = body.opening_frame(o.offset - o.width / 2 + frac0 * o.width,
                                                (frac1 - frac0) * o.width)
                    seg = body.sub(q0, q1)
                    if len(seg) >= 3:
                        mesh.add_prism(seg, z0, z1, MAT_FRAME, obj)
                # 面板本体：取洞口四边形的中点后半厚薄板
                cx = sum(p[0] for p in quad) / len(quad)
                cy = sum(p[1] for p in quad) / len(quad)
                thin = 0.02
                shrunk = [
                    (cx + (p[0] - cx) * 0.9, cy + (p[1] - cy) * 0.9) for p in quad
                ]
                mesh.add_prism(shrunk, z0, z1, mat, obj)
            else:
                mesh.add_prism(quad, z0, z1, mat, obj)

    # ---------------- 地面 / 楼板 / 天花
    rooms = plan.rooms()
    for room in rooms:
        if include_slab:
            mesh.add_prism(
                room.polygon,
                base_z - slab,
                base_z,
                MAT_SLAB,
                f"Slab:{room.label}",
            )
        if include_floor:
            mesh.add_polygon(
                room.polygon, base_z + 0.001, MAT_FLOOR, f"Floor:{room.label}", up=True
            )
        if include_ceiling:
            mesh.add_polygon(
                room.polygon,
                base_z + H,
                MAT_CEILING,
                f"Ceiling:{room.label}",
                up=False,
            )
    return mesh


def build_project_mesh(project: Project, **kwargs) -> Mesh:
    """多楼层合并网格（各层按 elevation 叠放）。"""
    out = Mesh()
    for plan in project.plans:
        m = build_plan_mesh(plan, **kwargs)
        offset = len(out.vertices)
        out.vertices.extend(m.vertices)
        out.faces.extend((i + offset, j + offset, k + offset) for i, j, k in m.faces)
        out.face_materials.extend(m.face_materials)
        out.object_names.extend(m.object_names)
        for name, fids in m._object_faces.items():
            out._object_faces.setdefault(name, []).extend(f + offset for f in fids)
    return out


def mesh_report(mesh: Mesh) -> dict:
    mn, mx = mesh.bounds()
    return {
        "vertices": len(mesh.vertices),
        "triangles": mesh.triangle_count,
        "objects": len(mesh._object_faces),
        "materials": sorted(set(mesh.face_materials)),
        "bbox_min": [round(v, 3) for v in mn],
        "bbox_max": [round(v, 3) for v in mx],
        "volume_m3": round(mesh.volume(), 4),
    }
