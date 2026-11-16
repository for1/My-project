"""把项目打成「可以直接拖进 GitHub 网页上传」的 zip。

    python tools/make_upload_zip.py
    → dist/scan2plan-repo.zip

为什么要这个：目标机器上连 git 都没有，所以"推仓库"这条路走不通；
GitHub 网页支持拖拽上传文件，但要求 .github/ 这类隐藏目录也在压缩包根目录里。
这个脚本就是精确控制压缩包内容用的。

排除：build/ dist/ __pycache__ *.pyc _libs/ 以及所有导出产物。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "dist"

SKIP_DIRS = {
    "build",
    "dist",
    "_libs",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "_env",
    "_tmp",
    "_tmp2",
    "_wheels",
    "captures",
    "node_modules",
}
SKIP_SUFFIX = {".pyc", ".pyo", ".whl", ".apk", ".aab", ".ply", ".pcd", ".log"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "local.properties"}


def should_skip(rel: Path) -> bool:
    if any(part in SKIP_DIRS for part in rel.parts):
        return True
    if rel.suffix.lower() in SKIP_SUFFIX:
        return True
    if rel.name in SKIP_NAMES:
        return True
    # 生成物（样例工程 JSON 保留在 samples 之外的一律不打包）
    if rel.suffix.lower() == ".json" and rel.parts[0] in ("build", "dist"):
        return True
    return False


def collect() -> list[Path]:
    out: list[Path] = []
    for p in sorted(REPO.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO)
        if should_skip(rel):
            continue
        out.append(p)
    return out


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    target = DIST / "scan2plan-repo.zip"
    if target.exists():
        target.unlink()

    files = collect()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(REPO).as_posix())

    # 校验：关键路径必须在包里，且 .github 在根目录
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
    required = [
        "README.md",
        "pyproject.toml",
        ".github/workflows/build-apk.yml",
        "android/settings.gradle.kts",
        "android/build.gradle.kts",
        "android/gradle.properties",
        "android/app/build.gradle.kts",
        "android/app/src/main/AndroidManifest.xml",
        "android/app/src/main/java/com/scan2plan/capture/MainActivity.kt",
        "android/app/src/main/java/com/scan2plan/capture/CaptureEngine.kt",
        "android/app/src/main/java/com/scan2plan/capture/DepthSampler.kt",
        "android/app/src/main/java/com/scan2plan/capture/SessionWriter.kt",
        "android/app/src/main/java/com/scan2plan/capture/VoxelAccumulator.kt",
        "android/app/src/main/res/layout/activity_main.xml",
        "scan2plan/__init__.py",
        "scan2plan/cli.py",
        "scan2plan/web/index.html",
    ]
    missing = [r for r in required if r not in names]
    if missing:
        print("[失败] 压缩包缺少关键文件：")
        for m in missing:
            print("   ", m)
        return 1

    size = target.stat().st_size
    print(f"产物: {target}")
    print(f"大小: {size / 1024:.1f} KB")
    print(f"文件数: {len(files)}")
    print("\n上传要点：")
    print("  GitHub 新建仓库 → Add file → Upload files → 把本 zip **解压后**的内容拖进去")
    print("  确认仓库根目录能看到 .github/ 和 android/ 两个目录")
    print("  然后 Actions → Build Android APK → Run workflow → 下载 artifact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
