"""DXF 导出器（手写，零依赖）。

输出 **AutoCAD R12 (AC1009) ASCII DXF**，理由：
    * R12 是兼容性最好的 DXF 版本 —— AutoCAD 2000~2026、中望、浩辰、
      LibreCAD、BricsCAD、Rhino 都能读，而且读进来就是可编辑的线条。
    * 不用 LWPOLYLINE / 复杂对象，任何老软件都不会报错。
    * 中文用 GBK 编码 + $DWGCODEPAGE ANSI_936，与国内 CAD 环境一致。

输出内容按施工图习惯组织（AIA 图层命名）：
    A-WALL        墙体外轮廓（闭合多段线，可直接编辑）
    A-WALL-FILL   墙体填充（SOLID，深灰）
    A-DOOR        门：门套 + 门扇 + 开启弧
    A-GLAZ        窗：四线窗符号
    A-AREA-IDEN   房间名 + 面积
    A-DIMS        尺寸标注（自绘：尺寸线 + 箭头 + 文字，不用 DIMENSION 实体，
                  避免不同软件对标注样式解释不一致）
    A-AXIS        墙中线（虚线）
    A-OUTLINE     建筑外轮廓

单位：内部米 → 输出毫米（$INSUNITS=4）。可用 ``unit="m"`` 改为米。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..geom import XY, as_xy, length, perp, unit
from ..model import FloorPlan, Opening, Project
from ..wallbody import WallBody

ACADVER = "AC1009"
DWGCODEPAGE = "ANSI_936"

# ---- 图层定义：(名称, ACI 颜色, 线型)
LAYERS: list[tuple[str, int, str]] = [
    ("A-WALL", 7, "CONTINUOUS"),
    ("A-WALL-FILL", 8, "CONTINUOUS"),
    ("A-WALL-CENTER", 254, "DASHED"),
    ("A-DOOR", 30, "CONTINUOUS"),
    ("A-GLAZ", 140, "CONTINUOUS"),
    ("A-AREA-IDEN", 7, "CONTINUOUS"),
    ("A-ROOM-BNDY", 5, "DASHED"),
    ("A-DIMS", 2, "CONTINUOUS"),
    ("A-AXIS", 4, "DASHED"),
    ("A-OUTLINE", 1, "CONTINUOUS"),
    ("A-TEXT", 7, "CONTINUOUS"),
]


def _fmt(v: float) -> str:
    """DXF 浮点格式：避免科学计数法（AutoCAD 不认）。"""
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        return "0.0"
    if "." not in s:
        s += ".0"
    return s


@dataclass
class DxfOptions:
    unit: str = "mm"            # mm | m
    wall_fill: bool = True
    draw_dims: bool = True
    dim_mode: str = "overall"   # overall | rooms | walls | none
    draw_axes: bool = True
    draw_room_labels: bool = True
    text_height_m: float = 0.25
    layer_wall: str = "A-WALL"
    layer_door: str = "A-DOOR"
    layer_window: str = "A-GLAZ"
    layer_dims: str = "A-DIMS"
    layer_text: str = "A-AREA-IDEN"


# ================================================================ 底层写入


class DxfDocument:
    """极简 DXF R12 组装器。坐标一律用「输出单位」。"""

    def __init__(self, scale: float = 1000.0) -> None:
        self.scale = float(scale)
        self._ent: list[str] = []
        self._extra_layers: dict[str, tuple[int, str]] = {}
        self.extmin = [float("inf")] * 2
        self.extmax = [float("-inf")] * 2

    # ------------------------------------------------------------ 坐标

    def s(self, v: float) -> float:
        return float(v) * self.scale

    def p(self, xy: Sequence[float]) -> tuple[float, float]:
        return (self.s(xy[0]), self.s(xy[1]))

    def _track(self, x: float, y: float) -> None:
        self.extmin[0] = min(self.extmin[0], x)
        self.extmin[1] = min(self.extmin[1], y)
        self.extmax[0] = max(self.extmax[0], x)
        self.extmax[1] = max(self.extmax[1], y)

    # ------------------------------------------------------------ 原语

    def _w(self, code: int, value) -> None:
        self._ent.append(str(code))
        self._ent.append(str(value))

    def add_layer(self, name: str, color: int = 7, linetype: str = "CONTINUOUS") -> str:
        if name not in {l[0] for l in LAYERS}:
            self._extra_layers[name] = (color, linetype)
        return name

    def line(self, a: Sequence[float], b: Sequence[float], layer: str) -> None:
        ax, ay = self.p(a)
        bx, by = self.p(b)
        self._track(ax, ay)
        self._track(bx, by)
        self._w(0, "LINE")
        self._w(8, layer)
        self._w(10, _fmt(ax))
        self._w(20, _fmt(ay))
        self._w(30, "0.0")
        self._w(11, _fmt(bx))
        self._w(21, _fmt(by))
        self._w(31, "0.0")

    def polyline(
        self, pts: Sequence[Sequence[float]], layer: str, closed: bool = True
    ) -> None:
        if len(pts) < 2:
            return
        self._w(0, "POLYLINE")
        self._w(8, layer)
        self._w(66, 1)
        self._w(70, 1 if closed else 0)
        self._w(10, "0.0")
        self._w(20, "0.0")
        self._w(30, "0.0")
        for p in pts:
            x, y = self.p(p)
            self._track(x, y)
            self._w(0, "VERTEX")
            self._w(8, layer)
            self._w(10, _fmt(x))
            self._w(20, _fmt(y))
            self._w(30, "0.0")
        self._w(0, "SEQEND")
        self._w(8, layer)

    def circle(self, c: Sequence[float], r: float, layer: str) -> None:
        cx, cy = self.p(c)
        rr = self.s(r)
        self._track(cx - rr, cy - rr)
        self._track(cx + rr, cy + rr)
        self._w(0, "CIRCLE")
        self._w(8, layer)
        self._w(10, _fmt(cx))
        self._w(20, _fmt(cy))
        self._w(30, "0.0")
        self._w(40, _fmt(rr))

    def arc(
        self, c: Sequence[float], r: float, a0: float, a1: float, layer: str
    ) -> None:
        cx, cy = self.p(c)
        rr = self.s(r)
        self._track(cx - rr, cy - rr)
        self._track(cx + rr, cy + rr)
        self._w(0, "ARC")
        self._w(8, layer)
        self._w(10, _fmt(cx))
        self._w(20, _fmt(cy))
        self._w(30, "0.0")
        self._w(40, _fmt(rr))
        self._w(50, _fmt(a0 % 360.0))
        self._w(51, _fmt(a1 % 360.0))

    def text(
        self,
        xy: Sequence[float],
        height_out: float,
        value: str,
        layer: str,
        *,
        rotation: float = 0.0,
        center: bool = False,
    ) -> None:
        x, y = self.p(xy)
        # 文字包围盒粗略估计，用于 $EXTMIN/EXTMAX
        h = height_out
        w = h * 0.75 * len(value)
        if center:
            self._track(x - w / 2, y - h / 2)
            self._track(x + w / 2, y + h / 2)
        else:
            self._track(x, y)
        self._w(0, "TEXT")
        self._w(8, layer)
        self._w(10, _fmt(x))
        self._w(20, _fmt(y))
        self._w(30, "0.0")
        self._w(40, _fmt(h))
        self._w(1, value)
        if abs(rotation) > 1e-9:
            self._w(50, _fmt(rotation))
        if center:
            self._w(72, 1)  # center
            self._w(11, _fmt(x))
            self._w(21, _fmt(y))
            self._w(31, "0.0")
            self._w(73, 2)  # middle
        else:
            self._w(72, 0)
            self._w(73, 0)

    def solid(self, quad: Sequence[Sequence[float]], layer: str) -> None:
        """四点填充。注意 DXF SOLID 的顶点顺序是 Z 字形 (1,2,4,3)。"""
        if len(quad) == 3:
            quad = [quad[0], quad[1], quad[2], quad[2]]
        if len(quad) != 4:
            return
        a, b, c, d = quad
        for idx, p in enumerate((a, b, d, c)):
            x, y = self.p(p)
            self._track(x, y)
            code = 10 + idx
            self._w(0, "SOLID") if idx == 0 else None
            if idx == 0:
                self._w(8, layer)
            self._w(code, _fmt(x))
            self._w(code + 10, _fmt(y))
            self._w(code + 20, "0.0")

    # ------------------------------------------------------------ 箭头 + 标注

    def arrowhead(
        self, tip: Sequence[float], direction: Sequence[float], size_m: float, layer: str
    ) -> None:
        """实心三角形箭头，direction 指向箭头尖端方向。"""
        d = unit(as_xy(direction))
        n = perp(d)
        tipv = as_xy(tip)
        base = tipv - d * size_m
        left = base + n * (size_m * 0.18)
        right = base - n * (size_m * 0.18)
        self.solid(
            [
                (tipv[0], tipv[1]),
                (left[0], left[1]),
                (right[0], right[1]),
                (right[0], right[1]),
            ],
            layer,
        )

    # ------------------------------------------------------------ 输出

    def build(self, encoding: str = "gbk") -> str:
        if self.extmin[0] == float("inf"):
            self.extmin = [0.0, 0.0]
            self.extmax = [1.0, 1.0]
        out: list[str] = []

        def sec_head(name: str) -> None:
            out.extend(["0", "SECTION", "2", name])

        # ---------------- HEADER
        sec_head("HEADER")
        header_vars = [
            ("$ACADVER", 1, ACADVER),
            ("$DWGCODEPAGE", 3, DWGCODEPAGE),
            ("$INSBASE", 10, None),
            ("$EXTMIN", 10, None),
            ("$EXTMAX", 10, None),
            ("$LIMMIN", 10, None),
            ("$LIMMAX", 10, None),
            ("$LTSCALE", 40, "1.0"),
            ("$TEXTSTYLE", 7, "STANDARD"),
            ("$CLAYER", 8, "A-WALL"),
            ("$TEXTSIZE", 40, _fmt(self.scale * 0.25)),
            ("$INSUNITS", 70, 4 if self.scale == 1000.0 else 6),
        ]
        pad = max(0.0, self.scale * 1.0)
        boxes = {
            "$INSBASE": (0.0, 0.0),
            "$EXTMIN": (self.extmin[0] - pad, self.extmin[1] - pad),
            "$EXTMAX": (self.extmax[0] + pad, self.extmax[1] + pad),
            "$LIMMIN": (self.extmin[0] - pad, self.extmin[1] - pad),
            "$LIMMAX": (self.extmax[0] + pad, self.extmax[1] + pad),
        }
        for name, code, value in header_vars:
            out.extend(["9", name])
            if value is None:
                x, y = boxes[name]
                out.extend(["10", _fmt(x), "20", _fmt(y), "30", "0.0"])
            else:
                out.extend([str(code), str(value)])
        out.extend(["0", "ENDSEC"])

        # ---------------- TABLES
        sec_head("TABLES")
        # LTYPE
        out.extend(["0", "TABLE", "2", "LTYPE", "70", "2"])
        out.extend(
            ["0", "LTYPE", "2", "CONTINUOUS", "70", "0", "3", "Solid line", "72", "65", "73", "0", "40", "0.0"]
        )
        out.extend(
            [
                "0", "LTYPE", "2", "DASHED", "70", "0", "3", "__ __ __", "72", "65",
                "73", "2", "40", _fmt(self.scale * 0.25),
                "49", _fmt(self.scale * 0.15), "74", "0",
                "49", _fmt(-self.scale * 0.10), "74", "0",
            ]
        )
        out.extend(["0", "ENDTAB"])

        # LAYER
        all_layers = list(LAYERS) + [
            (n, c, lt) for n, (c, lt) in self._extra_layers.items()
        ]
        out.extend(["0", "TABLE", "2", "LAYER", "70", str(len(all_layers))])
        for name, color, lt in all_layers:
            out.extend(
                ["0", "LAYER", "2", name, "70", "0", "62", str(color), "6", lt]
            )
        out.extend(["0", "ENDTAB"])

        # STYLE
        out.extend(["0", "TABLE", "2", "STYLE", "70", "1"])
        out.extend(
            [
                "0", "STYLE", "2", "STANDARD", "70", "0", "40", "0.0", "41", "1.0",
                "50", "0.0", "71", "0", "42", _fmt(self.scale * 0.25), "3", "txt", "4", "",
            ]
        )
        out.extend(["0", "ENDTAB"])

        # APPID（R12 下非必需，但补上更稳）
        out.extend(["0", "TABLE", "2", "APPID", "70", "1"])
        out.extend(["0", "APPID", "2", "ACAD", "70", "0"])
        out.extend(["0", "ENDTAB"])

        # VPORT（提供一个默认视口）
        out.extend(["0", "TABLE", "2", "VPORT", "70", "1"])
        out.extend(
            [
                "0", "VPORT", "2", "*ACTIVE", "70", "0",
                "10", "0.0", "20", "0.0",
                "11", "1.0", "21", "1.0",
                "12", _fmt((self.extmin[0] + self.extmax[0]) / 2),
                "22", _fmt((self.extmin[1] + self.extmax[1]) / 2),
                "13", "0.0", "23", "0.0", "14", "10.0", "24", "10.0",
                "15", "10.0", "25", "10.0", "16", "0.0", "26", "0.0",
                "17", "0.0", "27", "0.0",
                "40", _fmt(max(self.extmax[1] - self.extmin[1], 1.0)),
                "41", "1.5", "42", "50.0", "43", "0.0", "44", "0.0",
                "50", "0.0", "51", "0.0", "71", "0", "72", "100", "73", "1",
                "74", "3", "75", "0", "76", "0", "77", "0", "78", "0",
            ]
        )
        out.extend(["0", "ENDTAB"])

        out.extend(["0", "ENDSEC"])

        # ---------------- BLOCKS（空）
        sec_head("BLOCKS")
        out.extend(["0", "ENDSEC"])

        # ---------------- ENTITIES
        sec_head("ENTITIES")
        out.extend(self._ent)
        out.extend(["0", "ENDSEC"])

        out.extend(["0", "EOF"])

        # DXF 规范要求 CRLF 行尾，AutoCAD 对纯 LF 容忍度不稳，统一用 CRLF
        text = "\r\n".join(out) + "\r\n"
        return text

    def write(self, path: str | Path, encoding: str = "gbk") -> int:
        text = self.build()
        data = text.encode(encoding, errors="replace")
        Path(path).write_bytes(data)
        return len(data)


# ================================================================ 户型 → DXF


def _wall_fill_quad(body: WallBody) -> list[XY]:
    return body.polygon()


def _door_symbol(
    doc: DxfDocument,
    body: WallBody,
    op: Opening,
    layer: str,
    text_h: float,
) -> None:
    """门：两端门套线 + 门扇 + 90° 开启弧。"""
    L = body.length
    s0, s1 = body.opening_frame(op.offset, op.width)
    q_end0 = (body.left_at(s0), body.right_at(s0))
    q_end1 = (body.left_at(s1), body.right_at(s1))
    # 门套（洞口两侧横线）
    doc.line(q_end0[0], q_end0[1], layer)
    doc.line(q_end1[0], q_end1[1], layer)

    w = float(op.width)
    direction = body.direction
    n = body.normal
    # 铰链在洞口一端，门扇沿法向伸出，弧从门扇末端扫到另一端
    hinge_s, other_s = (s0, s1) if op.swing != "right" else (s1, s0)
    hinge = body.center_at(hinge_s)
    side = 1.0 if op.swing_in else -1.0
    leaf_dir = n * side
    leaf_end = hinge + leaf_dir * w
    doc.line(hinge, leaf_end, layer)
    # 开启弧：从门扇末端的角度扫到墙方向
    a_leaf = math.degrees(math.atan2(leaf_dir[1], leaf_dir[0]))
    along = unit(body.center_at(other_s) - hinge)
    a_wall = math.degrees(math.atan2(along[1], along[0]))
    # 保证扫过 90°
    delta = (a_wall - a_leaf) % 360.0
    if delta > 180.0:
        a0, a1 = a_wall, a_leaf
    else:
        a0, a1 = a_leaf, a_wall
    doc.arc(hinge, w, a0, a1, layer)


def _window_symbol(doc: DxfDocument, body: WallBody, op: Opening, layer: str) -> None:
    """窗：洞口两端封口 + 沿墙厚的两根玻璃线。"""
    s0, s1 = body.opening_frame(op.offset, op.width)
    # 两端封口
    doc.line(body.left_at(s0), body.right_at(s0), layer)
    doc.line(body.left_at(s1), body.right_at(s1), layer)
    # 窗框：左右边线
    doc.line(body.left_at(s0), body.left_at(s1), layer)
    doc.line(body.right_at(s0), body.right_at(s1), layer)
    # 玻璃线（沿墙厚 1/3、2/3 处）
    for frac in (1.0 / 3.0, 2.0 / 3.0):
        a = body.right_at(s0) + (body.left_at(s0) - body.right_at(s0)) * frac
        b = body.right_at(s1) + (body.left_at(s1) - body.right_at(s1)) * frac
        doc.line(a, b, layer)


def _dim_aligned(
    doc: DxfDocument,
    p1: Sequence[float],
    p2: Sequence[float],
    offset: float,
    layer: str,
    text_h_out: float,
    text_h_m: float,
    value_text: str | None = None,
) -> None:
    """自绘对齐尺寸标注：延伸线 + 尺寸线 + 双箭头 + 文字。

    注意：p1/p2/offset 都是**模型坐标（米）**，doc 内部再乘比例；
    text_h_out 已经是输出单位，text_h_m 用来算文字相对尺寸线的偏移。
    """
    a = as_xy(p1)
    b = as_xy(p2)
    d = unit(b - a)
    n = perp(d)
    L = float(length(b - a))
    if L < 1e-6:
        return
    a2 = a + n * offset
    b2 = b + n * offset
    # 延伸线（略微超出尺寸线）
    ext = abs(offset) * 0.12 if abs(offset) > 1e-9 else 0.05
    doc.line(a, a2 + n * (ext if offset >= 0 else -ext), layer)
    doc.line(b, b2 + n * (ext if offset >= 0 else -ext), layer)
    # 尺寸线
    doc.line(a2, b2, layer)
    # 箭头（尺寸单位：米）
    doc.arrowhead(a2, -d, 0.08, layer)
    doc.arrowhead(b2, d, 0.08, layer)
    # 文字
    txt = value_text if value_text is not None else f"{L:.0f}"
    mid = (a2 + b2) * 0.5
    ang = math.degrees(math.atan2(d[1], d[0]))
    if ang > 90.0 or ang < -90.0:
        ang += 180.0
    doc.text(
        mid + n * (text_h_m * 0.9), text_h_out, txt, layer, rotation=ang, center=False
    )


def export_floorplan_dxf(
    plan: FloorPlan | Project,
    path: str | Path,
    options: DxfOptions | None = None,
) -> dict:
    """把一层户型导出为 DXF 文件，返回统计信息。"""
    opt = options or DxfOptions()
    if isinstance(plan, Project):
        fp = plan.active
    else:
        fp = plan

    scale = 1000.0 if opt.unit == "mm" else 1.0
    doc = DxfDocument(scale=scale)
    text_h = doc.s(opt.text_height_m)
    bodies = fp.wall_body_map()

    # ---------------- 墙体
    for body in fp.wall_bodies():
        poly = _wall_fill_quad(body)
        if len(poly) < 3:
            continue
        doc.polyline(poly, opt.layer_wall, closed=True)
        if opt.wall_fill:
            # SOLID 支持四点，斜接后的墙正好是四边形
            if len(poly) == 4:
                doc.solid(poly, "A-WALL-FILL")

    # ---------------- 门窗
    for op in fp.openings.values():
        body = bodies.get(op.wall)
        if body is None:
            continue
        if op.kind == "door":
            _door_symbol(doc, body, op, opt.layer_door, text_h)
        elif op.kind in ("window", "bay"):
            _window_symbol(doc, body, op, opt.layer_window)
        elif op.kind == "passage":
            s0, s1 = body.opening_frame(op.offset, op.width)
            doc.line(body.left_at(s0), body.right_at(s0), opt.layer_door)
            doc.line(body.left_at(s1), body.right_at(s1), opt.layer_door)
            for f in (1.0 / 3.0, 2.0 / 3.0):
                a = body.right_at(s0) + (body.left_at(s0) - body.right_at(s0)) * f
                b = body.right_at(s1) + (body.left_at(s1) - body.right_at(s1)) * f
                doc.line(a, b, opt.layer_door + "-GLASS")

    # ---------------- 墙中线（虚线）
    if opt.draw_axes:
        for w in fp.walls.values():
            a, b = fp.wall_endpoints(w.id)
            doc.line((float(a[0]), float(a[1])), (float(b[0]), float(b[1])), "A-WALL-CENTER")

    # ---------------- 建筑外轮廓
    outline = fp.outline()
    if len(outline) >= 3:
        doc.polyline(outline, "A-OUTLINE", closed=True)

    # ---------------- 房间名 + 面积
    rooms = fp.rooms()
    for room in rooms:
        if not opt.draw_room_labels:
            continue
        cx, cy = room.centroid
        doc.text(
            (cx, cy + opt.text_height_m * 0.9),
            text_h,
            room.label,
            opt.layer_text,
            center=True,
        )
        doc.text(
            (cx, cy - opt.text_height_m * 0.9),
            text_h * 0.8,
            f"{room.net_area:.2f} m2",
            opt.layer_text,
            center=True,
        )
        # 房间净轮廓（细虚线，便于编辑核对）
        doc.polyline(room.polygon, "A-ROOM-BNDY", closed=True)

    # ---------------- 标注
    n_dims = 0
    if opt.draw_dims and opt.dim_mode != "none":
        if opt.dim_mode in ("overall", "rooms"):
            # 每个房间的水平/竖向净尺寸
            for room in rooms:
                xs = [p[0] for p in room.polygon]
                ys = [p[1] for p in room.polygon]
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                _dim_aligned(
                    doc,
                    (x0, y0),
                    (x1, y0),
                    -0.9,
                    opt.layer_dims,
                    text_h * 0.8,
                    opt.text_height_m,
                )
                _dim_aligned(
                    doc,
                    (x0, y1),
                    (x0, y0),
                    -0.9,
                    opt.layer_dims,
                    text_h * 0.8,
                    opt.text_height_m,
                )
                n_dims += 2
        if opt.dim_mode in ("overall", "walls"):
            for w in fp.walls.values():
                a, b = fp.wall_endpoints(w.id)
                if abs(b[1] - a[1]) < 1e-6 or abs(b[0] - a[0]) < 1e-6:
                    # 只给正交墙标注，避免斜墙标注过于杂乱
                    off = 0.35 * (w.thickness / 0.2 + 0.5)
                    _dim_aligned(
                        doc,
                        (float(a[0]), float(a[1])),
                        (float(b[0]), float(b[1])),
                        off,
                        opt.layer_dims,
                        text_h * 0.8,
                        opt.text_height_m,
                    )
                    n_dims += 1

    size = doc.write(path)
    return {
        "path": str(path),
        "bytes": size,
        "scale": scale,
        "units": opt.unit,
        "layers": len(LAYERS) + len(doc._extra_layers),
        "entities": len(doc._ent) // 2,
        "walls": len(bodies),
        "openings": len(fp.openings),
        "rooms": len(rooms),
        "dimensions": n_dims,
        "extents": [
            [round(doc.extmin[0], 3), round(doc.extmin[1], 3)],
            [round(doc.extmax[0], 3), round(doc.extmax[1], 3)],
        ],
    }


# ================================================================ 结构校验


def validate_dxf(path: str | Path, encoding: str = "gbk") -> dict:
    """不依赖任何第三方库，检查 DXF 结构是否自洽。

    校验项：
        * 成对出现（奇数行是 group code）
        * SECTION / ENDSEC 配对
        * HEADER/TABLES/ENTITIES 都存在
        * 以 EOF 结束
        * 实体类型统计
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        text = raw.decode(encoding, errors="replace")
    lines = text.splitlines()
    problems: list[str] = []

    if len(lines) % 2 != 0:
        problems.append(f"行数为奇数（{len(lines)}），最后一行孤立")
    codes: list[int] = []
    values: list[str] = []
    for i in range(0, len(lines) - 1, 2):
        try:
            codes.append(int(lines[i].strip()))
        except ValueError:
            problems.append(f"第 {i + 1} 行不是 group code: {lines[i]!r}")
            codes.append(-1)
        values.append(lines[i + 1])

    pairs = list(zip(codes, values))
    depth = 0
    sections: list[str] = []
    i = 0
    entities: dict[str, int] = {}
    while i < len(pairs):
        code, val = pairs[i]
        if code == 0 and val == "SECTION":
            depth += 1
            if i + 1 < len(pairs) and pairs[i + 1][0] == 2:
                sections.append(pairs[i + 1][1])
                i += 2
                continue
        elif code == 0 and val == "ENDSEC":
            depth -= 1
            if depth < 0:
                problems.append("ENDSEC 多于 SECTION")
        elif code == 0:
            entities[val] = entities.get(val, 0) + 1
        i += 1
    if depth != 0:
        problems.append(f"SECTION/ENDSEC 不配对（depth={depth}）")
    for need in ("HEADER", "TABLES", "ENTITIES"):
        if need not in sections:
            problems.append(f"缺少 {need} 段")
    if not pairs or pairs[-1] != (0, "EOF"):
        problems.append("文件未以 EOF 结束")

    return {
        "ok": not problems,
        "problems": problems,
        "sections": sections,
        "entity_count": sum(entities.values()),
        "entity_types": dict(sorted(entities.items(), key=lambda kv: -kv[1])),
        "bytes": len(raw),
        "lines": len(lines),
    }
