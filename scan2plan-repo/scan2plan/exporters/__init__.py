"""导出器集合。

    dxf   AutoCAD R12 DXF（施工图样式：图层、填充、门弧、窗符号、尺寸标注）
    obj   Wavefront OBJ + MTL（兼容性最广）
    glb   glTF 2.0 二进制（现代通用格式，Y-up 规范）
    ifc   IFC4（BIM，Revit / Archicad / Navisworks 可直接打开）
    fbx   FBX ASCII 7.4（3ds Max / Maya / UE / Blender）
"""

from __future__ import annotations

from .dxf import DxfOptions, export_floorplan_dxf, validate_dxf
from .glb import export_glb, validate_glb
from .obj import export_obj, validate_obj

__all__ = [
    "DxfOptions",
    "export_floorplan_dxf",
    "validate_dxf",
    "export_obj",
    "validate_obj",
    "export_glb",
    "validate_glb",
]


def _lazy(name: str):
    """IFC / FBX 体积较大，按需导入。"""

    def _get():
        if name == "ifc":
            from . import ifc

            return ifc
        from . import fbx

        return fbx

    return _get
