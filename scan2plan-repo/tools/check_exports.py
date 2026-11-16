"""导出格式自检：生成 demo 户型并导出全部格式，逐个校验。

    <dsh-python> -X utf8 tools/check_exports.py [--out build]
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

from scan2plan import samples  # noqa: E402
from scan2plan.exporters import dxf as dxfmod  # noqa: E402
from scan2plan.exporters import glb as glbmod  # noqa: E402
from scan2plan.exporters import obj as objmod  # noqa: E402
from scan2plan.mesh import build_plan_mesh, mesh_report  # noqa: E402


def show(report: dict, label: str) -> bool:
    ok = report.get("ok", True)
    print(f"\n=== {label} ===")
    for k, v in report.items():
        if k == "problems" and not v:
            continue
        print(f"  {k}: {v}")
    print(f"  --> {'通过' if ok else '失败'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build")
    args = ap.parse_args()
    out = REPO / args.out
    out.mkdir(parents=True, exist_ok=True)

    proj = samples.demo_apartment()
    fp = proj.active
    print(f"样例: {proj.name}  rooms={len(fp.rooms())} walls={len(fp.walls)}")

    mesh = build_plan_mesh(fp, include_ceiling=True)
    mr = mesh_report(mesh)
    print("\n=== 网格 ===")
    for k, v in mr.items():
        print(f"  {k}: {v}")

    all_ok = True

    # ---------------- DXF
    for unit, tag in (("mm", "mm"), ("m", "m")):
        p = out / f"apartment_{tag}.dxf"
        rep = dxfmod.export_floorplan_dxf(
            fp, p, dxfmod.DxfOptions(unit=unit, dim_mode="rooms")
        )
        print(f"\n=== DXF({unit}) 导出 ===")
        for k, v in rep.items():
            print(f"  {k}: {v}")
        all_ok &= show(dxfmod.validate_dxf(p), f"DXF({unit}) 结构校验")

    # ---------------- OBJ
    p = out / "apartment.obj"
    rep = objmod.export_obj(mesh, p, name="apartment")
    print("\n=== OBJ 导出 ===")
    for k, v in rep.items():
        print(f"  {k}: {v}")
    all_ok &= show(objmod.validate_obj(p), "OBJ 结构校验")

    # ---------------- GLB
    p = out / "apartment.glb"
    rep = glbmod.export_glb(mesh, p, name="apartment", extras=mr)
    print("\n=== GLB 导出 ===")
    for k, v in rep.items():
        print(f"  {k}: {v}")
    all_ok &= show(glbmod.validate_glb(p), "GLB 结构校验")

    # ---------------- FBX
    from scan2plan.exporters import fbx as fbxmod

    p = out / "apartment.fbx"
    rep = fbxmod.export_fbx(mesh, p, name="apartment")
    print("\n=== FBX 导出 ===")
    for k, v in rep.items():
        print(f"  {k}: {v}")
    all_ok &= show(fbxmod.validate_fbx(p), "FBX 结构校验")

    # ---------------- IFC
    from scan2plan.exporters import ifc as ifcmod

    p = out / "apartment.ifc"
    rep = ifcmod.export_ifc(proj, p)
    print("\n=== IFC 导出 ===")
    for k, v in rep.items():
        print(f"  {k}: {v}")
    all_ok &= show(ifcmod.validate_ifc(p), "IFC 结构校验")

    print("\n" + ("全部导出通过" if all_ok else "存在失败项"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
