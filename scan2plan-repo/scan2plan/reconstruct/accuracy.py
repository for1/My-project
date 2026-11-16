"""精度评估：重建户型 vs 真值户型。

「1cm 精度」不能只靠嘴说，必须能量。本模块给出可复现的误差指标：

    wall_errors     每一面真值墙到最近重建墙的**垂直距离**（mm）
    angle_errors    对应墙的方向角误差（度）
    room_area_errors 同位置房间的净面积相对误差（%）
    summary         mean / p95 / max，以及 1cm、2cm、5cm 内的通过率

注意：这是「重建几何误差」，不是整体测量不确定度。真机数据里还叠加了
IMU/深度标定误差，最终验收要按 README 里写的现场标定流程做。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..geom import angle_between, as_xy, cross, dot, seg_point_distance, unit
from ..model import FloorPlan, Project


@dataclass
class WallError:
    truth_index: int
    recon_index: int | None
    distance_mm: float
    angle_deg: float
    truth_length: float
    overlap: float


def _wall_segments(fp: FloorPlan) -> list[tuple[np.ndarray, np.ndarray, float]]:
    out = []
    for w in fp.walls.values():
        a, b = fp.wall_endpoints(w.id)
        out.append((a, b, w.thickness))
    return out


def _point_to_line_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    d = b - a
    n = np.hypot(*d)
    if n < 1e-9:
        return float(np.hypot(*(p - a)))
    return abs(float(cross(d / n, p - a)))


def compare_plans(
    recon: FloorPlan,
    truth: FloorPlan,
    *,
    angle_tol_deg: float = 5.0,
    min_overlap: float = 0.15,
) -> dict:
    """逐墙比对，返回误差统计。"""
    rsegs = _wall_segments(recon)
    tsegs = _wall_segments(truth)

    errors: list[WallError] = []
    for ti, (ta, tb, tt) in enumerate(tsegs):
        td = unit(tb - ta)
        tL = float(np.hypot(*(tb - ta)))
        if tL < 1e-6:
            continue
        best: tuple[float, int] | None = None
        for ri, (ra, rb, rt) in enumerate(rsegs):
            rd = unit(rb - ra)
            ang = angle_between(td, rd)
            if min(ang, abs(180.0 - ang)) > angle_tol_deg:
                continue
            dist = _point_to_line_distance((ta + tb) / 2, ra, rb)
            # 沿真值墙方向检查是否有实体覆盖（避免匹配到很远的平行墙）
            proj = []
            for k in range(11):
                p = ta + td * (tL * k / 10)
                proj.append(seg_point_distance(p, ra, rb))
            lateral = float(np.max(proj))
            score = dist + lateral * 0.5
            if best is None or score < best[0]:
                best = (score, ri)
        if best is None:
            errors.append(WallError(ti, None, float("inf"), float("nan"), tL, 0.0))
            continue
        ri = best[1]
        ra, rb, rt = rsegs[ri]
        rd = unit(rb - ra)
        if dot(rd, td) < 0:
            rd = -rd
        dist = _point_to_line_distance((ta + tb) / 2, ra, rb)
        ang = angle_between(td, rd)
        # 覆盖长度：真值墙上有多少比例的点落在重建墙段近旁
        covered = 0
        for k in range(21):
            p = ta + td * (tL * k / 20)
            if seg_point_distance(p, ra, rb) <= 0.08:
                covered += 1
        overlap = covered / 21.0
        errors.append(WallError(ti, ri, dist * 1000.0, ang, tL, overlap))

    matched = [e for e in errors if e.recon_index is not None]
    dists = np.array([e.distance_mm for e in matched]) if matched else np.zeros(0)

    # 房间面积对比：按质心就近匹配
    rrooms = recon.rooms()
    trooms = truth.rooms()
    area_errors = []
    for tr in trooms:
        hit = None
        for rr in rrooms:
            if _point_in(rr.polygon, tr.centroid):
                hit = rr
                break
        if hit is None:
            # 退化为最近房间
            best = None
            for rr in rrooms:
                d = math.hypot(rr.centroid[0] - tr.centroid[0], rr.centroid[1] - tr.centroid[1])
                if best is None or d < best[0]:
                    best = (d, rr)
            if best and best[0] < 2.5:
                hit = best[1]
        if hit is None:
            area_errors.append(
                {"truth": tr.label, "truth_area": round(tr.net_area, 3), "recon": None,
                 "abs_error": None, "rel_error_pct": None}
            )
            continue
        abs_err = hit.net_area - tr.net_area
        area_errors.append(
            {
                "truth": tr.label,
                "recon": hit.label,
                "truth_area": round(tr.net_area, 3),
                "recon_area": round(hit.net_area, 3),
                "abs_error": round(abs_err, 4),
                "rel_error_pct": round(100.0 * abs_err / tr.net_area, 3) if tr.net_area else None,
            }
        )

    def pct(limit_mm: float) -> float:
        if not len(dists):
            return 0.0
        return round(100.0 * float((dists <= limit_mm).mean()), 1)

    total_area = sum(r.net_area for r in trooms) or 1.0
    return {
        "truth_walls": len(tsegs),
        "recon_walls": len(rsegs),
        "matched_walls": len(matched),
        "unmatched_truth_walls": len(errors) - len(matched),
        "wall_errors": [
            {
                "truth": e.truth_index,
                "recon": e.recon_index,
                "distance_mm": None if not math.isfinite(e.distance_mm) else round(e.distance_mm, 2),
                "angle_deg": None if math.isnan(e.angle_deg) else round(e.angle_deg, 3),
                "truth_length": round(e.truth_length, 3),
                "overlap": round(e.overlap, 2),
            }
            for e in errors
        ],
        "summary": {
            "mean_mm": round(float(dists.mean()), 2) if len(dists) else None,
            "p95_mm": round(float(np.percentile(dists, 95)), 2) if len(dists) else None,
            "max_mm": round(float(dists.max()), 2) if len(dists) else None,
            "within_5mm_pct": pct(5.0),
            "within_10mm_pct": pct(10.0),
            "within_20mm_pct": pct(20.0),
            "within_50mm_pct": pct(50.0),
        },
        "truth_net_area": round(total_area, 3),
        "recon_net_area": round(sum(r.net_area for r in rrooms), 3),
        "room_area_errors": area_errors,
    }


def _point_in(poly, p) -> bool:
    x, y = float(p[0]), float(p[1])
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-300) + x1
            if x < xint:
                inside = not inside
    return inside


def compare_projects(recon: Project, truth: Project) -> dict:
    return compare_plans(recon.active, truth.active)


def format_report(report: dict) -> str:
    s = report["summary"]
    lines = [
        f"  真值墙 {report['truth_walls']} / 重建墙 {report['recon_walls']} / "
        f"匹配 {report['matched_walls']} / 未匹配 {report['unmatched_truth_walls']}",
        f"  墙面位置误差: mean {s['mean_mm']}mm  p95 {s['p95_mm']}mm  max {s['max_mm']}mm",
        f"  命中率: ≤5mm {s['within_5mm_pct']}%   ≤10mm {s['within_10mm_pct']}%   "
        f"≤20mm {s['within_20mm_pct']}%   ≤50mm {s['within_50mm_pct']}%",
        f"  净面积: 真值 {report['truth_net_area']} m² / 重建 {report['recon_net_area']} m²",
    ]
    for r in report["room_area_errors"]:
        if r["recon"] is None:
            lines.append(f"    - {r['truth']}: 未匹配")
        else:
            lines.append(
                f"    - {r['truth']:<6} 真值 {r['truth_area']:7.3f}  重建 {r['recon_area']:7.3f}  "
                f"误差 {r['abs_error']:+.4f} m² ({r['rel_error_pct']:+.2f}%)"
            )
    return "\n".join(lines)
