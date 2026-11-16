"""重建精度验收：合成点云 → 重建 → 与真值逐墙比对。

    <dsh-python> -X utf8 tools/check_recon.py
    <dsh-python> -X utf8 tools/check_recon.py --seed 3 --noise 0.008
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
from scan2plan.reconstruct import accuracy, pipeline  # noqa: E402
from scan2plan.reconstruct.pointcloud import save_ply  # noqa: E402
from scan2plan.reconstruct.synthetic import SynthOptions, generate_point_cloud  # noqa: E402
from scan2plan.reconstruct.vectorize import VectorizeOptions  # noqa: E402
from scan2plan.reconstruct.walls import ReconstructionOptions  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="build/recon")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--noise", type=float, default=0.004)
    ap.add_argument("--spacing", type=float, default=0.035)
    ap.add_argument("--dropout", type=float, default=0.10)
    ap.add_argument("--no-furniture", action="store_true")
    ap.add_argument("--voxel", type=float, default=0.03)
    ap.add_argument("--save", action="store_true", help="保存点云与重建结果")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    out = REPO / args.out
    out.mkdir(parents=True, exist_ok=True)

    truth = samples.demo_apartment()
    sopt = SynthOptions(
        point_spacing=args.spacing,
        noise=args.noise,
        dropout=args.dropout,
        furniture=not args.no_furniture,
        seed=args.seed,
    )
    cloud = generate_point_cloud(truth.active, sopt)
    print(f"合成点云: {cloud.stats()}")
    if args.save:
        p = out / f"cloud_seed{args.seed}.ply"
        save_ply(p, cloud)
        print(f"  已保存 {p} ({p.stat().st_size/1024:.1f} KB)")

    opt = ReconstructionOptions(voxel_size=args.voxel)
    vopt = VectorizeOptions()
    proj = pipeline.reconstruct(
        cloud, options=opt, vectorize_options=vopt, name="重建户型", verbose=not args.quiet
    )

    print("\n=== 重建结果 ===")
    rep = pipeline.project_report(proj)
    print(f"  墙 {rep['walls']} / 顶点 {rep['vertices']} / 门窗 {rep['openings']} / 房间 {len(rep['rooms'])}")
    for r in rep["rooms"]:
        print(f"    {r['label']:<8} {r['net_area']:8.3f} m²  周长 {r['perimeter']:7.3f}")
    if rep["problems"]:
        print("  警告:")
        for p in rep["problems"][:6]:
            print(f"    ! {p}")

    print("\n=== 精度（重建 vs 真值）===")
    acc = accuracy.compare_projects(proj, truth)
    print(accuracy.format_report(acc))

    if args.save:
        from scan2plan.model import Project

        (out / f"recon_seed{args.seed}.json").write_text(
            __import__("json").dumps(proj.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  已保存重建工程到 {out / f'recon_seed{args.seed}.json'}")

    s = acc["summary"]
    ok = s["mean_mm"] is not None and s["mean_mm"] <= 30.0
    print("\n" + ("重建质量：可接受" if ok else "重建质量：需要调参"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
