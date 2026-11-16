"""零依赖打 wheel 包（不联网、不需要 build/setuptools）。

    python tools/build_wheel.py
    → dist/scan2plan-<version>-py3-none-any.whl

wheel 本质就是一个 zip + 一份 RECORD 清单，所以纯标准库就能手写。
打完会自动解开校验一遍（能否 import、资源文件是否齐全）。

安装：
    pip install dist/scan2plan-0.1.0-py3-none-any.whl
    scan2plan --help
"""
from __future__ import annotations

import base64
import hashlib
import re
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = "scan2plan"
DIST = REPO / "dist"


def read_version() -> str:
    text = (REPO / PKG / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("找不到 __version__")
    return m.group(1)


def collect_files() -> dict[str, Path]:
    """包内所有需要打进 wheel 的文件（.py + web 静态资源）。"""
    files: dict[str, Path] = {}
    pkg_dir = REPO / PKG
    for p in sorted(pkg_dir.rglob("*")):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo"):
            continue
        rel = p.relative_to(REPO).as_posix()
        files[rel] = p
    return files


METADATA = """Metadata-Version: 2.1
Name: {name}
Version: {version}
Summary: 手机室内扫描 → 可编辑户型图 → DXF / IFC / GLB / OBJ / FBX
Home-page: https://example.invalid/scan2plan
License: MIT
Keywords: floorplan,arcore,point-cloud,dxf,ifc,gltf,bim,cad
Classifier: Programming Language :: Python :: 3
Classifier: Topic :: Multimedia :: Graphics :: 3D Modeling
Requires-Python: >=3.10
Description-Content-Type: text/markdown

{readme}
"""

WHEEL = """Wheel-Version: 1.0
Generator: scan2plan-build-wheel (zero-dependency)
Root-Is-Purelib: true
Tag: py3-none-any
"""

ENTRY_POINTS = """[console_scripts]
scan2plan = scan2plan.cli:main
"""


def _hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build() -> Path:
    version = read_version()
    readme = ""
    rm = REPO / "README.md"
    if rm.exists():
        readme = rm.read_text(encoding="utf-8")

    files = collect_files()
    if not files:
        raise SystemExit("没有收集到任何文件")

    dist_info = f"{PKG}-{version}.dist-info"
    out = DIST / f"{PKG}-{version}-py3-none-any.whl"
    DIST.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    records: list[tuple[str, bytes]] = []

    def add(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
        zf.writestr(arcname, data)
        records.append((arcname, data))

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in files.items():
            add(zf, arcname, path.read_bytes())
        add(
            zf,
            f"{dist_info}/METADATA",
            METADATA.format(name=PKG, version=version, readme=readme).encode("utf-8"),
        )
        add(zf, f"{dist_info}/WHEEL", WHEEL.encode("utf-8"))
        add(zf, f"{dist_info}/entry_points.txt", ENTRY_POINTS.encode("utf-8"))
        add(zf, f"{dist_info}/top_level.txt", (PKG + "\n").encode("utf-8"))
        # RECORD 最后写；RECORD 自己不写 hash
        lines = []
        for arcname, data in records:
            lines.append(f"{arcname},sha256={_hash(data)},{len(data)}")
        lines.append(f"{dist_info}/RECORD,,")
        zf.writestr(f"{dist_info}/RECORD", "\n".join(lines) + "\n")

    return out


def verify(wheel: Path) -> bool:
    """解开 wheel 做一次真实校验：结构、RECORD 哈希、能否 import。"""
    print(f"\n校验 {wheel.name} …")
    problems: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        dist_info = [n for n in names if n.endswith(".dist-info/METADATA")]
        if not dist_info:
            problems.append("缺 METADATA")
        if not any(n.endswith(".dist-info/WHEEL") for n in names):
            problems.append("缺 WHEEL")
        if not any(n.endswith(".dist-info/RECORD") for n in names):
            problems.append("缺 RECORD")
        record_name = next(
            (n for n in names if n.endswith(".dist-info/RECORD")), None
        )
        if record_name:
            rec = zf.read(record_name).decode("utf-8").strip().splitlines()
            listed = set()
            for line in rec:
                parts = line.rsplit(",", 2)
                if len(parts) < 3:
                    problems.append(f"RECORD 行格式错误: {line!r}")
                    continue
                path, digest, _size = parts
                listed.add(path)
                if not digest:
                    continue
                data = zf.read(path)
                expect = "sha256=" + _hash(data)
                if digest != expect:
                    problems.append(f"RECORD 哈希不符: {path}")
            missing = set(n for n in names if not n.endswith("/")) - listed
            if missing:
                problems.append(f"{len(missing)} 个文件未列入 RECORD: {sorted(missing)[:3]}")

        # 关键：web 目录的资源必须在包里，否则 serve 会 404
        for need in ("scan2plan/web/index.html", "scan2plan/web/app.js",
                     "scan2plan/web/editor2d.js", "scan2plan/web/viewer3d.js",
                     "scan2plan/web/style.css"):
            if need not in names:
                problems.append(f"缺少资源 {need}")

    if not problems:
        # 直接从 wheel（zip）里 import —— 不需要解包到磁盘，
        # 等价于验证「装完之后 import 得起来」。
        sys.path.insert(0, str(wheel))
        try:
            import importlib

            for mod in (
                "scan2plan",
                "scan2plan.cli",
                "scan2plan.server",
                "scan2plan.model",
                "scan2plan.exporters.dxf",
                "scan2plan.exporters.ifc",
                "scan2plan.reconstruct.pipeline",
            ):
                importlib.import_module(mod)
            from scan2plan import samples

            proj = samples.demo_apartment()
            n = len(proj.active.rooms())
            print(f"  从 wheel 直接 import 全部通过；样例户型房间数 = {n}")
            if n != 6:
                problems.append(f"样例房间数异常: {n}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"import 失败: {exc}")
        finally:
            sys.path.pop(0)

    for p in problems:
        print(f"  [失败] {p}")
    if not problems:
        print("  [通过] wheel 结构与内容校验通过")
    return not problems


def main() -> int:
    wheel = build()
    ok = verify(wheel)
    size = wheel.stat().st_size
    print(f"\n产物: {wheel}")
    print(f"大小: {size / 1024:.1f} KB")
    print(f"\n安装：\n  pip install {wheel.name}\n  scan2plan --help")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
