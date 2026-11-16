"""几何层诊断脚本 —— 开发期用来看数值对不对。

    <dsh-python> tools/diag.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

try:  # Windows 控制台默认 cp936，强制 UTF-8 才能正常显示中文
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scan2plan import samples  # noqa: E402
from scan2plan.geom import polygon_area  # noqa: E402

FAILD = 0


def check(label: str, got: float, want: float, tol: float = 1e-3) -> None:
    global FAILD
    ok = abs(got - want) <= tol
    if not ok:
        FAILD += 1
    print(f"  [{'ok ' if ok else 'BAD'}] {label:<38} got={got:11.4f}  want={want:11.4f}")


def report(proj, name: str) -> None:
    fp = proj.active
    rooms = fp.rooms()
    print(f"\n=== {name} ===")
    print(f"  vertices={len(fp.vertices)} walls={len(fp.walls)} openings={len(fp.openings)}")
    st = fp.stats()
    print(
        f"  rooms={st['rooms']} net={st['net_area_m2']:.3f} gross={st['gross_area_m2']:.3f} "
        f"wall_len={st['wall_length_m']:.3f}"
    )
    if st["unclosed_hints"]:
        for h in st["unclosed_hints"]:
            print(f"  !! {h}")
    for r in rooms:
        print(
            f"    - {r.label:<6} net={r.net_area:8.3f} gross={r.gross_area:8.3f} "
            f"perim={r.perimeter:7.3f} walls={len(r.wall_ids)} verts={len(r.polygon)}"
        )
    problems = fp.validate()
    if problems:
        print("  VALIDATION PROBLEMS:")
        for p in problems:
            print(f"    ! {p}")


def main() -> int:
    # ---- 单房间 4x3，墙厚 0.2 => 净面积 (4-0.2)*(3-0.2) = 3.8*2.8 = 10.64
    p = samples.demo_single_room()
    report(p, "单房间 4x3")
    r = p.active.rooms()[0]
    check("单房间净面积", r.net_area, 3.8 * 2.8)
    check("单房间毛面积", r.gross_area, 12.0)

    # ---- 两房共墙：内墙 0.1（半厚 0.05），外墙 0.2（半厚 0.1）
    p = samples.demo_two_rooms()
    report(p, "两房共墙 8x4")
    rooms = {r.label: r for r in p.active.rooms()}
    check("左房净面积", rooms["左房"].net_area, (5 - 0.10 - 0.05) * (4 - 0.10 - 0.10))
    check("右房净面积", rooms["右房"].net_area, (3 - 0.05 - 0.10) * (4 - 0.10 - 0.10))

    # ---- L 形房间：轴线面积 6*3+3*3=27，向内偏移 0.1
    p = samples.demo_L_room()
    report(p, "L形房间")
    r = p.active.rooms()[0]
    check("L形毛面积", r.gross_area, 27.0)
    check("L形净面积", r.net_area, 5.8 * 2.8 + 2.8 * 3.0)

    # ---- 完整户型
    p = samples.demo_apartment()
    report(p, "两室两厅 80m²")
    rooms = {r.label: r for r in p.active.rooms()}
    check("房间数", len(rooms), 6, tol=0)
    check("总毛面积", sum(r.gross_area for r in rooms.values()), 80.0)
    # 客厅：x 0.12~3.54(内墙0.12/2=0.06) y 0.12~4.74
    check("客厅净面积", rooms["客厅"].net_area, (3.6 - 0.12 - 0.06) * (4.8 - 0.12 - 0.06))
    check("厨房净面积", rooms["厨房"].net_area, (6.0 - 3.6 - 0.06 - 0.06) * (2.4 - 0.12 - 0.06))
    check("卫生间净面积", rooms["卫生间"].net_area, (2.4 - 0.06 - 0.06) * (2.4 - 0.06 - 0.06))
    check("主卧净面积", rooms["主卧"].net_area, (6.0 - 0.12 - 0.06) * (3.2 - 0.06 - 0.12))
    check(
        "次卧净面积",
        rooms["次卧"].net_area,
        (4.0 - 0.06 - 0.12) * (5.6 - 0.12 - 0.06),
    )
    check(
        "餐厅净面积",
        rooms["餐厅"].net_area,
        (4.0 - 0.06 - 0.12) * (2.4 - 0.06 - 0.12),
    )

    # ---- 墙实体轮廓：单房间外墙转角必须是完整斜接
    print("\n=== 墙体轮廓（单房间）===")
    bodies = samples.demo_single_room().active.wall_bodies()
    for b in bodies:
        poly = b.polygon()
        print(
            f"    wall len={b.length:.3f} t={b.thickness:.2f} kind={b.start_kind}/{b.end_kind} "
            f"area={b.area():.4f}"
        )
        print(f"      {[(round(x,3), round(y,3)) for x, y in poly]}")
    # 每片墙面积 = 墙长 × 墙厚（转角斜接的三角形一增一减，正好抵消）
    for b in bodies:
        want = b.length * b.thickness
        check(f"墙面积 {b.wall_id[:6]}", b.area(), want, tol=1e-6)

    print("\n=== 门窗切分 ===")
    fp = samples.demo_apartment().active
    bodies = fp.wall_body_map()
    for oid, o in fp.openings.items():
        b = bodies.get(o.wall)
        if b is None:
            continue
        boxes = b.boxes_with_openings(
            [(o.offset, o.width, o.sill, o.head)], fp.ceiling_height
        )
        total_h = sum((z1 - z0) for _poly, z0, z1 in boxes)
        print(
            f"    {o.kind:<8} on len={b.length:5.2f} w={o.width:.2f} off={o.offset:5.2f} "
            f"-> {len(boxes)} 块，竖向总高 {total_h:6.2f}"
        )

    print("\n=== 楼层统计 ===")
    st = samples.demo_apartment().active.stats()
    for k, v in st.items():
        print(f"    {k}: {v}")

    # ---- 编辑操作：改墙长、改角度、改厚度
    print("\n=== 编辑操作 ===")
    fp = samples.demo_single_room().active
    wid = next(w.id for w in fp.walls.values())
    before = fp.rooms()[0].net_area
    fp.set_wall_length(wid, 5.0)  # 默认 adjust_neighbors=True，应为 5x3 矩形
    check("改墙长后净面积", fp.rooms()[0].net_area, (5.0 - 0.2) * (3.0 - 0.2))
    print(f"    改墙长 4.0→5.0: 净面积 {before:.3f} → {fp.rooms()[0].net_area:.3f}")
    # 邻墙必须仍然横平竖直，不能被拉歪
    def off_axis(deg: float) -> float:
        return min(abs(deg - 90.0 * k) for k in range(-4, 5))

    for w in fp.walls.values():
        off = off_axis(fp.wall_angle(w.id))
        check(f"墙体仍横平竖直 {w.id[:6]}", off, 0.0, tol=1e-6)
    # 小角度旋转：房间不应塌陷
    fp.set_wall_angle(wid, 3.0)
    check("改角度后角度", fp.wall_angle(wid), 3.0, tol=1e-6)
    check("改角度后仍是 1 个房间", len(fp.rooms()), 1, tol=0)
    check("改角度后邻墙仍垂直", off_axis(fp.wall_angle(next(w.id for w in fp.walls.values() if w.id != wid))), 0.0, tol=1e-6)
    print(f"    改角度为 3° 后净面积 = {fp.rooms()[0].net_area:.3f}")
    fp.set_wall_angle(wid, 0.0)
    fp.calibrate_by_wall(wid, 5.2)
    check("标定后墙长", fp.wall_length(wid), 5.2, tol=1e-6)
    print(f"    标定缩放 5.0→5.2 后净面积 = {fp.rooms()[0].net_area:.3f}")

    # ---- JSON 往返
    print("\n=== JSON 往返 ===")
    proj = samples.demo_apartment()
    blob = proj.to_dict()
    from scan2plan import Project

    back = Project.from_dict(blob)
    a = proj.active.rooms()
    b = back.active.rooms()
    check("往返房间数", len(b), len(a), tol=0)
    check("往返净面积和", sum(r.net_area for r in b), sum(r.net_area for r in a), tol=1e-9)
    print(f"    序列化 JSON 大小 ≈ {len(str(blob))} 字符")

    print("\n" + ("全部通过" if FAILD == 0 else f"{FAILD} 项不符"))
    return 0 if FAILD == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
