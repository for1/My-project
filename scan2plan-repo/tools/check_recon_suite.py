"""重建鲁棒性回归：多种噪声 / 稀疏度 / 旋转 / 遮挡条件下的端到端精度。

    <dsh-python> -X utf8 tools/check_recon_suite.py
    <dsh-python> -X utf8 tools/check_recon_suite.py --quick

每个用例都从「已知真值户型」合成点云 → 跑完整重建 → 与真值逐墙比对。
判定标准（对齐用户要求的 1% 相对精度，这里更严）：
    墙面位置误差 mean ≤ 20mm 且 房间数完全正确
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from scan2plan import samples  # noqa: E402
from scan2plan.geom import rot_about  # noqa: E402
from scan2plan.model import FloorPlan, Project  # noqa: E402
from scan2plan.reconstruct import accuracy, pipeline  # noqa: E402
from scan2plan.reconstruct.synthetic import SynthOptions, generate_point_cloud  # noqa: E402
from scan2plan.reconstruct.walls import ReconstructionOptions  # noqa: E402


def rotate_plan(fp: FloorPlan, angle_deg: float) -> FloorPlan:
    """绕原点整体旋转一层（门窗 offset 是沿墙参数，刚体旋转下不变）。"""
    out = fp.clone()
    pivot = (0.0, 0.0)
    for v in out.vertices.values():
        p = rot_about(v.xy, pivot, angle_deg)
        v.xy = (float(p[0]), float(p[1]))
    for lab in out.room_labels:
        p = rot_about(lab.xy, pivot, angle_deg)
        lab.xy = (float(p[0]), float(p[1]))
    return out


def run_case(
    name: str,
    synth: SynthOptions,
    *,
    rotate_deg: float = 0.0,
    recon: ReconstructionOptions | None = None,
) -> dict:
    truth = samples.demo_apartment()
    cloud = generate_point_cloud(truth.active, synth)
    if abs(rotate_deg) > 1e-9:
        cloud = cloud.rotate_z(math.radians(rotate_deg))

    proj = pipeline.reconstruct(cloud, options=recon or ReconstructionOptions())
    fp = proj.active

    if abs(rotate_deg) > 1e-9:
        # 重建结果在"墙轴对齐坐标系"里；先转回点云坐标系，再反向抵消注入的旋转
        yaw_deg = float(proj.meta.get("pipeline", {}).get("yaw_deg", 0.0))
        fp = rotate_plan(fp, yaw_deg - rotate_deg)

    acc = accuracy.compare_plans(fp, truth.active)
    s = acc["summary"]
    truth_rooms = len(truth.active.rooms())
    recon_rooms = len(fp.rooms())
    area_err = abs(acc["recon_net_area"] - acc["truth_net_area"])
    # 房间数可能因 90° 歧义对不上，此处只做形状精度判定
    ok = (
        s["mean_mm"] is not None
        and s["mean_mm"] <= 20.0
        and abs(recon_rooms - truth_rooms) <= 0
    )
    return {
        "name": name,
        "points": len(cloud),
        "mean_mm": s["mean_mm"],
        "p95_mm": s["p95_mm"],
        "max_mm": s["max_mm"],
        "within_10mm_pct": s["within_10mm_pct"],
        "rooms": f"{recon_rooms}/{truth_rooms}",
        "area_err_m2": round(area_err, 4),
        "ok": ok,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    base = dict(point_spacing=0.045)
    cases = [
        ("基线（噪声4mm+家具）", SynthOptions(noise=0.004, seed=0, **base), 0.0),
        ("高噪声 10mm", SynthOptions(noise=0.010, seed=1, **base), 0.0),
        ("稀疏采样 6cm", SynthOptions(noise=0.004, seed=2, point_spacing=0.06), 0.0),
        ("无家具干扰", SynthOptions(noise=0.004, seed=3, furniture=False, **base), 0.0),
        ("重度缺失 25%", SynthOptions(noise=0.004, seed=4, dropout=0.25, **base), 0.0),
        ("旋转户型 37°", SynthOptions(noise=0.004, seed=5, **base), 37.0),
        ("旋转户型 -64°", SynthOptions(noise=0.004, seed=6, **base), -64.0),
    ]
    if args.quick:
        cases = cases[:3]

    results = []
    print(f"共 {len(cases)} 个用例\n")
    header = f"{'用例':<20}{'点云':>9}{'mean':>9}{'p95':>9}{'max':>9}{'≤10mm':>8}{'房间':>8}{'面积差':>10}{'判定':>6}"
    print(header)
    print("-" * len(header))
    for name, synth, rot in cases:
        r = run_case(name, synth, rotate_deg=rot)
        results.append(r)

        def f(v, spec=".2f"):
            return "—" if v is None else format(v, spec)

        print(
            f"{r['name']:<20}{r['points']:>9}{f(r['mean_mm']):>9}{f(r['p95_mm']):>9}"
            f"{f(r['max_mm']):>9}{f(r['within_10mm_pct'], '.1f'):>8}{r['rooms']:>8}"
            f"{r['area_err_m2']:>10.4f}{'通过' if r['ok'] else '失败':>6}"
        )

    passed = sum(1 for r in results if r["ok"])
    print("-" * len(header))
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
