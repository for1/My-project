"""把 Project 变成前端可直接渲染的派生几何数据。

设计原则：**几何只算一次，算在 Python 里**。
浏览器端不重复实现 Rooms/WallBody/Mesh 算法，只负责画和交互，
避免出现两套几何逻辑（这是同类工具最常见的 bug 来源）。
"""
from __future__ import annotations

import base64
import struct
from typing import Any

import numpy as np

from .mesh import MATERIAL_COLORS, Mesh, build_plan_mesh, mesh_report
from .model import FloorPlan, Project


def _pack_mesh(mesh: Mesh) -> dict[str, Any]:
    """把网格打包成前端友好的紧凑结构。

    顶点/索引以 base64 Float32/Uint32 传输；按材质分组，每组一个 primitive。
    """
    verts = np.asarray(mesh.vertices, dtype=np.float32).reshape(-1, 3)
    faces = np.asarray(mesh.faces, dtype=np.int64).reshape(-1, 3)

    by_mat: dict[str, list[int]] = {}
    for fi, m in enumerate(mesh.face_materials):
        by_mat.setdefault(m, []).append(fi)

    prims: list[dict[str, Any]] = []
    for mat in sorted(by_mat):
        fids = np.array(by_mat[mat], dtype=np.int64)
        tri = faces[fids]  # (n, 3)
        flat = tri.reshape(-1)
        # 去重顶点（位置相同即复用）
        uniq, inverse = np.unique(flat, return_inverse=True)
        pos = verts[uniq]
        idx = inverse.astype(np.uint32).reshape(-1)
        prims.append(
            {
                "material": mat,
                "color": list(MATERIAL_COLORS.get(mat, (0.8, 0.8, 0.8, 1.0))),
                "positions": base64.b64encode(pos.tobytes()).decode("ascii"),
                "indices": base64.b64encode(idx.tobytes()).decode("ascii"),
                "vertexCount": int(pos.shape[0]),
                "indexCount": int(idx.shape[0]),
            }
        )
    mn, mx = mesh.bounds()
    return {
        "primitives": prims,
        "bboxMin": [float(v) for v in mn],
        "bboxMax": [float(v) for v in mx],
        "report": mesh_report(mesh),
    }


def derive_plan(
    fp: FloorPlan,
    *,
    include_mesh: bool = True,
    include_ceiling: bool = False,
) -> dict[str, Any]:
    bodies = fp.wall_body_map()
    walls = []
    for w in fp.walls.values():
        body = bodies.get(w.id)
        a, b = fp.wall_endpoints(w.id)
        entry: dict[str, Any] = {
            "id": w.id,
            "start": w.start,
            "end": w.end,
            "a": [round(float(a[0]), 5), round(float(a[1]), 5)],
            "b": [round(float(b[0]), 5), round(float(b[1]), 5)],
            "thickness": w.thickness,
            "height": w.height,
            "kind": w.kind,
            "length": round(fp.wall_length(w.id), 5),
            "angle": round(fp.wall_angle(w.id), 4),
        }
        if body is not None:
            entry["body"] = {
                "polygon": [[round(x, 5), round(y, 5)] for x, y in body.polygon()],
                "p0L": [round(float(body.p0L[0]), 5), round(float(body.p0L[1]), 5)],
                "p1L": [round(float(body.p1L[0]), 5), round(float(body.p1L[1]), 5)],
                "p0R": [round(float(body.p0R[0]), 5), round(float(body.p0R[1]), 5)],
                "p1R": [round(float(body.p1R[0]), 5), round(float(body.p1R[1]), 5)],
                "startKind": body.start_kind,
                "endKind": body.end_kind,
                "area": round(body.area(), 5),
            }
        walls.append(entry)

    quads = {}
    for o in fp.openings.values():
        body = bodies.get(o.wall)
        if body is None:
            continue
        s0, s1 = body.opening_frame(o.offset, o.width)
        quads[o.id] = [[round(x, 5), round(y, 5)] for x, y in body.sub(s0, s1)]

    openings = []
    for o in fp.openings.values():
        openings.append(
            {
                **o.to_dict(),
                "head": round(o.head, 5),
                "quad": quads.get(o.id, []),
            }
        )

    rooms = [r.to_dict() for r in fp.rooms()]

    out: dict[str, Any] = {
        "name": fp.name,
        "elevation": fp.elevation,
        "ceilingHeight": fp.ceiling_height,
        "slabThickness": fp.slab_thickness,
        "vertices": [
            {"id": v.id, "xy": [round(v.xy[0], 5), round(v.xy[1], 5)]}
            for v in fp.vertices.values()
        ],
        "walls": walls,
        "openings": openings,
        "rooms": rooms,
        "roomLabels": [
            {"name": r.name, "xy": [round(r.xy[0], 5), round(r.xy[1], 5)], "kind": r.kind}
            for r in fp.room_labels
        ],
        "stats": fp.stats(),
        "outline": [[round(x, 5), round(y, 5)] for x, y in fp.outline()],
        "problems": fp.validate(),
    }
    if include_mesh:
        mesh = build_plan_mesh(fp, include_ceiling=include_ceiling)
        out["mesh"] = _pack_mesh(mesh)
    return out


def derive(project: Project, *, include_mesh: bool = True) -> dict[str, Any]:
    return {
        "name": project.name,
        "plans": [
            derive_plan(p, include_mesh=include_mesh and i == 0)
            for i, p in enumerate(project.plans)
        ],
    }
