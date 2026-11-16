"""Development-only dependency bootstrap.

The DSH sandbox blocks pip's temp-dir strategy on this host, so instead of
`pip install` we fetch the exact official wheels from the PyPI JSON API and
unpack them into ``<repo>/_libs``.  Nothing here is needed at runtime by the
shipped exporters -- production code depends on numpy only.  These libraries
exist so the test-suite can *independently validate* the DXF/IFC/GLB files we
write by hand (ezdxf, trimesh, ifcopenshell).

Usage:
    python tools/bootstrap_libs.py
    python tools/bootstrap_libs.py ezdxf trimesh
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIBS = REPO / "_libs"

DEFAULT_PACKAGES = [
    "ezdxf",
    "trimesh",
    "shapely",
    "networkx",
    "pyparsing",
]

# Wheel tags we are willing to unpack, best first.  This host is Windows x64 /
# CPython 3.12; pure-python wheels are always acceptable.
PREFERRED_TAGS = ("cp312-cp312-win_amd64", "py3-none-any", "py2.py3-none-any")

UA = {"User-Agent": "scan2plan-bootstrap/1.0"}


def _rank(filename: str) -> int:
    for i, tag in enumerate(PREFERRED_TAGS):
        if tag in filename:
            return i
    return len(PREFERRED_TAGS)


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick_wheel(pkg: str) -> tuple[str, str]:
    meta = _fetch_json(f"https://pypi.org/pypi/{pkg}/json")
    version = meta["info"]["version"]
    candidates = [
        f
        for f in meta["releases"][version]
        if f["filename"].endswith(".whl") and f.get("yanked") is False
    ]
    if not candidates:
        raise RuntimeError(f"{pkg} {version}: no wheel published")
    candidates.sort(key=lambda f: _rank(f["filename"]))
    best = candidates[0]
    if _rank(best["filename"]) >= len(PREFERRED_TAGS):
        raise RuntimeError(
            f"{pkg} {version}: no compatible wheel "
            f"(saw {[c['filename'] for c in candidates][:4]})"
        )
    return best["filename"], best["url"]


def install(pkg: str) -> None:
    filename, url = pick_wheel(pkg)
    print(f"  {pkg:<12} {filename}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as resp:
        blob = resp.read()
    LIBS.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        # A wheel is a zip; skip the metadata dirs we do not import.
        for name in names:
            if name.endswith("/") or name.startswith(".dist-info/") or name.startswith(".data/"):
                continue
            dest = LIBS / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as out:
                out.write(src.read())


def main(argv: list[str]) -> int:
    packages = argv[1:] or DEFAULT_PACKAGES
    print(f"unpacking wheels into {LIBS}")
    for pkg in packages:
        try:
            install(pkg)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  !! {pkg}: {exc}", file=sys.stderr)
    sys.path.insert(0, str(LIBS))
    ok = 0
    for pkg in packages:
        try:
            mod = __import__(pkg)
            ver = getattr(mod, "__version__", "?")
            print(f"  ok {pkg:<12} {ver}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {pkg}: {exc}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
