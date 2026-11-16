"""把墙线段矢量化成可编辑的 FloorPlan。

输入是从点云量出来的墙段（中线 + 厚度 + 门窗），输出是模型层的 FloorPlan。
这一步决定"图好不好看、能不能编辑"，关键动作：

    1. **坐标归一**：同一条直线上的墙段强制共用同一个坐标（否则会有 1~2mm 错台）
    2. **端点吸附**：端点吸到最近的垂直墙上（补齐遮挡造成的缺口）
    3. **端点延伸**：还够不着的，沿自身方向延伸到垂直墙线（限幅，避免乱伸）
    4. **去重合并**：同一直线上重叠的墙段合成一段
    5. **门窗重定位**：墙端点移动后，按世界坐标把门窗投影回新墙上
    6. 焊接顶点 → 房间自动从墙中线图的面派生出来（整户多房间一次成型）
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace

import numpy as np

from ..geom import as_xy, unit
from ..model import (
    DEFAULT_WALL_HEIGHT,
    FloorPlan,
    Opening,
    RoomLabel,
)
from .walls import ReconstructionOptions, WallSegment


@dataclass
class VectorizeOptions:
    coord_snap: float = 0.012      # 同一直线的坐标归并容差
    endpoint_snap: float = 0.18    # 端点吸到垂直墙的容差
    extend_tol: float = 0.35       # 端点沿线延伸到垂直墙的最大距离
    corner_tol: float = 0.32       # 拐角补全容差（两面墙各自短一截时互相接上）
    min_length: float = 0.30
    weld_tol: float = 0.02
    door_height: float = 2.10
    window_height: float = 1.50
    window_sill: float = 0.90
    passage_height: float = 2.30


# ================================================================ 辅助


def _oid() -> str:
    return f"o_{uuid.uuid4().hex[:10]}"


def _complete_corners(
    horiz: list[WallSegment], vert: list[WallSegment], tol: float
) -> int:
    """拐角补全：两段互相垂直的墙各短一截时，把它们都延伸到交点。

    这是重建拓扑最关键的一步。点云在墙角处往往有一小段缺失（视场/遮挡），
    导致每面墙都比真值短 10~30cm，房间因此闭合不了。
    做法：枚举 H×V 组合，若交点距两段都小于 tol，就把两段的近端都补到交点。
    """
    fixed = 0
    for h in horiz:
        hx0, hx1 = sorted((float(h.p0[0]), float(h.p1[0])))
        hy = float(h.p0[1])
        for v in vert:
            vx = float(v.p0[0])
            vy0, vy1 = sorted((float(v.p0[1]), float(v.p1[1])))
            # 交点到两段的距离
            if hx0 <= vx <= hx1:
                dH = 0.0
            else:
                dH = min(abs(vx - hx0), abs(vx - hx1))
            if vy0 <= hy <= vy1:
                dV = 0.0
            else:
                dV = min(abs(hy - vy0), abs(hy - vy1))
            if dH > tol or dV > tol:
                continue
            changed = False
            if dH > 1e-6:
                # 延伸 H 较近的那一端
                if abs(vx - hx0) <= abs(vx - hx1):
                    if h.p0[0] > h.p1[0]:
                        h.p1 = np.array([vx, hy])
                    else:
                        h.p0 = np.array([vx, hy])
                else:
                    if h.p0[0] > h.p1[0]:
                        h.p0 = np.array([vx, hy])
                    else:
                        h.p1 = np.array([vx, hy])
                changed = True
            if dV > 1e-6:
                if abs(hy - vy0) <= abs(hy - vy1):
                    if v.p0[1] > v.p1[1]:
                        v.p1 = np.array([vx, hy])
                    else:
                        v.p0 = np.array([vx, hy])
                else:
                    if v.p0[1] > v.p1[1]:
                        v.p0 = np.array([vx, hy])
                    else:
                        v.p1 = np.array([vx, hy])
                changed = True
            if changed:
                fixed += 1
                hx0, hx1 = sorted((float(h.p0[0]), float(h.p1[0])))
            # 更新 V 的坐标缓存
            vy0, vy1 = sorted((float(v.p0[1]), float(v.p1[1])))
    return fixed


def _is_horizontal(seg: WallSegment, tol: float = 1e-6) -> bool:
    return abs(seg.p1[1] - seg.p0[1]) <= tol


def _norm_orientation(seg: WallSegment) -> WallSegment:
    """统一方向：水平墙从左到右，竖直墙从下到上。"""
    if _is_horizontal(seg):
        if seg.p0[0] > seg.p1[0]:
            return replace(seg, p0=seg.p1.copy(), p1=seg.p0.copy())
    else:
        if seg.p0[1] > seg.p1[1]:
            return replace(seg, p0=seg.p1.copy(), p1=seg.p0.copy())
    return seg


def _snap_coords(values: list[float], tol: float) -> list[float]:
    """一维坐标归并：把相差小于 tol 的值统一成一个代表值。"""
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups: list[list[int]] = [[order[0]]]
    for k in order[1:]:
        if values[k] - values[groups[-1][-1]] <= tol:
            groups[-1].append(k)
        else:
            groups.append([k])
    out = list(values)
    for g in groups:
        mean = sum(values[i] for i in g) / len(g)
        for i in g:
            out[i] = mean
    return out


# ================================================================ 主流程


def segments_to_floorplan(
    segments: list[WallSegment],
    *,
    opt: ReconstructionOptions | None = None,
    vopt: VectorizeOptions | None = None,
    ceiling_height: float | None = None,
    name: str = "1F",
) -> tuple[FloorPlan, dict]:
    rcon = opt or ReconstructionOptions()
    vopt = vopt or VectorizeOptions()
    diag: dict = {"input_segments": len(segments)}

    segs = [_norm_orientation(s) for s in segments]
    segs = [s for s in segs if s.length >= vopt.min_length]

    horiz = [s for s in segs if _is_horizontal(s)]
    vert = [s for s in segs if not _is_horizontal(s)]
    diag["horizontal"] = len(horiz)
    diag["vertical"] = len(vert)

    # 1) 坐标归一
    ys = _snap_coords([float(s.p0[1]) for s in horiz], vopt.coord_snap)
    for s, y in zip(horiz, ys):
        s.p0 = np.array([s.p0[0], y])
        s.p1 = np.array([s.p1[0], y])
    xs = _snap_coords([float(s.p0[0]) for s in vert], vopt.coord_snap)
    for s, x in zip(vert, xs):
        s.p0 = np.array([x, s.p0[1]])
        s.p1 = np.array([x, s.p1[1]])

    # 2) 端点吸附到垂直墙
    for s in horiz:
        y = s.p0[1]
        for end in (0, 1):
            x = s.p0[0] if end == 0 else s.p1[0]
            best = None
            for v in vert:
                vx = v.p0[0]
                lo, hi = min(v.p0[1], v.p1[1]), max(v.p0[1], v.p1[1])
                if lo - vopt.endpoint_snap <= y <= hi + vopt.endpoint_snap:
                    d = abs(vx - x)
                    if d <= vopt.endpoint_snap and (best is None or d < best[0]):
                        best = (d, vx)
            if best is not None:
                if end == 0:
                    s.p0 = np.array([best[1], y])
                else:
                    s.p1 = np.array([best[1], y])
    for s in vert:
        x = s.p0[0]
        for end in (0, 1):
            y = s.p0[1] if end == 0 else s.p1[1]
            best = None
            for h in horiz:
                hy = h.p0[1]
                lo, hi = min(h.p0[0], h.p1[0]), max(h.p0[0], h.p1[0])
                if lo - vopt.endpoint_snap <= x <= hi + vopt.endpoint_snap:
                    d = abs(hy - y)
                    if d <= vopt.endpoint_snap and (best is None or d < best[0]):
                        best = (d, hy)
            if best is not None:
                if end == 0:
                    s.p0 = np.array([x, best[1]])
                else:
                    s.p1 = np.array([x, best[1]])

    # 3) 端点延伸补缺口
    for s in horiz:
        y = s.p0[1]
        targets = sorted({float(v.p0[0]) for v in vert})
        for end in (0, 1):
            x = s.p0[0] if end == 0 else s.p1[0]
            cand = [t for t in targets if abs(t - x) <= vopt.extend_tol and abs(t - x) > 1e-6]
            if not cand:
                continue
            # 只延伸到"跨越该 y"的竖直墙
            ok = []
            for t in cand:
                for v in vert:
                    if abs(v.p0[0] - t) > 1e-6:
                        continue
                    lo, hi = min(v.p0[1], v.p1[1]), max(v.p0[1], v.p1[1])
                    if lo - vopt.endpoint_snap <= y <= hi + vopt.endpoint_snap:
                        ok.append(t)
                        break
            if not ok:
                continue
            t = min(ok, key=lambda t: abs(t - x))
            if end == 0:
                s.p0 = np.array([t, y])
            else:
                s.p1 = np.array([t, y])
    for s in vert:
        x = s.p0[0]
        targets = sorted({float(h.p0[1]) for h in horiz})
        for end in (0, 1):
            y = s.p0[1] if end == 0 else s.p1[1]
            cand = [t for t in targets if abs(t - y) <= vopt.extend_tol and abs(t - y) > 1e-6]
            if not cand:
                continue
            ok = []
            for t in cand:
                for h in horiz:
                    if abs(h.p0[1] - t) > 1e-6:
                        continue
                    lo, hi = min(h.p0[0], h.p1[0]), max(h.p0[0], h.p1[0])
                    if lo - vopt.endpoint_snap <= x <= hi + vopt.endpoint_snap:
                        ok.append(t)
                        break
            if not ok:
                continue
            t = min(ok, key=lambda t: abs(t - y))
            if end == 0:
                s.p0 = np.array([x, t])
            else:
                s.p1 = np.array([x, t])

    # 4) 拐角补全（两次迭代，第一次补全后可能让第二次能接上）
    corner_fixes = 0
    for _ in range(2):
        corner_fixes += _complete_corners(horiz, vert, vopt.corner_tol)
    diag["corner_fixes"] = corner_fixes

    # 5) 去重合并同一直线上的重叠段
    horiz = _merge_horiz(horiz, vopt.coord_snap)
    vert = _merge_vert(vert, vopt.coord_snap)

    final = [s for s in horiz + vert if s.length >= vopt.min_length]
    diag["final_segments"] = len(final)

    # 5) 建 FloorPlan
    fp = FloorPlan(name=name, ceiling_height=float(ceiling_height or DEFAULT_WALL_HEIGHT))
    ext_thick = rcon.thickness_ext
    for s in final:
        thick = float(s.thickness)
        kind = "exterior" if thick >= (ext_thick * 0.75) else "interior"
        w = fp.add_wall(
            (float(s.p0[0]), float(s.p0[1])),
            (float(s.p1[0]), float(s.p1[1])),
            thickness=thick,
            kind=kind,
            source="reconstruction",
            confidence=0.9,
            weld_tol=vopt.weld_tol,
        )
        # 门窗：把世界坐标投影回最终墙
        a = np.array([float(s.p0[0]), float(s.p0[1])])
        b = np.array([float(s.p1[0]), float(s.p1[1])])
        d = unit(b - a)
        L = float(np.hypot(*(b - a)))
        for offset, width, kind_name in s.openings:
            center = s.p0 + d * offset
            proj = float((center - a) @ d)
            o = Opening(
                id=_oid(),
                wall=w.id,
                kind=kind_name,
                offset=max(width / 2 + 0.02, min(L - width / 2 - 0.02, proj)),
                width=float(width),
                height=(vopt.door_height if kind_name == "door" else vopt.window_height),
                sill=(0.0 if kind_name == "door" else vopt.window_sill),
            )
            if o.height + o.sill > fp.ceiling_height:
                o.height = max(0.4, fp.ceiling_height - o.sill)
            fp.openings[o.id] = o

    fp.weld(vopt.weld_tol)
    try:
        fp.merge_collinear()
    except Exception:  # noqa: BLE001 - 合并失败不应阻断重建
        pass

    diag["walls"] = len(fp.walls)
    diag["vertices"] = len(fp.vertices)
    diag["openings"] = len(fp.openings)

    # 6) 房间命名：按面积排序给通用名（语义无法从几何可靠推断）
    rooms = fp.rooms()
    names = ["主卧", "客厅", "次卧", "餐厅", "厨房", "卫生间", "书房", "阳台", "玄关", "储物间"]
    for i, room in enumerate(rooms):
        label = names[i] if i < len(names) else f"房间{i + 1}"
        fp.room_labels.append(RoomLabel(label, room.centroid, kind="room"))
    diag["rooms"] = len(rooms)
    return fp, diag


def _merge_horiz(group: list[WallSegment], tol: float) -> list[WallSegment]:
    group = sorted(group, key=lambda s: (s.p0[1], s.p0[0]))
    out: list[WallSegment] = []
    for s in group:
        merged = False
        for a in out:
            if abs(a.p0[1] - s.p0[1]) > tol:
                continue
            # 区间重叠或相邻 → 合并
            if not (s.p1[0] < a.p0[0] - 0.05 or s.p0[0] > a.p1[0] + 0.05):
                a.p0[0] = min(a.p0[0], s.p0[0])
                a.p1[0] = max(a.p1[0], s.p1[0])
                a.openings.extend(s.openings)
                a.thickness = max(a.thickness, s.thickness)
                merged = True
                break
        if not merged:
            out.append(s)
    return out


def _merge_vert(group: list[WallSegment], tol: float) -> list[WallSegment]:
    group = sorted(group, key=lambda s: (s.p0[0], s.p0[1]))
    out: list[WallSegment] = []
    for s in group:
        merged = False
        for a in out:
            if abs(a.p0[0] - s.p0[0]) > tol:
                continue
            if not (s.p1[1] < a.p0[1] - 0.05 or s.p0[1] > a.p1[1] + 0.05):
                a.p0[1] = min(a.p0[1], s.p0[1])
                a.p1[1] = max(a.p1[1], s.p1[1])
                a.openings.extend(s.openings)
                a.thickness = max(a.thickness, s.thickness)
                merged = True
                break
        if not merged:
            out.append(s)
    return out
