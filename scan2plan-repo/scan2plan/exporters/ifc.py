"""IFC4 导出器（手写 STEP 物理文件，零依赖）。

为什么值得手写：
    IFC 是 BIM 的通用交换格式 —— Revit、Archicad、Bentley、Tekla、Navisworks、
    Solibri 都能直接打开。户型图导成 IFC 之后，客户/施工方可以拿去做
    算量、碰撞检查、施工模拟，价值远高于一个裸网格。

本模块输出符合 ISO-10303-21 的 **IFC4** 文件，包含：
    IfcSite / IfcBuilding / IfcBuildingStorey 三层空间结构
    IfcWall（含 PredefinedType）——每面墙一个构件，可单独选中、改属性
    IfcSlab(.FLOOR.) —— 楼板
    IfcOpeningElement + IfcRelVoidsElement —— 真实门窗洞口（布尔洞口）
    IfcDoor / IfcWindow + IfcRelFillsElement —— 门窗构件填充洞口
    IfcSpace —— 房间空间体，带 Qto_SpaceBaseQuantities 净面积
    IfcRelAggregates / IfcRelContainedInSpatialStructure —— 标准层级关系

几何：一律用 ``IfcExtrudedAreaSolid`` + ``IfcArbitraryClosedProfileDef``，
这是 BIM 软件解析最稳、最不容易丢面的表示方式。
单位：METRE（模型本来就是米，不做换算）。
"""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Sequence

from ..geom import XY
from ..model import FloorPlan, Project, Room
from ..wallbody import WallBody

IFC_SCHEMA = "IFC4"
GUID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


# ================================================================ 工具


def compress_guid(raw: bytes | None = None) -> str:
    """128 bit UUID → IFC 的 22 字符 base64 变体。

    分组：(3,3,3,3,4) 字节 → (4,4,4,4,6) 字符，共 22 字符，
    与 buildingSMART 的 IfcGloballyUniqueId 编码一致。
    """
    b = raw or uuid.uuid4().bytes
    if len(b) != 16:
        raise ValueError("guid needs 16 bytes")
    out: list[str] = []
    for i, size in enumerate((3, 3, 3, 3, 4)):
        chunk = int.from_bytes(b[i * 3 : i * 3 + size], "big")
        nchars = 4 if size == 3 else 6
        s = ""
        v = chunk
        for _ in range(nchars):
            s = GUID_CHARS[v & 63] + s
            v >>= 6
        out.append(s)
    return "".join(out)


def _s(text: str) -> str:
    """STEP 字符串字面量。"""
    if text is None:
        return "$"
    esc = str(text).replace("\\", "\\\\").replace("'", "''")
    return f"'{esc}'"


def _f(v: float) -> str:
    """STEP 实数：必须是 float 形式且不用科学计数法。"""
    if v is None:
        return "$"
    if abs(v) < 1e-12:
        return "0."
    s = f"{v:.6f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    if "." not in s:
        s += "."
    return s


class Step:
    """极简 STEP 组装器，自动管理 #id。"""

    def __init__(self) -> None:
        self.rows: list[str] = []
        self._n = 0

    def add(self, body: str) -> int:
        self._n += 1
        self.rows.append(f"#{self._n}= {body};")
        return self._n

    def __len__(self) -> int:
        return self._n


def _pt(st: Step, xy: Sequence[float], z: float = 0.0) -> int:
    return st.add(
        f"IFCCARTESIANPOINT(({_f(float(xy[0]))},{_f(float(xy[1]))},{_f(z)}))"
    )


def _pt2(st: Step, xy: Sequence[float]) -> int:
    return st.add(f"IFCCARTESIANPOINT(({_f(float(xy[0]))},{_f(float(xy[1]))}))")


def _dir(st: Step, v: Sequence[float]) -> int:
    return st.add(
        f"IFCDIRECTION(({_f(float(v[0]))},{_f(float(v[1]))},{_f(float(v[2]))}))"
    )


def _axis2(st: Step, origin_xy: Sequence[float], z: float = 0.0) -> int:
    loc = _pt(st, origin_xy, z)
    return st.add(f"IFCAXIS2PLACEMENT3D(#{loc},$,$)")


def _local_placement(st: Step, rel_to: int | None, origin_xy=(0.0, 0.0), z: float = 0.0) -> int:
    axis = _axis2(st, origin_xy, z)
    rel = f"#{rel_to}" if rel_to else "$"
    return st.add(f"IFCLOCALPLACEMENT({rel},#{axis})")


def _profile_polyline(st: Step, ring: Sequence[Sequence[float]]) -> int:
    """闭合轮廓：首点重复一次（IFC 约定 OuterCurve 必须闭合）。"""
    pts = [_pt2(st, p) for p in ring]
    pts.append(pts[0])
    poly = st.add("IFCPOLYLINE((" + ",".join(f"#{p}" for p in pts) + "))")
    return st.add(f"IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#{poly})")


def _extruded_solid(
    st: Step,
    ring: Sequence[Sequence[float]],
    z0: float,
    depth: float,
) -> int:
    profile = _profile_polyline(st, ring)
    pos = _axis2(st, (0.0, 0.0), z0)
    dirv = _dir(st, (0.0, 0.0, 1.0))
    return st.add(
        f"IFCEXTRUDEDAREASOLID(#{profile},#{pos},#{dirv},{_f(float(depth))})"
    )


def _shape_rep(st: Step, sub_ctx: int, items: Sequence[int]) -> int:
    item_ref = "(" + ",".join(f"#{i}" for i in items) + ")"
    rep = st.add(
        f"IFCSHAPEREPRESENTATION(#{sub_ctx},{_s('Body')},{_s('SweptSolid')},{item_ref})"
    )
    return rep


def _product_shape(st: Step, rep: int) -> int:
    return st.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{rep}))")


# ================================================================ 主流程


def export_ifc(
    plan: FloorPlan | Project,
    path: str | Path,
    *,
    name: str | None = None,
    author: str = "scan2plan",
) -> dict:
    """导出 IFC4 文件，返回统计信息。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(plan, Project):
        project = plan
        fp = project.active
    else:
        fp = plan
        project = Project(name=fp.name, plans=[fp])

    st = Step()
    title = name or project.name or fp.name or "scan2plan project"

    # ---------------- 基础：应用 / 单位 / 上下文
    org = st.add(f"IFCORGANIZATION($,{_s(author)},$,$,$)")
    app = st.add(
        f"IFCAPPLICATION(#{org},{_s('0.1.0')},{_s('scan2plan')},{_s(author)})"
    )

    units: list[int] = []
    for utype, uname in (
        (".LENGTHUNIT.", ".METRE."),
        (".AREAUNIT.", ".SQUARE_METRE."),
        (".VOLUMEUNIT.", ".CUBIC_METRE."),
    ):
        units.append(st.add(f"IFCSIUNIT(*,{utype},$,{uname})"))
    units.append(st.add("IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.)"))
    unit_assign = st.add("IFCUNITASSIGNMENT((" + ",".join(f"#{u}" for u in units) + "))")

    origin_pt = _pt(st, (0.0, 0.0), 0.0)
    wcs = st.add(f"IFCAXIS2PLACEMENT3D(#{origin_pt},$,$)")
    north = _dir(st, (0.0, 1.0, 0.0))
    ctx = st.add(
        f"IFCGEOMETRICREPRESENTATIONCONTEXT($,{_s('Model')},3,1.E-05,#{wcs},#{north})"
    )
    sub_ctx = st.add(
        f"IFCGEOMETRICREPRESENTATIONSUBCONTEXT({_s('Body')},{_s('Model')},*,*,*,*,"
        f"#{ctx},$,{_s('Model View')},$)"
    )

    # ---------------- 空间结构
    proj = st.add(
        f"IFCPROJECT({_s(compress_guid())},$,{_s(title)},$,$,{_s(fp.name)},$,"
        f"(#{ctx}),#{unit_assign})"
    )
    site_pl = _local_placement(st, None)
    site = st.add(
        f"IFCSITE({_s(compress_guid())},$,{_s('Site')},$,$,#{site_pl},$,$,.ELEMENT.,"
        f"$,$,$,$,$)"
    )
    bldg_pl = _local_placement(st, site_pl, z=fp.elevation)
    bldg = st.add(
        f"IFCBUILDING({_s(compress_guid())},$,{_s('Building')},$,$,#{bldg_pl},$,$,"
        f".ELEMENT.,$,$,$)"
    )
    storey_pl = _local_placement(st, bldg_pl, z=fp.elevation)
    storey = st.add(
        f"IFCBUILDINGSTOREY({_s(compress_guid())},$,{_s(fp.name)},$,$,#{storey_pl},$,$,"
        f".ELEMENT.,{_f(fp.elevation)})"
    )
    st.add(
        f"IFCRELAGGREGATES({_s(compress_guid())},$,$,$,#{proj},(#{site}))"
    )
    st.add(f"IFCRELAGGREGATES({_s(compress_guid())},$,$,$,#{site},(#{bldg}))")
    st.add(f"IFCRELAGGREGATES({_s(compress_guid())},$,$,$,#{bldg},(#{storey}))")

    contained: list[int] = []
    bodies = fp.wall_body_map()
    stats = {
        "walls": 0,
        "slabs": 0,
        "doors": 0,
        "windows": 0,
        "openings": 0,
        "spaces": 0,
    }

    # ---------------- 墙体
    opening_by_wall: dict[str, list] = {}
    for op in fp.openings.values():
        opening_by_wall.setdefault(op.wall, []).append(op)
    opening_elem: dict[str, int] = {}

    for wall in fp.walls.values():
        body = bodies.get(wall.id)
        if body is None:
            continue
        poly = body.polygon()
        if len(poly) < 3:
            continue
        solid = _extruded_solid(st, poly, 0.0, wall.height)
        rep = _shape_rep(st, sub_ctx, [solid])
        shape = _product_shape(st, rep)
        pl = _local_placement(st, storey_pl)
        predefined = ".PARTITIONING." if wall.kind != "exterior" else ".SOLIDWALL."
        w = st.add(
            f"IFCWALL({_s(compress_guid())},$,{_s('墙体-' + wall.id)},{_s(wall.kind)},$,"
            f"#{pl},#{shape},{_s(wall.id)},{predefined})"
        )
        contained.append(w)
        stats["walls"] += 1

        # ---- 门窗洞口（真实布尔洞口）
        for op in sorted(opening_by_wall.get(wall.id, []), key=lambda o: o.offset):
            s0, s1 = body.opening_frame(op.offset, op.width)
            quad = body.sub(s0, s1)
            if len(quad) < 3:
                continue
            z0 = float(op.sill)
            depth = float(min(op.height, wall.height - z0))
            if depth <= 1e-4:
                continue
            void_solid = _extruded_solid(st, quad, z0, depth)
            void_rep = _shape_rep(st, sub_ctx, [void_solid])
            void_shape = _product_shape(st, void_rep)
            void_pl = _local_placement(st, storey_pl)
            opening = st.add(
                f"IFCOPENINGELEMENT({_s(compress_guid())},$,{_s('洞口-' + op.id)},$,$,"
                f"#{void_pl},#{void_shape},$,.OPENING.)"
            )
            contained.append(opening)
            opening_elem[op.id] = opening
            st.add(
                f"IFCRELVOIDSELEMENT({_s(compress_guid())},$,$,$,#{w},#{opening})"
            )
            stats["openings"] += 1

            # ---- 填充构件：门 / 窗（属性个数 13 个，尾部操作类型留空）
            fill_pl = _local_placement(st, storey_pl)
            if op.kind == "passage":
                continue
            panel_rep = _shape_rep(
                st, sub_ctx, [_extruded_solid(st, quad, z0, depth)]
            )
            panel_shape = _product_shape(st, panel_rep)
            label = ("门-" if op.kind == "door" else "窗-") + op.id
            if op.kind == "door":
                panel = st.add(
                    f"IFCDOOR({_s(compress_guid())},$,{_s(label)},$,$,#{fill_pl},"
                    f"#{panel_shape},$,{_f(float(op.height))},{_f(float(op.width))},"
                    f".NOTDEFINED.,$,$)"
                )
            else:
                panel = st.add(
                    f"IFCWINDOW({_s(compress_guid())},$,{_s(label)},$,$,#{fill_pl},"
                    f"#{panel_shape},$,{_f(float(op.height))},{_f(float(op.width))},"
                    f".NOTDEFINED.,$,$)"
                )
            contained.append(panel)
            st.add(
                f"IFCRELFILLSELEMENT({_s(compress_guid())},$,$,$,#{opening},#{panel})"
            )
            if op.kind == "door":
                stats["doors"] += 1
            else:
                stats["windows"] += 1

    # ---------------- 楼板 + 房间空间
    rooms = fp.rooms()
    if rooms:
        # 楼板：用房间净多边形各自一块（避免布尔并集，BIM 里也常见）
        for room in rooms:
            slab_solid = _extruded_solid(
                st, room.polygon, -float(fp.slab_thickness), float(fp.slab_thickness)
            )
            slab = st.add(
                f"IFCSLAB({_s(compress_guid())},$,{_s('楼板-' + room.label)},$,$,"
                f"#{_local_placement(st, storey_pl)},"
                f"#{_product_shape(st, _shape_rep(st, sub_ctx, [slab_solid]))},$,.FLOOR.)"
            )
            contained.append(slab)
            stats["slabs"] += 1

        for room in rooms:
            space_solid = _extruded_solid(
                st, room.polygon, 0.0, float(fp.ceiling_height)
            )
            space_shape = _product_shape(
                st, _shape_rep(st, sub_ctx, [space_solid])
            )
            qty = st.add(
                f"IFCQUANTITYAREA({_s('NetFloorArea')},$,"
                f"#{st.add('IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.)')},"
                f"{_f(room.net_area)},$)"
            )
            equantity = st.add(
                f"IFCELEMENTQUANTITY({_s(compress_guid())},$,{_s('Qto_SpaceBaseQuantities')},$,"
                f"$,(#{qty}))"
            )
            space = st.add(
                f"IFCSPACE({_s(compress_guid())},$,{_s('房间-' + room.label)},$,$,"
                f"#{_local_placement(st, storey_pl)},#{space_shape},"
                f"{_s(room.label)},.ELEMENT.,.INTERNAL.,{_f(0.0)})"
            )
            contained.append(space)
            st.add(
                f"IFCRELDEFINESBYPROPERTIES({_s(compress_guid())},$,$,$,(#{space}),#{equantity})"
            )
            stats["spaces"] += 1

    # ---------------- 容纳关系
    if contained:
        refs = "(" + ",".join(f"#{c}" for c in contained) + ")"
        st.add(
            f"IFCRELCONTAINEDINSPATIALSTRUCTURE({_s(compress_guid())},$,$,$,"
            f"{refs},#{storey})"
        )

    # ---------------- 组装文件
    header = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView_V2.0]'),'2;1');",
        (
            "FILE_NAME("
            + _s(title + ".ifc")
            + ",'2024-01-01T00:00:00',("
            + _s(author)
            + "),("
            + _s(author)
            + "),"
            + "'scan2plan 0.1','scan2plan 0.1','');"
        ),
        f"FILE_SCHEMA(('{IFC_SCHEMA}'));",
        "ENDSEC;",
        "",
        "DATA;",
    ]
    footer = ["ENDSEC;", "", "END-ISO-10303-21;", ""]
    text = "\n".join(header + st.rows + footer)
    path.write_text(text, encoding="utf-8", newline="\n")

    return {
        "format": "ifc",
        "schema": IFC_SCHEMA,
        "path": str(path),
        "bytes": path.stat().st_size,
        "entities": len(st),
        "walls": stats["walls"],
        "slabs": stats["slabs"],
        "openings": stats["openings"],
        "doors": stats["doors"],
        "windows": stats["windows"],
        "spaces": stats["spaces"],
        "units": "METRE",
        "storey_elevation": fp.elevation,
    }


# IFC4 各实体类型的**必填属性总个数**（含继承属性）。
# 手写 STEP 最常见的错误就是属性个数不对（少写一个 $），而严格的 IFC 读入器
# （Revit / ifcopenshell）会直接拒收，所以这里逐条核对。
IFC4_ATTR_COUNTS: dict[str, int] = {
    "IFCPROJECT": 9,
    "IFCSITE": 14,
    "IFCBUILDING": 12,
    "IFCBUILDINGSTOREY": 10,
    "IFCRELAGGREGATES": 6,
    "IFCRELCONTAINEDINSPATIALSTRUCTURE": 6,
    "IFCRELVOIDSELEMENT": 6,
    "IFCRELFILLSELEMENT": 6,
    "IFCRELDEFINESBYPROPERTIES": 6,
    "IFCWALL": 9,
    "IFCSLAB": 9,
    "IFCSPACE": 11,
    "IFCOPENINGELEMENT": 9,
    "IFCDOOR": 13,
    "IFCWINDOW": 13,
    "IFCORGANIZATION": 5,
    "IFCAPPLICATION": 4,
    "IFCSIUNIT": 4,
    "IFCUNITASSIGNMENT": 1,
    "IFCGEOMETRICREPRESENTATIONCONTEXT": 6,
    "IFCGEOMETRICREPRESENTATIONSUBCONTEXT": 10,
    "IFCSHAPEREPRESENTATION": 4,
    "IFCPRODUCTDEFINITIONSHAPE": 3,
    "IFCEXTRUDEDAREASOLID": 4,
    "IFCARBITRARYCLOSEDPROFILEDEF": 3,
    "IFCPOLYLINE": 1,
    "IFCCARTESIANPOINT": 1,
    "IFCDIRECTION": 1,
    "IFCAXIS2PLACEMENT3D": 3,
    "IFCLOCALPLACEMENT": 2,
    "IFCELEMENTQUANTITY": 6,
    "IFCQUANTITYAREA": 5,
}


def _split_step_args(body: str) -> list[str]:
    """按顶层逗号切分 STEP 参数列表（尊重括号与字符串）。"""
    if not body.strip():
        return []
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    in_str = False
    i = 0
    while i < len(body):
        ch = body[i]
        if in_str:
            cur.append(ch)
            if ch == "'":
                if i + 1 < len(body) and body[i + 1] == "'":
                    cur.append("'")
                    i += 1
                else:
                    in_str = False
        else:
            if ch == "'":
                in_str = True
                cur.append(ch)
            elif ch == "(":
                depth += 1
                cur.append(ch)
            elif ch == ")":
                depth -= 1
                cur.append(ch)
            elif ch == "," and depth == 0:
                out.append("".join(cur).strip())
                cur = []
            else:
                cur.append(ch)
        i += 1
    out.append("".join(cur).strip())
    return out


def validate_ifc(path: str | Path) -> dict:
    """结构校验：STEP 头尾、实体引用完整性、GUID 长度、**属性个数**。"""
    import re

    raw = Path(path).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    problems: list[str] = []
    if not text.startswith("ISO-10303-21;"):
        problems.append("缺少 ISO-10303-21 头")
    if "END-ISO-10303-21;" not in text:
        problems.append("缺少 END-ISO-10303-21 尾")
    if "FILE_SCHEMA(('IFC4'));" not in text:
        problems.append("FILE_SCHEMA 不是 IFC4")
    if "DATA;" not in text or "ENDSEC;" not in text:
        problems.append("缺少 DATA 段")

    defined: dict[int, str] = {}
    bodies: dict[int, str] = {}
    for m in re.finditer(r"^#(\d+)= ([A-Z0-9_]+)\((.*)\);[ \t\r]*$", text, re.MULTILINE):
        eid = int(m.group(1))
        defined[eid] = m.group(2)
        bodies[eid] = m.group(3)
    if not defined:
        problems.append("没有任何实体")

    refs = set(int(x) for x in re.findall(r"#(\d+)", text))
    missing = sorted(r for r in refs if r not in defined and r != 0)
    if missing:
        problems.append(f"{len(missing)} 个引用指向不存在的实体: {missing[:8]}")

    gids = re.findall(
        r"IFC(?:PROJECT|SITE|BUILDING|BUILDINGSTOREY|WALL|SLAB|"
        r"DOOR|WINDOW|SPACE|OPENINGELEMENT|REL[A-Z]+)\('([^']*)'",
        text,
    )
    bad_guid = [g for g in gids if len(g) != 22]
    if bad_guid:
        problems.append(f"{len(bad_guid)} 个 IfcGloballyUniqueId 长度不是 22")

    # ---- 属性个数校验（手写 IFC 最容易错的地方）
    attr_errors: list[str] = []
    checked = 0
    for eid, etype in defined.items():
        expect = IFC4_ATTR_COUNTS.get(etype)
        if expect is None:
            continue
        args = _split_step_args(bodies[eid])
        checked += 1
        if len(args) != expect:
            attr_errors.append(f"#{eid} {etype} 属性 {len(args)} 个，应为 {expect}")
    if attr_errors:
        problems.append(
            f"{len(attr_errors)} 个实体属性个数不符: {attr_errors[:5]}"
        )

    counts: dict[str, int] = {}
    for name in defined.values():
        counts[name] = counts.get(name, 0) + 1

    if text.count("(") != text.count(")"):
        problems.append("括号不配平")

    return {
        "ok": not problems,
        "problems": problems,
        "bytes": len(raw),
        "entities": len(defined),
        "attributes_checked": checked,
        "entity_types": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:14]),
        "guids": len(gids),
    }
