"""编辑操作（Op）解释器。

前端只发「操作」，Python 端负责把它作用到模型上 —— 这样编辑语义只有一份实现，
CLI、Web、脚本调用完全一致。

每个 op 是 ``{"op": "<name>", ...参数}``。所有 op 都作用在 ``project.plans[plan]``。
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from .geom import as_xy
from .model import FloorPlan, Opening, Project, RoomLabel


class OpError(ValueError):
    """操作用户输入错误（前端可直接展示）。"""


def _plan(project: Project, plan_index: int = 0) -> FloorPlan:
    if plan_index < 0 or plan_index >= len(project.plans):
        raise OpError(f"plan index {plan_index} 越界（共 {len(project.plans)} 层）")
    return project.plans[plan_index]


def _wall(fp: FloorPlan, wall_id: str):
    w = fp.walls.get(wall_id)
    if w is None:
        raise OpError(f"墙不存在: {wall_id}")
    return w


def _opening(fp: FloorPlan, opening_id: str) -> Opening:
    o = fp.openings.get(opening_id)
    if o is None:
        raise OpError(f"门窗不存在: {opening_id}")
    return o


def apply_op(project: Project, op: dict[str, Any]) -> None:
    """执行单个操作（就地修改 project）。"""
    name = str(op.get("op", ""))
    pi = int(op.get("plan", 0))
    fp = _plan(project, pi)
    a = op  # 简写

    if name == "move_vertex":
        vid = str(a["id"])
        if vid not in fp.vertices:
            raise OpError(f"顶点不存在: {vid}")
        old = as_xy(fp.vertices[vid].xy)
        new = as_xy(a["xy"])
        fp.vertices[vid].xy = (float(new[0]), float(new[1]))
        if a.get("adjust_neighbors", True):
            fp._shift_neighbor_fars(vid, new - old, old)

    elif name == "move_vertex_free":
        vid = str(a["id"])
        if vid not in fp.vertices:
            raise OpError(f"顶点不存在: {vid}")
        fp.move_vertex(vid, a["xy"])

    elif name == "move_wall":
        wid = str(a["id"])
        w = _wall(fp, wid)
        delta = as_xy(a["delta"])
        old_a, old_b = fp.wall_endpoints(wid)
        # 先带上落在墙身上的 T 型接头端点，避免平移后房间脱开
        fp._transform_attached(wid, np.zeros(2), delta=delta)
        for vid, old in ((w.start, old_a), (w.end, old_b)):
            fp._shift_neighbor_fars(vid, delta, old, exclude=wid)
            v = fp.vertices[vid]
            fp.vertices[vid].xy = (v.x + float(delta[0]), v.y + float(delta[1]))

    elif name == "set_wall_length":
        wid = str(a["id"])
        _wall(fp, wid)
        L = float(a["length"])
        if L <= 0:
            raise OpError("墙长必须大于 0")
        fp.set_wall_length(wid, L, pivot=str(a.get("pivot", "start")))

    elif name == "set_wall_angle":
        wid = str(a["id"])
        _wall(fp, wid)
        fp.set_wall_angle(wid, float(a["angle"]))

    elif name == "set_wall_thickness":
        wid = str(a["id"])
        _wall(fp, wid)
        t = float(a["thickness"])
        if t <= 0:
            raise OpError("墙厚必须大于 0")
        fp.set_wall_thickness(wid, t)

    elif name == "set_wall_meta":
        wid = str(a["id"])
        w = _wall(fp, wid)
        if "kind" in a:
            w.kind = str(a["kind"])
        if "height" in a:
            w.height = float(a["height"])

    elif name == "add_wall":
        fp.add_wall(
            a["a"],
            a["b"],
            thickness=float(a.get("thickness", 0.12)),
            kind=str(a.get("kind", "interior")),
            source="manual",
        )

    elif name == "delete_wall":
        wid = str(a["id"])
        _wall(fp, wid)
        fp.delete_wall(wid)

    elif name == "split_wall":
        wid = str(a["id"])
        _wall(fp, wid)
        at = a["at"]
        if isinstance(at, (int, float)):
            fp.split_wall(wid, float(at))
        else:
            fp.split_wall(wid, tuple(at))

    elif name == "add_opening":
        wid = str(a["wall"])
        _wall(fp, wid)
        fp.add_opening(
            wid,
            kind=str(a.get("kind", "door")),
            offset=a.get("offset"),
            width=a.get("width"),
            height=a.get("height"),
            sill=a.get("sill"),
            swing=str(a.get("swing", "left")),
            swing_in=bool(a.get("swing_in", True)),
        )

    elif name == "update_opening":
        o = _opening(fp, str(a["id"]))
        for key in ("kind", "swing"):
            if key in a:
                setattr(o, key, str(a[key]))
        for key in ("offset", "width", "height", "sill"):
            if key in a:
                setattr(o, key, float(a[key]))
        if "swing_in" in a:
            o.swing_in = bool(a["swing_in"])
        # 夹到墙内
        L = fp.wall_length(o.wall)
        o.width = max(0.1, min(o.width, max(L - 0.02, 0.1)))
        o.offset = max(o.width / 2, min(L - o.width / 2, o.offset))
        o.height = max(0.1, min(o.height, fp.ceiling_height))
        o.sill = max(0.0, min(o.sill, fp.ceiling_height - o.height))

    elif name == "move_opening":
        o = _opening(fp, str(a["id"]))
        L = fp.wall_length(o.wall)
        o.offset = max(o.width / 2, min(L - o.width / 2, float(a["offset"])))

    elif name == "delete_opening":
        oid = str(a["id"])
        _opening(fp, oid)
        fp.delete_opening(oid)

    elif name == "rename_room":
        idx = int(a["index"])
        rooms = fp.rooms()
        if idx < 0 or idx >= len(rooms):
            raise OpError(f"房间序号 {idx} 越界")
        room = rooms[idx]
        label = str(a.get("label", "")).strip() or room.label
        kind = str(a.get("kind", room.kind))
        # 用房间质心作为命名锚点，替换附近已有锚点
        fp.room_labels = [
            r
            for r in fp.room_labels
            if _dist2(r.xy, room.centroid) > 0.25
        ]
        fp.room_labels.append(RoomLabel(label, room.centroid, kind))

    elif name == "calibrate":
        wid = str(a["id"])
        _wall(fp, wid)
        measured = float(a["length"])
        if measured <= 0:
            raise OpError("实测长度必须大于 0")
        fp.calibrate_by_wall(wid, measured)

    elif name == "scale":
        f = float(a["factor"])
        if f <= 0:
            raise OpError("缩放系数必须大于 0")
        fp.scale_all(f)

    elif name == "snap_orthogonal":
        fp.snap_orthogonal(
            origin_deg=float(a.get("origin", 0.0)),
            step_deg=float(a.get("step", 90.0)),
            tol_deg=float(a.get("tol", 8.0)),
        )

    elif name == "weld":
        fp.weld(float(a.get("tol", 0.02)))

    elif name == "merge_collinear":
        fp.merge_collinear()

    elif name == "set_ceiling_height":
        fp.ceiling_height = max(1.8, min(6.0, float(a["height"])))

    elif name == "set_slab_thickness":
        fp.slab_thickness = max(0.0, min(1.0, float(a["thickness"])))

    elif name == "move_room_label":
        idx = int(a["index"])
        rooms = fp.rooms()
        if 0 <= idx < len(rooms):
            room = rooms[idx]
            target = as_xy(a["xy"])
            fp.room_labels = [
                r for r in fp.room_labels if _dist2(r.xy, room.centroid) > 0.25
            ]
            fp.room_labels.append(RoomLabel(room.label, target, room.kind))

    else:
        raise OpError(f"未知操作: {name}")


def _dist2(p, q) -> float:
    dx = float(p[0]) - float(q[0])
    dy = float(p[1]) - float(q[1])
    return dx * dx + dy * dy


def apply_ops(project: Project, ops: Iterable[dict[str, Any]]) -> Project:
    for op in ops:
        apply_op(project, op)
    return project
