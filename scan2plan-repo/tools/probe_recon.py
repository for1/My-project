"""重建调试探针：看清平面提取与配对的中间结果。"""
from __future__ import annotations

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
from scan2plan.reconstruct import walls as W  # noqa: E402
from scan2plan.reconstruct.synthetic import SynthOptions, generate_point_cloud  # noqa: E402
from scan2plan.reconstruct.planes import voxel_normals, estimate_yaw  # noqa: E402

truth = samples.demo_apartment()
cloud = generate_point_cloud(truth.active, SynthOptions(seed=0))
print("points:", len(cloud))

opt = W.ReconstructionOptions()
vd = voxel_normals(cloud.points, opt.voxel_size, opt.normal_radius)
print("voxels:", len(vd.points))
yaw, conf = estimate_yaw(vd.normals, vd.planarity)
print(f"yaw = {np.degrees(yaw):.4f}°  conf={conf:.4f}")

# 法向质量
v = np.abs(vd.normals[:, 2]) < 0.35
print(f"vertical normals: {v.sum()} / {len(vd.normals)}")
if v.sum():
    ang = np.degrees(np.arctan2(vd.normals[v, 1], vd.normals[v, 0])) % 90.0
    hist, edges = np.histogram(ang, bins=36, range=(0, 90))
    print("angle histogram (2.5° bins):")
    for i, h in enumerate(hist):
        if h > 0:
            print(f"   {edges[i]:5.1f}° : {h:6d}")
    print("    5/50/95 percentile:", np.percentile(ang, [5, 50, 95]))
print("planarity percentiles:", np.percentile(vd.planarity, [5, 25, 50, 75, 95]))

ext = W.extract_walls(cloud, opt)
print("\nplanes:")
for p in ext.planes:
    print(
        f"  axis={p.axis} coord={p.coord:8.4f} extent=[{p.start:7.3f},{p.end:7.3f}] "
        f"len={p.length:6.3f} n={p.count:6d} rms={p.rms*1000:5.2f}mm used={p.used}"
    )
print("\nsegments:")
for s in ext.segments:
    print(
        f"  {s.kind:<7} {s.p0.round(3)} -> {s.p1.round(3)} len={s.length:6.3f} "
        f"t={s.thickness:5.3f} openings={s.openings}"
    )
