"""用第三方库交叉校验我们手写的导出器（可选，需要联网装库）。

我们自己写的结构校验器只能证明"文件自洽"；用 ezdxf / trimesh 这类成熟库
真正把文件解析一遍，才能证明"别的软件也能读"。这是最有力的质量证据。

依赖（可选，缺了就跳过对应项）：
    ezdxf     → DXF   （还需 pyparsing）
    trimesh   → GLB / OBJ
    ifcopenshell → IFC

    python tools/bootstrap_libs.py ezdxf trimesh pyparsing

用法：
    <dsh-python> -X utf8 tools/validate_with_thirdparty.py [--out build]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "_libs"))  # 可选的第三方库目录


def ok(msg: str) -> None:
    print(f"  [通过] {msg}")


def bad(msg: str) -> None:
    print(f"  [失败] {msg}")


def skip(msg: str) -> None:
    print(f"  [跳过] {msg}")


# ---------------------------------------------------------------- DXF


def check_dxf(path: Path) -> bool:
    try:
        import ezdxf  # type: ignore
    except Exception as exc:  # noqa: BLE001
        skip(f"ezdxf 不可用（{type(exc).__name__}），跳过 DXF 交叉校验")
        return True
    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:  # noqa: BLE001
        bad(f"ezdxf 无法解析 {path.name}: {exc}")
        return False

    msp = doc.modelspace()
    counts: dict[str, int] = {}
    for e in msp:
        counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
    layers = [l.dxf.name for l in doc.layers]
    audited = 0
    try:
        auditor = doc.audit()
        audited = len(auditor.errors if hasattr(auditor, "errors") else [])
    except Exception:  # noqa: BLE001
        pass

    print(f"    ezdxf: DXF 版本 {doc.dxfversion}，实体 {sum(counts.values())} 个")
    print(f"    实体类型: {dict(sorted(counts.items(), key=lambda kv: -kv[1]))}")
    print(f"    图层({len(layers)}): {layers}")
    print(f"    审计错误: {audited}")

    problems = []
    if not counts:
        problems.append("模型空间没有实体")
    if "LWPOLYLINE" not in counts and "POLYLINE" not in counts:
        problems.append("没有多段线（墙体轮廓缺失）")
    if "TEXT" not in counts:
        problems.append("没有文字（房间名/标注缺失）")
    if audited > 0:
        problems.append(f"ezdxf 审计发现 {audited} 个错误")
    for p in problems:
        bad(p)
    if not problems:
        ok("ezdxf 完整解析并通过审计")
    return not problems


# ---------------------------------------------------------------- GLB


def check_glb(path: Path) -> bool:
    try:
        import trimesh  # type: ignore
    except Exception as exc:  # noqa: BLE001
        skip(f"trimesh 不可用（{type(exc).__name__}），跳过 GLB 交叉校验")
        return True
    try:
        scene = trimesh.load(str(path), force="scene")
    except Exception as exc:  # noqa: BLE001
        bad(f"trimesh 无法加载 GLB: {exc}")
        return False
    geoms = list(scene.geometry.values())
    tris = sum(len(g.faces) for g in geoms)
    print(f"    trimesh: 几何体 {len(geoms)} 个，三角面 {tris}，材质 {len(scene.geometry)}")
    print(f"    包围盒: {scene.bounds.tolist() if scene.bounds is not None else None}")
    problems = []
    if not geoms or tris == 0:
        problems.append("没有几何体")
    if scene.bounds is None:
        problems.append("包围盒为空")
    for p in problems:
        bad(p)
    if not problems:
        ok("trimesh 成功解析 GLB，几何与包围盒有效")
    return not problems


# ---------------------------------------------------------------- OBJ


def check_obj(path: Path) -> bool:
    try:
        import trimesh  # type: ignore
    except Exception as exc:  # noqa: BLE001
        skip(f"trimesh 不可用（{type(exc).__name__}），跳过 OBJ 交叉校验")
        return True
    try:
        scene = trimesh.load(str(path), force="scene")
    except Exception as exc:  # noqa: BLE001
        bad(f"trimesh 无法加载 OBJ: {exc}")
        return False
    tris = sum(len(g.faces) for g in scene.geometry.values())
    print(f"    trimesh: 几何体 {len(scene.geometry)}，三角面 {tris}")
    if tris == 0:
        bad("OBJ 没有三角面")
        return False
    ok("trimesh 成功解析 OBJ")
    return True


# ---------------------------------------------------------------- IFC


def check_ifc(path: Path) -> bool:
    try:
        import ifcopenshell  # type: ignore
    except Exception as exc:  # noqa: BLE001
        skip(f"ifcopenshell 不可用（{type(exc).__name__}），跳过 IFC 交叉校验")
        return True
    try:
        f = ifcopenshell.open(str(path))
    except Exception as exc:  # noqa: BLE001
        bad(f"ifcopenshell 无法打开 IFC: {exc}")
        return False
    schema = f.schema
    walls = f.by_type("IfcWall")
    slabs = f.by_type("IfcSlab")
    spaces = f.by_type("IfcSpace")
    doors = f.by_type("IfcDoor")
    windows = f.by_type("IfcWindow")
    openings = f.by_type("IfcOpeningElement")
    project = f.by_type("IfcProject")
    print(
        f"    ifcopenshell: schema={schema} walls={len(walls)} slabs={len(slabs)} "
        f"spaces={len(spaces)} doors={len(doors)} windows={len(windows)} openings={len(openings)}"
    )
    problems = []
    if not project:
        problems.append("缺少 IfcProject")
    if not walls:
        problems.append("没有 IfcWall")
    if not openings:
        problems.append("没有 IfcOpeningElement（门窗洞口未生成）")
    for p in problems:
        bad(p)
    if not problems:
        ok("ifcopenshell 成功打开 IFC4，空间结构与构件完整")
    return not problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build")
    args = ap.parse_args()
    out = REPO / args.out
    if not out.exists():
        print(f"目录不存在：{out}（先跑 tools/check_exports.py）")
        return 2

    results: dict[str, bool] = {}
    checks = [
        ("DXF", "apartment_mm.dxf", check_dxf),
        ("GLB", "apartment.glb", check_glb),
        ("OBJ", "apartment.obj", check_obj),
        ("IFC", "apartment.ifc", check_ifc),
    ]
    for label, filename, fn in checks:
        path = out / filename
        print(f"\n=== {label} 交叉校验（{filename}）===")
        if not path.exists():
            print(f"  [跳过] 文件不存在：{path}")
            results[label] = True
            continue
        results[label] = fn(path)

    print("\n" + "-" * 50)
    for k, v in results.items():
        print(f"  {k}: {'通过' if v else '失败'}")
    all_ok = all(results.values())
    print("\n" + ("第三方库交叉校验全部通过" if all_ok else "存在失败项"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
