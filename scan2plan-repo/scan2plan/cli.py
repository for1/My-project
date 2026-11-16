"""命令行入口。

    python -m scan2plan serve                      启动 Web 编辑器
    python -m scan2plan sample apartment -o a.json 生成样例工程
    python -m scan2plan info a.json                查看户型统计
    python -m scan2plan export a.json --format dxf --out build
    python -m scan2plan export a.json --format all --out build
    python -m scan2plan recon fetch.ply -o a.json  点云 → 户型图
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, samples
from .model import Project

SAMPLES = {
    "apartment": samples.demo_apartment,
    "single": samples.demo_single_room,
    "two": samples.demo_two_rooms,
    "lshape": samples.demo_L_room,
}


def _load(path: str) -> Project:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"文件不存在: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return Project.from_dict(data)


def _save(proj: Project, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(proj.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(f"已写入 {p}  ({p.stat().st_size} 字节)")


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(host=args.host, port=args.port, open_browser=args.open)
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    factory = SAMPLES.get(args.name)
    if factory is None:
        raise SystemExit(f"未知样例 {args.name!r}，可选: {sorted(SAMPLES)}")
    proj = factory()
    _save(proj, args.out)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    proj = _load(args.input)
    for plan in proj.plans:
        st = plan.stats()
        print(f"\n=== {plan.name}  elevation={plan.elevation} ===")
        for k, v in st.items():
            print(f"  {k}: {v}")
        rooms = plan.rooms()
        if rooms:
            print(f"  {'房间':<8}{'净面积':>10}{'毛面积':>10}{'净周长':>10}")
            for r in rooms:
                print(
                    f"  {r.label:<8}{r.net_area:>10.3f}{r.gross_area:>10.3f}{r.perimeter:>10.3f}"
                )
        problems = plan.validate()
        if problems:
            print("  检查问题:")
            for p in problems:
                print(f"    ! {p}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .exporters import dxf as dxfmod
    from .exporters import glb as glbmod
    from .exporters import obj as objmod
    from .mesh import build_plan_mesh, mesh_report

    proj = _load(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fp = proj.active
    stem = args.name or "floorplan"
    fmt = args.format.lower()
    formats = ["dxf", "ifc", "glb", "obj", "fbx"] if fmt == "all" else [fmt]
    reports = []
    for f in formats:
        if f == "dxf":
            p = out / f"{stem}.dxf"
            rep = dxfmod.export_floorplan_dxf(
                fp,
                p,
                dxfmod.DxfOptions(unit=args.unit, dim_mode=args.dims),
            )
            rep["check"] = dxfmod.validate_dxf(p)
        elif f == "ifc":
            from .exporters import ifc as ifcmod

            p = out / f"{stem}.ifc"
            rep = ifcmod.export_ifc(proj, p)
            rep["check"] = ifcmod.validate_ifc(p)
        elif f == "obj":
            p = out / f"{stem}.obj"
            rep = objmod.export_obj(build_plan_mesh(fp, include_ceiling=args.ceiling), p, name=stem)
            rep["check"] = objmod.validate_obj(p)
        elif f == "glb":
            p = out / f"{stem}.glb"
            rep = glbmod.export_glb(
                build_plan_mesh(fp, include_ceiling=args.ceiling), p, name=stem
            )
            rep["check"] = glbmod.validate_glb(p)
        elif f == "fbx":
            from .exporters import fbx as fbxmod

            p = out / f"{stem}.fbx"
            rep = fbxmod.export_fbx(build_plan_mesh(fp), p, name=stem)
            rep["check"] = fbxmod.validate_fbx(p)
        else:
            raise SystemExit(f"不支持的格式: {f}")
        reports.append(rep)
        ok = rep.get("check", {}).get("ok", True)
        probs = rep.get("check", {}).get("problems", [])
        print(
            f"  {f.upper():<5} {rep['path']}  {rep['bytes'] / 1024:7.1f} KB  "
            f"{'通过' if ok else '警告: ' + '; '.join(probs)}"
        )
    mesh = build_plan_mesh(fp)
    print(f"\n网格: {mesh_report(mesh)}")
    return 0


def cmd_recon(args: argparse.Namespace) -> int:
    from .reconstruct.pipeline import reconstruct
    from .reconstruct.vectorize import VectorizeOptions
    from .reconstruct.walls import ReconstructionOptions

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"点云文件不存在: {args.input}")
    opt = ReconstructionOptions(
        voxel_size=args.voxel,
        plane_assign_tol=args.plane_tolerance,
        min_plane_points=args.min_plane_points,
        wall_height=args.wall_height,
        thickness_ext=args.thickness_ext,
        thickness_int=args.thickness_int,
        manhattan=not args.no_manhattan,
    )
    vopt = VectorizeOptions(door_height=args.door_height, window_sill=args.window_sill)
    proj = reconstruct(
        path, options=opt, vectorize_options=vopt, name=args.name, verbose=not args.quiet
    )
    if args.out:
        _save(proj, args.out)
    rooms = proj.active.rooms()
    print(f"\n重建完成：{len(rooms)} 个房间，净面积 {sum(r.net_area for r in rooms):.2f} m²")
    for r in rooms:
        print(f"  {r.label:<10} {r.net_area:8.3f} m²  周长 {r.perimeter:6.2f} m")
    problems = proj.active.validate()
    if problems:
        print("\n检查提示（前 8 条）：")
        for p in problems[:8]:
            print(f"  ! {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scan2plan",
        description="手机室内拍摄 → 可编辑户型图 → CAD / BIM / 三维模型",
    )
    ap.add_argument("--version", action="version", version=f"scan2plan {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="启动 Web 编辑器")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="自动打开浏览器")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("sample", help="生成内置样例工程")
    p.add_argument("name", nargs="?", default="apartment", choices=sorted(SAMPLES))
    p.add_argument("-o", "--out", default="apartment.json")
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("info", help="查看户型统计")
    p.add_argument("input")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("export", help="导出 CAD / BIM / 三维格式")
    p.add_argument("input")
    p.add_argument("--format", default="dxf", help="dxf|ifc|glb|obj|fbx|all")
    p.add_argument("--out", default="build")
    p.add_argument("--name", default=None)
    p.add_argument("--unit", default="mm", choices=["mm", "m"])
    p.add_argument("--dims", default="rooms", choices=["overall", "rooms", "walls", "none"])
    p.add_argument("--ceiling", action="store_true", help="三维模型包含天花")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("recon", help="点云 → 户型图")
    p.add_argument("input", help="点云文件 (.ply/.pcd/.xyz/.npy)")
    p.add_argument("-o", "--out", default="recon.json")
    p.add_argument("--name", default=None, help="输出户型名称")
    p.add_argument("--voxel", type=float, default=0.03, help="体素尺寸（米），越大越快越粗")
    p.add_argument("--plane-tolerance", type=float, default=0.025, help="墙面归类容差（米）")
    p.add_argument("--min-plane-points", type=int, default=150)
    p.add_argument("--wall-height", type=float, default=2.8)
    p.add_argument("--thickness-ext", type=float, default=0.24)
    p.add_argument("--thickness-int", type=float, default=0.12)
    p.add_argument("--door-height", type=float, default=2.10)
    p.add_argument("--window-sill", type=float, default=0.90)
    p.add_argument("--no-manhattan", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_recon)

    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
