"""点云容器与文件读写（零依赖，只支持重建需要的字段）。

支持格式：
    .ply   ASCII 与 binary_little_endian（含 x/y/z，可选 rgb）
    .pcd   ASCII（x y z 或 x y z rgb）
    .xyz / .txt  空白分隔的 x y z
    .npy  numpy 数组 (N,3) 或 (N,6)

坐标约定与全工程一致：米，Z 向上。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np


@dataclass
class PointCloud:
    points: np.ndarray                     # (N, 3) float64
    colors: np.ndarray | None = None       # (N, 3) uint8
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.points = np.asarray(self.points, dtype=np.float64).reshape(-1, 3)
        if self.colors is not None:
            self.colors = np.asarray(self.colors, dtype=np.uint8).reshape(-1, 3)

    def __len__(self) -> int:
        return int(self.points.shape[0])

    # ------------------------------------------------------------ 基本操作

    def copy(self) -> "PointCloud":
        return PointCloud(
            self.points.copy(),
            None if self.colors is None else self.colors.copy(),
            dict(self.meta),
        )

    def transform(self, R: np.ndarray, t: Iterable[float] = (0.0, 0.0, 0.0)) -> "PointCloud":
        pts = self.points @ np.asarray(R, dtype=float).T + np.asarray(t, dtype=float)
        return PointCloud(pts, self.colors, dict(self.meta))

    def rotate_z(self, angle_rad: float) -> "PointCloud":
        c, s = np.cos(angle_rad), np.sin(angle_rad)
        R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        return self.transform(R)

    def translate(self, t: Iterable[float]) -> "PointCloud":
        return PointCloud(self.points + np.asarray(t, dtype=float), self.colors, dict(self.meta))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        if not len(self):
            z = np.zeros(3)
            return z, z
        return self.points.min(axis=0), self.points.max(axis=0)

    def center(self) -> np.ndarray:
        lo, hi = self.bounds()
        return (lo + hi) * 0.5

    def voxel_downsample(self, voxel: float) -> "PointCloud":
        """体素下采样：每个体素取均值，返回带元信息（每体素点数）。"""
        if voxel <= 0 or not len(self):
            return self.copy()
        keys = np.floor(self.points / voxel).astype(np.int64)
        uniq, inverse, counts = np.unique(
            keys, axis=0, return_inverse=True, return_counts=True
        )
        n = uniq.shape[0]
        sums = np.zeros((n, 3))
        np.add.at(sums, inverse.ravel(), self.points)
        centroids = sums / counts[:, None]
        colors = None
        if self.colors is not None:
            csum = np.zeros((n, 3))
            np.add.at(csum, inverse.ravel(), self.colors.astype(float))
            colors = (csum / counts[:, None]).astype(np.uint8)
        meta = dict(self.meta)
        meta["voxel_size"] = float(voxel)
        meta["source_points"] = int(len(self))
        out = PointCloud(centroids, colors, meta)
        out.voxel_counts = counts  # type: ignore[attr-defined]
        return out

    def filter_height(self, zmin: float, zmax: float) -> "PointCloud":
        m = (self.points[:, 2] >= zmin) & (self.points[:, 2] <= zmax)
        return PointCloud(self.points[m], None if self.colors is None else self.colors[m], dict(self.meta))

    def random_sample(self, n: int, seed: int = 0) -> "PointCloud":
        if len(self) <= n:
            return self
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(self), size=n, replace=False)
        return PointCloud(self.points[idx], None if self.colors is None else self.colors[idx], dict(self.meta))

    def stats(self) -> dict:
        lo, hi = self.bounds()
        return {
            "points": int(len(self)),
            "bbox_min": [round(float(v), 4) for v in lo],
            "bbox_max": [round(float(v), 4) for v in hi],
            "extent": [round(float(v), 4) for v in (hi - lo)],
        }


# ================================================================ PLY


def load_ply(path: str | Path) -> PointCloud:
    raw = Path(path).read_bytes()
    end = raw.find(b"end_header")
    if end < 0:
        raise ValueError("PLY 缺少 end_header")
    nl = raw.find(b"\n", end)
    header = raw[:nl].decode("ascii", errors="replace")
    body = raw[nl + 1 :]

    fmt = "ascii"
    n_vertex = 0
    props: list[tuple[str, str]] = []
    in_vertex = False
    for line in header.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            in_vertex = parts[1] == "vertex"
            if in_vertex:
                n_vertex = int(parts[2])
        elif parts[0] == "property" and in_vertex and len(parts) >= 3:
            props.append((parts[2], parts[1]))

    names = [p[0] for p in props]
    for need in ("x", "y", "z"):
        if need not in names:
            raise ValueError(f"PLY 缺少 {need} 属性")

    if fmt == "ascii":
        rows = body.decode("ascii", errors="replace").split("\n")
        vals = []
        for line in rows:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < len(props):
                continue
            vals.append([float(parts[i]) for i in range(len(props))])
            if len(vals) >= n_vertex:
                break
        arr = np.asarray(vals, dtype=np.float64)
    elif fmt == "binary_little_endian":
        dtype_map = {
            "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
            "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
            "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
            "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
        }
        dt = np.dtype([(name, dtype_map[t]) for name, t in props])
        arr = np.frombuffer(body[: n_vertex * dt.itemsize], dtype=dt)
        cols = np.stack([arr[n].astype(np.float64) for n in names], axis=1)
        arr = cols
    else:
        raise ValueError(f"不支持的 PLY 格式: {fmt}")

    if arr.size == 0:
        return PointCloud(np.zeros((0, 3)))
    ix, iy, iz = names.index("x"), names.index("y"), names.index("z")
    pts = arr[:, [ix, iy, iz]]
    colors = None
    for cand in (("red", "green", "blue"), ("r", "g", "b")):
        if all(c in names for c in cand):
            colors = arr[:, [names.index(c) for c in cand]].astype(np.uint8)
            break
    meta = {"format": "ply", "source": str(path)}
    return PointCloud(pts, colors, meta)


def save_ply(path: str | Path, cloud: PointCloud, *, binary: bool = False) -> int:
    """写出 PLY。默认 ASCII（方便肉眼检查与 diff）。"""
    p = Path(path)
    n = len(cloud)
    has_color = cloud.colors is not None
    lines = [
        "ply",
        "format binary_little_endian 1.0" if binary else "format ascii 1.0",
        "comment generated by scan2plan",
        f"element vertex {n}",
        "property float x",
        "property float y",
        "property float z",
    ]
    if has_color:
        lines += ["property uchar red", "property uchar green", "property uchar blue"]
    lines.append("end_header")
    header = ("\n".join(lines) + "\n").encode("ascii")
    if binary:
        dtype = [("x", "f4"), ("y", "f4"), ("z", "f4")]
        if has_color:
            dtype += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
        arr = np.zeros(n, dtype=dtype)
        arr["x"], arr["y"], arr["z"] = (
            cloud.points[:, 0], cloud.points[:, 1], cloud.points[:, 2]
        )
        if has_color:
            arr["red"], arr["green"], arr["blue"] = (
                cloud.colors[:, 0], cloud.colors[:, 1], cloud.colors[:, 2]
            )
        p.write_bytes(header + arr.tobytes())
    else:
        body = []
        for i in range(n):
            x, y, z = cloud.points[i]
            if has_color:
                c = cloud.colors[i]
                body.append(f"{x:.6f} {y:.6f} {z:.6f} {c[0]} {c[1]} {c[2]}")
            else:
                body.append(f"{x:.6f} {y:.6f} {z:.6f}")
        p.write_text("\n".join(lines + body) + "\n", encoding="ascii", newline="\n")
    return p.stat().st_size


# ================================================================ OBJ / GLB（网格 → 点云）


def _sample_surface(
    verts: np.ndarray, faces: np.ndarray, target_points: int, seed: int = 0
) -> np.ndarray:
    """在三角网格表面上按面积加权撒点。

    为什么需要：手机扫描 App（Polycam / Scaniverse 等）导出的 OBJ/GLB 往往是
    **低模**（几万顶点），直接拿顶点当点云密度不够，墙面平面拟合会不稳。
    按面积均匀撒到 20~40 万点，后续点云管线就能正常工作。
    """
    if len(faces) == 0:
        return verts.copy()
    a = verts[faces[:, 0]]
    b = verts[faces[:, 1]]
    c = verts[faces[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = float(area.sum())
    if total <= 0:
        return verts.copy()
    prob = area / total
    rng = np.random.default_rng(seed)
    n = max(int(target_points), len(verts))
    idx = rng.choice(len(faces), size=n, replace=True, p=prob)
    r1 = np.sqrt(rng.random(n))
    r2 = rng.random(n)
    p = (
        (1 - r1)[:, None] * a[idx]
        + (r1 * (1 - r2))[:, None] * b[idx]
        + (r1 * r2)[:, None] * c[idx]
    )
    return np.vstack([verts, p])


def load_obj(path: str | Path, *, target_points: int = 300_000) -> PointCloud:
    """读 OBJ 网格，把表面采样成点云（手机扫描 App 最常用的导出格式之一）。"""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line[0] == "#":
            continue
        head = line[:2]
        if head == "v ":
            parts = line.split()
            if len(parts) >= 4:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif head == "f ":
            idxs: list[int] = []
            for tok in line.split()[1:]:
                k = int(tok.split("/")[0])
                if k < 0:
                    k = len(verts) + k + 1
                idxs.append(k - 1)
            if len(idxs) >= 3:
                for i in range(1, len(idxs) - 1):
                    faces.append((idxs[0], idxs[i], idxs[i + 1]))
    if not verts:
        raise ValueError("OBJ 里没有顶点")
    v = np.asarray(verts, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64) if faces else np.zeros((0, 3), dtype=np.int64)
    if len(f):
        f = f[(f >= 0).all(axis=1) & (f < len(v)).all(axis=1)]
    pts = _sample_surface(v, f, target_points)
    return PointCloud(
        pts,
        meta={
            "format": "obj",
            "source": str(path),
            "mesh_vertices": int(len(v)),
            "mesh_triangles": int(len(f)),
            "up_axis": "z",  # OBJ 无规范上轴，扫描 App 导出通常是 Z-up
        },
    )


_GLB_MAGIC = 0x46546C67


def load_glb(
    path: str | Path,
    *,
    target_points: int = 300_000,
    up_axis: str = "y",
) -> PointCloud:
    """读 GLB（glTF 2.0 二进制）并采样成点云。

    GLB 结构简单到可以自己解析：JSON chunk 描述场景/网格/访问器，BIN chunk 放顶点数据。
    这里取 POSITION 属性，按节点变换（含嵌套父子）变到世界坐标。

    glTF 规范是 Y-up，本工程是 Z-up，默认做 (X,Y,Z)→(X,−Z,Y) 转换。
    """
    import json as _json

    raw = Path(path).read_bytes()
    if len(raw) < 20 or int.from_bytes(raw[0:4], "little") != _GLB_MAGIC:
        raise ValueError("不是 GLB 文件（魔数不符）")
    off = 12
    json_len = int.from_bytes(raw[off : off + 4], "little")
    off += 8
    gltf = _json.loads(raw[off : off + json_len].decode("utf-8"))
    off += json_len
    bin_blob = b""
    if off + 8 <= len(raw):
        bin_len = int.from_bytes(raw[off : off + 4], "little")
        off += 8
        bin_blob = raw[off : off + bin_len]

    views = gltf.get("bufferViews", [])
    accessors = gltf.get("accessors", [])
    meshes = gltf.get("meshes", [])
    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes", [{}]) or [{}]
    si = gltf.get("scene", 0)
    roots = scenes[si].get("nodes", []) if si < len(scenes) else list(range(len(nodes)))

    def read_accessor(acc_idx: int) -> np.ndarray | None:
        acc = accessors[acc_idx]
        view = views[acc.get("bufferView", 0)]
        base = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
        count = int(acc["count"])
        if count <= 0 or not bin_blob:
            return None
        comp = acc.get("componentType")
        if comp == 5126 and acc.get("type") == "VEC3":
            stride = int(view.get("byteStride") or 12)
            if stride == 12:
                return (
                    np.frombuffer(bin_blob, dtype="<f4", count=count * 3, offset=base)
                    .reshape(-1, 3)
                    .astype(np.float64)
                )
            out = np.zeros((count, 3), dtype=np.float64)
            for i in range(count):
                out[i] = np.frombuffer(
                    bin_blob, dtype="<f4", count=3, offset=base + i * stride
                )
            return out
        # 索引
        dt = {5121: "u1", 5123: "<u2", 5125: "<u4"}.get(comp)
        if dt is not None:
            return np.frombuffer(
                bin_blob, dtype=dt, count=count, offset=base
            ).astype(np.int64)
        return None

    def node_matrix(node: dict) -> np.ndarray:
        m = node.get("matrix")
        if m and len(m) == 16:
            return np.asarray(m, dtype=np.float64).reshape(4, 4).T
        t = np.asarray(node.get("translation", [0, 0, 0]), dtype=np.float64)
        r = np.asarray(node.get("rotation", [0, 0, 0, 1]), dtype=np.float64)
        s = np.asarray(node.get("scale", [1, 1, 1]), dtype=np.float64)
        x, y, z, w = r
        rot = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        mat = np.eye(4)
        mat[:3, :3] = rot * s[None, :]
        mat[:3, 3] = t
        return mat

    # 逐 primitive 收集 (顶点, 三角形)。索引是相对该 primitive 的 POSITION 的，
    # 所以不能跨 primitive 合并后再重采样 —— 必须每个 primitive 单独采样。
    chunk_data: list[tuple[np.ndarray, np.ndarray | None]] = []
    visited: set[int] = set()

    def walk(ni: int, parent: np.ndarray) -> None:
        if ni in visited or ni >= len(nodes):
            return
        visited.add(ni)
        node = nodes[ni]
        world = parent @ node_matrix(node)
        if "mesh" in node and int(node["mesh"]) < len(meshes):
            for prim in meshes[int(node["mesh"])].get("primitives", []):
                pa = prim.get("attributes", {}).get("POSITION")
                if pa is None:
                    continue
                pts = read_accessor(int(pa))
                if pts is None or not len(pts):
                    continue
                homo = np.hstack([pts, np.ones((len(pts), 1))])
                world_pts = (world @ homo.T).T[:, :3]
                tris = None
                ia = prim.get("indices")
                if ia is not None:
                    idx = read_accessor(int(ia))
                    if idx is not None and len(idx) >= 3 and len(idx) % 3 == 0:
                        tris = idx.reshape(-1, 3)
                chunk_data.append((world_pts, tris))
        for ch in node.get("children", []):
            walk(int(ch), world)

    for root in roots:
        walk(int(root), np.eye(4))
    if not chunk_data:
        raise ValueError("GLB 里没有可用的 POSITION 顶点数据")

    # 每个 primitive 按面积重采样，再合并
    sampled: list[np.ndarray] = []
    used_surface = 0
    for pts, tris in chunk_data:
        if tris is not None and len(tris):
            valid = (tris >= 0).all(axis=1) & (tris < len(pts)).all(axis=1)
            tris = tris[valid]
            if len(tris):
                share = max(1, int(target_points * len(tris) / max(1, sum(
                    len(t) if t is not None else 0 for _p, t in chunk_data
                ))))
                sampled.append(_sample_surface(pts, tris, share))
                used_surface += 1
                continue
        sampled.append(pts)
    pts = np.vstack(sampled)
    resample = f"surface-sampled({used_surface}/{len(chunk_data)})" if used_surface else "vertices-only"

    if up_axis == "y":
        pts = np.stack([pts[:, 0], -pts[:, 2], pts[:, 1]], axis=1)

    return PointCloud(
        pts,
        meta={
            "format": "glb",
            "source": str(path),
            "primitives": len(chunk_data),
            "resample": resample,
            "up_axis": up_axis,
            "converted_to": "z-up",
        },
    )


# ================================================================ PCD / XYZ


def load_pcd(path: str | Path) -> PointCloud:
    raw = Path(path).read_bytes()
    header_end = raw.find(b"DATA")
    header = raw[:header_end].decode("ascii", errors="replace")
    fields: list[str] = []
    n_points = 0
    data_type = "ascii"
    for line in header.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "FIELDS":
            fields = parts[1:]
        elif parts[0] == "POINTS":
            n_points = int(parts[1])
        elif parts[0] == "DATA":
            data_type = parts[1] if len(parts) > 1 else "ascii"
    if data_type != "ascii":
        raise ValueError("PCD 仅支持 ascii（binary 请先转换）")
    body = raw[raw.find(b"DATA") :].split(b"\n", 1)[1]
    vals = []
    for line in body.decode("ascii", errors="replace").split("\n"):
        if not line.strip():
            continue
        vals.append([float(x) for x in line.split()[: len(fields)]])
        if len(vals) >= n_points:
            break
    arr = np.asarray(vals, dtype=np.float64)
    if arr.size == 0:
        return PointCloud(np.zeros((0, 3)))
    ix = fields.index("x")
    iy = fields.index("y")
    iz = fields.index("z")
    return PointCloud(arr[:, [ix, iy, iz]], meta={"format": "pcd", "source": str(path)})


def load_xyz(path: str | Path) -> PointCloud:
    data = np.loadtxt(path, ndmin=2)
    if data.shape[1] < 3:
        raise ValueError("XYZ 文件至少需要 3 列")
    colors = data[:, 3:6].astype(np.uint8) if data.shape[1] >= 6 else None
    return PointCloud(data[:, :3], colors, {"format": "xyz", "source": str(path)})


def load_npy(path: str | Path) -> PointCloud:
    data = np.load(path)
    data = np.asarray(data)
    if data.ndim != 2 or data.shape[1] < 3:
        raise ValueError("npy 需要 (N,3) 或 (N,6)")
    colors = data[:, 3:6].astype(np.uint8) if data.shape[1] >= 6 else None
    return PointCloud(data[:, :3], colors, {"format": "npy", "source": str(path)})


def load(path: str | Path) -> PointCloud:
    """按扩展名自动识别输入格式。

    支持两类输入：
        点云：.ply .pcd .xyz .txt .pts .csv .npy
        网格：.obj .glb  （手机扫描 App 最常导出的两种，会自动表面重采样成点云）
    """
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".ply":
        return load_ply(p)
    if ext == ".pcd":
        return load_pcd(p)
    if ext == ".npy":
        return load_npy(p)
    if ext in (".xyz", ".txt", ".pts", ".csv"):
        return load_xyz(p)
    if ext == ".obj":
        return load_obj(p)
    if ext == ".glb":
        return load_glb(p)
    if ext in (".gltf", ".fbx", ".stl", ".dae"):
        raise ValueError(
            f"{ext} 暂不支持直接读。请先导出为 .obj / .glb / .ply "
            f"（多数扫描 App 都支持），或用 Blender / MeshLab 转一次。"
        )
    raise ValueError(f"无法识别的输入格式: {ext}")

