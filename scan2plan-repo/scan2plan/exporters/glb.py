"""GLB / glTF 2.0 导出器（手写，零依赖）。

GLB 是"一个文件搞定"的现代三维格式：Windows 3D 查看器、Blender、Three.js、
Babylon、Unity、UE、SketchUp 2023+、Rhino 7+ 都能直接打开。

实现要点：
    * 纯手写二进制 glTF 2.0（JSON chunk + BIN chunk），不依赖任何库。
    * 按材质拆成多个 primitive，带 PBR 材质颜色，导入后能直接看出墙/门/窗/地面。
    * 法线按"位置 + 平面法向"去重，因此墙面是硬边（flat shading），不会糊成一片。
    * glTF 规范要求 Y 轴向上，本模块默认做 Z-up → Y-up 转换。
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from ..mesh import MATERIAL_COLORS, Mesh

MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMP_FLOAT = 5126
COMP_UINT = 5125
COMP_USHORT = 5123
TARGET_ARRAY = 34962
TARGET_ELEMENT = 34963


def _align4(b: bytes, pad: bytes = b"\x00") -> bytes:
    rem = len(b) % 4
    return b if rem == 0 else b + pad * (4 - rem)


def _material_json(name: str) -> dict:
    r, g, b, a = MATERIAL_COLORS.get(name, (0.8, 0.8, 0.8, 1.0))
    mat: dict = {
        "name": name,
        "pbrMetallicRoughness": {
            "baseColorFactor": [r, g, b, a],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.85,
        },
    }
    if a < 0.999:
        mat["alphaMode"] = "BLEND"
    if "window" in name:
        mat["doubleSided"] = True
    return mat


def _convert_axis(
    pts: np.ndarray, up_axis: str
) -> np.ndarray:
    """Z-up(米) → 目标上轴。"""
    if up_axis == "z":
        return pts
    if up_axis == "y":
        # glTF: (x, y, z)_zup  ->  (x, z, -y)_yup
        return np.stack([pts[:, 0], pts[:, 2], -pts[:, 1]], axis=1)
    raise ValueError(f"unsupported up_axis {up_axis!r}")


def export_glb(
    mesh: Mesh,
    path: str | Path,
    *,
    name: str = "scan2plan",
    up_axis: str = "y",
    extras: dict | None = None,
) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    verts = np.array(mesh.vertices, dtype=np.float64)
    if len(verts) == 0:
        raise ValueError("mesh is empty")

    # ---- 计算每个面的法向（平面法向，保持硬边）
    tri = np.array(mesh.faces, dtype=np.int64)
    p0 = verts[tri[:, 0]]
    p1 = verts[tri[:, 1]]
    p2 = verts[tri[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    nlen = np.linalg.norm(fn, axis=1, keepdims=True)
    nlen[nlen < 1e-12] = 1.0
    fn = fn / nlen

    # ---- 按材质分组
    by_mat: dict[str, list[int]] = {}
    for fi, mat in enumerate(mesh.face_materials):
        by_mat.setdefault(mat, []).append(fi)

    bin_parts: list[bytes] = []
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    materials: list[dict] = []
    primitives: list[dict] = []
    bin_offset = 0

    def add_view(data: bytes, target: int) -> int:
        nonlocal bin_offset
        padded = _align4(data)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": bin_offset,
                "byteLength": len(padded),
                "target": target,
            }
        )
        bin_parts.append(padded)
        bin_offset += len(padded)
        return len(buffer_views) - 1

    mat_order = sorted(by_mat.keys())
    for mi, mat in enumerate(mat_order):
        fids = by_mat[mat]
        # 位置 + 法向去重
        key_to_idx: dict[tuple, int] = {}
        positions: list[tuple[float, float, float]] = []
        normals: list[tuple[float, float, float]] = []
        indices: list[int] = []
        for fi in fids:
            a, b, c = mesh.faces[fi]
            n = fn[fi]
            for vi in (a, b, c):
                key = (
                    round(mesh.vertices[vi][0], 6),
                    round(mesh.vertices[vi][1], 6),
                    round(mesh.vertices[vi][2], 6),
                    round(float(n[0]), 4),
                    round(float(n[1]), 4),
                    round(float(n[2]), 4),
                )
                idx = key_to_idx.get(key)
                if idx is None:
                    idx = len(positions)
                    key_to_idx[key] = idx
                    positions.append(mesh.vertices[vi])
                    normals.append((float(n[0]), float(n[1]), float(n[2])))
                indices.append(idx)
        if not indices:
            continue

        pos_arr = _convert_axis(np.array(positions, dtype=np.float64), up_axis)
        nrm_arr = _convert_axis(np.array(normals, dtype=np.float64), up_axis)
        # 归一化法向（轴变换可能带负号，方向仍正确）
        nl = np.linalg.norm(nrm_arr, axis=1, keepdims=True)
        nl[nl < 1e-12] = 1.0
        nrm_arr = nrm_arr / nl

        # POSITION
        pos_bytes = pos_arr.astype(np.float32).tobytes()
        pos_view = add_view(pos_bytes, TARGET_ARRAY)
        accessors.append(
            {
                "bufferView": pos_view,
                "componentType": COMP_FLOAT,
                "count": len(pos_arr),
                "type": "VEC3",
                "min": [float(x) for x in pos_arr.min(axis=0)],
                "max": [float(x) for x in pos_arr.max(axis=0)],
            }
        )
        pos_acc = len(accessors) - 1

        # NORMAL
        nrm_view = add_view(nrm_arr.astype(np.float32).tobytes(), TARGET_ARRAY)
        accessors.append(
            {
                "bufferView": nrm_view,
                "componentType": COMP_FLOAT,
                "count": len(nrm_arr),
                "type": "VEC3",
            }
        )
        nrm_acc = len(accessors) - 1

        # INDICES
        if len(positions) <= 65535:
            idx_arr = np.array(indices, dtype=np.uint16)
            comp = COMP_USHORT
        else:
            idx_arr = np.array(indices, dtype=np.uint32)
            comp = COMP_UINT
        idx_bytes = _align4(idx_arr.tobytes())
        idx_view = add_view(idx_bytes, TARGET_ELEMENT)
        accessors.append(
            {
                "bufferView": idx_view,
                "componentType": comp,
                "count": len(idx_arr),
                "type": "SCALAR",
            }
        )
        idx_acc = len(accessors) - 1

        materials.append(_material_json(mat))
        primitives.append(
            {
                "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc},
                "indices": idx_acc,
                "material": mi,
                "mode": 4,  # TRIANGLES
            }
        )

    bin_blob = b"".join(bin_parts)
    bin_blob = _align4(bin_blob, b"\x00")

    gltf: dict = {
        "asset": {
            "version": "2.0",
            "generator": "scan2plan 0.1 (hand-written glTF writer)",
        },
        "scene": 0,
        "scenes": [{"nodes": [0], "name": name}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": primitives}],
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_blob)}],
    }
    if extras:
        gltf["asset"]["extras"] = extras

    json_bytes = _align4(json.dumps(gltf, ensure_ascii=False).encode("utf-8"), b" ")
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)

    out = bytearray()
    out += struct.pack("<III", MAGIC, 2, total)
    out += struct.pack("<II", len(json_bytes), CHUNK_JSON)
    out += json_bytes
    out += struct.pack("<II", len(bin_blob), CHUNK_BIN)
    out += bin_blob
    path.write_bytes(bytes(out))

    return {
        "format": "glb",
        "path": str(path),
        "bytes": len(out),
        "primitives": len(primitives),
        "materials": mat_order,
        "triangles": sum(a["count"] for a in accessors if a["type"] == "SCALAR") // 3,
        "up_axis": up_axis,
    }


def validate_glb(path: str | Path) -> dict:
    """解析 GLB 头部与 JSON chunk，校验结构自洽（不依赖第三方库）。"""
    raw = Path(path).read_bytes()
    problems: list[str] = []
    if len(raw) < 20:
        return {"ok": False, "problems": ["文件太短"], "bytes": len(raw)}
    magic, version, total = struct.unpack("<III", raw[:12])
    if magic != MAGIC:
        problems.append("魔数不是 glTF")
    if version != 2:
        problems.append(f"glTF 版本不是 2（{version}）")
    if total != len(raw):
        problems.append(f"头部声明长度 {total} 与实际 {len(raw)} 不符")
    off = 12
    json_len, json_type = struct.unpack("<II", raw[off : off + 8])
    if json_type != CHUNK_JSON:
        problems.append("第一个 chunk 不是 JSON")
    off += 8
    try:
        gltf = json.loads(raw[off : off + json_len].decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "problems": [f"JSON 解析失败: {exc}"], "bytes": len(raw)}
    off += json_len
    bin_len = 0
    if off < len(raw):
        bin_len, bin_type = struct.unpack("<II", raw[off : off + 8])
        if bin_type != CHUNK_BIN:
            problems.append("第二个 chunk 不是 BIN")
        off += 8
        if off + bin_len > len(raw):
            problems.append("BIN chunk 越界")

    buffers = gltf.get("buffers", [])
    if not buffers:
        problems.append("缺少 buffers")
    else:
        declared = buffers[0].get("byteLength", 0)
        if declared > bin_len:
            problems.append(f"buffer byteLength {declared} > BIN chunk {bin_len}")
    prims = gltf.get("meshes", [{}])[0].get("primitives", [])
    if not prims:
        problems.append("没有 primitive")
    # 只有被 primitive 当作 POSITION 引用的 accessor 才强制 min/max
    position_accessors: set[int] = set()
    for p in prims:
        attrs = p.get("attributes", {})
        if "POSITION" not in attrs:
            problems.append("primitive 缺 POSITION")
        else:
            position_accessors.add(int(attrs["POSITION"]))
        if "indices" not in p:
            problems.append("primitive 缺 indices")
    accessors = gltf.get("accessors", [])
    for ai in position_accessors:
        if ai >= len(accessors):
            problems.append(f"POSITION accessor#{ai} 越界")
            continue
        acc = accessors[ai]
        if "min" not in acc or "max" not in acc:
            problems.append(f"POSITION accessor#{ai} 缺 min/max（规范要求）")
        if acc.get("type") != "VEC3":
            problems.append(f"POSITION accessor#{ai} 不是 VEC3")
    for i, acc in enumerate(accessors):
        if acc.get("count", 0) <= 0:
            problems.append(f"accessor#{i} count 非法")
        if int(acc.get("bufferView", -1)) >= len(gltf.get("bufferViews", [])):
            problems.append(f"accessor#{i} bufferView 越界")
    return {
        "ok": not problems,
        "problems": problems,
        "bytes": len(raw),
        "version": version,
        "meshes": len(gltf.get("meshes", [])),
        "primitives": len(prims),
        "materials": [m.get("name") for m in gltf.get("materials", [])],
        "accessors": len(gltf.get("accessors", [])),
        "buffer_views": len(gltf.get("bufferViews", [])),
        "bin_bytes": bin_len,
    }
