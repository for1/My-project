"""OBJ + MTL 导出器（手写，零依赖）。

OBJ 是兼容性最广的三维格式：3ds Max、Maya、Blender、Rhino、SketchUp、
C4D、Unity、UE、ZBrush 全部直接支持，且是纯文本、易 diff、易二次处理。

输出按「对象」分组（o Wall:w_xxx / o Floor:客厅 …），并附带 .mtl 材质，
这样在 3ds Max / Blender 里导入后墙体、门窗、地面是分开的、带颜色的，
而不是一坨没有结构的网格。
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..mesh import MATERIAL_COLORS, Mesh


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def build_mtl(materials: Sequence[str], name: str = "scan2plan") -> str:
    out = [f"# scan2plan materials: {name}", ""]
    for m in materials:
        r, g, b, a = MATERIAL_COLORS.get(m, (0.8, 0.8, 0.8, 1.0))
        out.append(f"newmtl {m}")
        out.append(f"Ka 0.2 0.2 0.2")
        out.append(f"Kd {r:.4f} {g:.4f} {b:.4f}")
        out.append(f"Ks 0.05 0.05 0.05")
        out.append(f"Ns 10")
        out.append(f"d {a:.4f}")
        out.append(f"illum 2")
        out.append("")
    return "\n".join(out)


def export_obj(
    mesh: Mesh,
    path: str | Path,
    *,
    name: str = "scan2plan",
    with_mtl: bool = True,
    group_by_object: bool = True,
) -> dict:
    """导出 OBJ（可选同时写 .mtl）。返回统计信息。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# scan2plan OBJ export")
    lines.append(f"# units: meters, Y-up (Blender/C4D ready)")
    lines.append(f"o {name}")

    for v in mesh.vertices:
        lines.append(f"v {_fmt(v[0])} {_fmt(v[1])} {_fmt(v[2])}")

    # 按对象分组输出面
    groups: dict[str, list[int]] = {}
    for fi, obj in enumerate(mesh.object_names):
        groups.setdefault(obj, []).append(fi)

    mtl_path = path.with_suffix(".mtl")
    if with_mtl:
        lines.append(f"mtllib {mtl_path.name}")

    order = list(groups.keys()) if group_by_object else [name]
    current_mat = None
    for obj in order:
        fids = groups[obj] if group_by_object else list(range(len(mesh.faces)))
        lines.append(f"g {obj.replace(' ', '_')}")
        lines.append(f"o {obj.replace(' ', '_')}")
        for fi in fids:
            mat = mesh.face_materials[fi]
            if mat != current_mat:
                lines.append(f"usemtl {mat}")
                current_mat = mat
            a, b, c = mesh.faces[fi]
            # OBJ 索引从 1 开始
            lines.append(f"f {a + 1} {b + 1} {c + 1}")

    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")

    mtl_written = None
    if with_mtl:
        mtl_path.write_text(
            build_mtl(sorted(set(mesh.face_materials)), name),
            encoding="utf-8",
            newline="\n",
        )
        mtl_written = str(mtl_path)

    return {
        "format": "obj",
        "path": str(path),
        "mtl": mtl_written,
        "bytes": path.stat().st_size,
        "vertices": len(mesh.vertices),
        "triangles": len(mesh.faces),
        "objects": len(groups),
        "materials": sorted(set(mesh.face_materials)),
    }


def validate_obj(path: str | Path) -> dict:
    """结构校验：v/f 计数、索引越界、面顶点数。"""
    path = Path(path)
    v_count = 0
    f_count = 0
    groups = 0
    problems: list[str] = []
    bad_index = 0
    max_idx = 0
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        tag, _, rest = line.partition(" ")
        if tag == "v":
            parts = rest.split()
            if len(parts) != 3:
                problems.append(f"line {ln}: v 需要 3 个分量")
            v_count += 1
        elif tag == "f":
            f_count += 1
            for tok in rest.split():
                idx = int(tok.split("/")[0])
                max_idx = max(max_idx, idx)
                if idx > v_count and v_count > 0:
                    bad_index += 1
        elif tag in ("g", "o"):
            groups += 1
    if bad_index:
        problems.append(f"{bad_index} 个面索引越界")
    if f_count == 0:
        problems.append("没有面")
    return {
        "ok": not problems,
        "problems": problems,
        "vertices": v_count,
        "faces": f_count,
        "groups": groups,
        "max_index": max_idx,
        "bytes": path.stat().st_size,
    }
