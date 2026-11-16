"""本地 HTTP 服务：给 Web 编辑器提供几何内核 + 导出能力。

只用 Python 标准库，没有任何 Web 框架依赖。

接口：
    GET  /                        Web 编辑器
    GET  /api/sample?name=...      取内置样例（apartment / single / two / lshape）
    POST /api/derive  {project}   只重算派生几何
    POST /api/edit    {project, ops}   应用编辑操作，返回新的 project + 派生几何
    POST /api/export  {project, format, options}  导出文件，返回下载链接
    GET  /files/<name>             下载 build/ 目录里的导出文件
    GET  /api/health               健康检查
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .derived import derive
from .exporters import dxf as dxfmod
from .exporters import glb as glbmod
from .exporters import obj as objmod
from .model import Project
from .ops import OpError, apply_ops
from . import samples

WEB_DIR = Path(__file__).resolve().parent / "web"
BUILD_DIR = Path(__file__).resolve().parents[1] / "build"

SAMPLES = {
    "apartment": samples.demo_apartment,
    "single": samples.demo_single_room,
    "two": samples.demo_two_rooms,
    "lshape": samples.demo_L_room,
}


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "scan2plan/0.1"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 工具

    def log_message(self, fmt: str, *args) -> None:  # 安静一点
        if os.environ.get("SCAN2PLAN_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, _json_bytes(obj))

    def _error(self, msg: str, code: int = 400) -> None:
        self._json({"ok": False, "error": msg}, code)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        # 某些 HTTP 客户端（如 PowerShell 5 的 Invoke-RestMethod）不按 UTF-8 编码请求体，
        # 这里做一次宽容解码，避免中文项目名直接 500。
        text: str | None = None
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise OpError("请求体编码无法识别（应为 UTF-8）")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpError(f"请求 JSON 解析失败: {exc}") from exc

    # ------------------------------------------------------------ 路由

    def do_GET(self) -> None:
        try:
            self._route_get()
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(f"服务器内部错误: {exc}", 500)

    def do_POST(self) -> None:
        try:
            self._route_post()
        except OpError as exc:
            self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(f"服务器内部错误: {exc}", 500)

    def _route_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path == "/api/health":
            return self._json({"ok": True, "version": "0.1.0", "web": str(WEB_DIR)})
        if path == "/api/sample":
            name = (parse_qs(parsed.query).get("name") or ["apartment"])[0]
            factory = SAMPLES.get(name)
            if factory is None:
                return self._error(f"未知样例 {name!r}，可选: {sorted(SAMPLES)}")
            proj = factory()
            return self._json({"ok": True, "project": proj.to_dict(), "derived": derive(proj)})
        if path == "/api/formats":
            return self._json(
                {
                    "ok": True,
                    "formats": [
                        {"ext": "dxf", "label": "AutoCAD DXF", "note": "施工图样式，可直接编辑"},
                        {"ext": "ifc", "label": "IFC4 (BIM)", "note": "Revit/Archicad 可打开"},
                        {"ext": "glb", "label": "glTF 2.0 (.glb)", "note": "现代通用三维格式"},
                        {"ext": "obj", "label": "Wavefront OBJ", "note": "兼容性最广"},
                        {"ext": "fbx", "label": "FBX ASCII", "note": "3ds Max / Maya / UE"},
                        {"ext": "json", "label": "scan2plan 工程文件", "note": "可再次编辑"},
                    ],
                }
            )
        if path.startswith("/files/"):
            name = path[len("/files/") :]
            return self._serve_build(name)
        # 其余当静态文件
        return self._static(path.lstrip("/"))

    def _route_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/derive":
            body = self._read_json()
            proj = Project.from_dict(body["project"])
            return self._json({"ok": True, "derived": derive(proj)})
        if path == "/api/edit":
            body = self._read_json()
            proj = Project.from_dict(body["project"])
            ops = body.get("ops") or []
            apply_ops(proj, ops)
            return self._json(
                {
                    "ok": True,
                    "project": proj.to_dict(),
                    "derived": derive(proj),
                    "applied": len(ops),
                }
            )
        if path == "/api/export":
            body = self._read_json()
            return self._export(body)
        self._error(f"未知接口: {path}", 404)

    # ------------------------------------------------------------ 导出

    def _export(self, body: dict) -> None:
        proj = Project.from_dict(body["project"])
        fmt = str(body.get("format", "dxf")).lower().lstrip(".")
        options = body.get("options") or {}
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in (proj.name or "floorplan") if c.isalnum() or c in "-_") or "floorplan"
        fp = proj.active

        if fmt == "dxf":
            opt = dxfmod.DxfOptions(
                unit=str(options.get("unit", "mm")),
                wall_fill=bool(options.get("wallFill", True)),
                draw_dims=bool(options.get("dims", True)),
                dim_mode=str(options.get("dimMode", "rooms")),
                draw_axes=bool(options.get("axes", True)),
            )
            out = BUILD_DIR / f"{safe}.dxf"
            report = dxfmod.export_floorplan_dxf(fp, out, opt)
            check = dxfmod.validate_dxf(out)
        elif fmt == "ifc":
            from .exporters import ifc as ifcmod

            out = BUILD_DIR / f"{safe}.ifc"
            report = ifcmod.export_ifc(proj, out)
            check = ifcmod.validate_ifc(out)
        elif fmt == "obj":
            from .mesh import build_plan_mesh

            out = BUILD_DIR / f"{safe}.obj"
            report = objmod.export_obj(build_plan_mesh(fp, include_ceiling=bool(options.get("ceiling", False))), out, name=safe)
            check = objmod.validate_obj(out)
        elif fmt == "glb":
            from .mesh import build_plan_mesh

            out = BUILD_DIR / f"{safe}.glb"
            report = glbmod.export_glb(
                build_plan_mesh(fp, include_ceiling=bool(options.get("ceiling", False))),
                out,
                name=safe,
            )
            check = glbmod.validate_glb(out)
        elif fmt == "fbx":
            from .mesh import build_plan_mesh
            from .exporters import fbx as fbxmod

            out = BUILD_DIR / f"{safe}.fbx"
            report = fbxmod.export_fbx(build_plan_mesh(fp), out, name=safe)
            check = fbxmod.validate_fbx(out)
        elif fmt in ("json", "s2p"):
            out = BUILD_DIR / f"{safe}.s2p.json"
            out.write_text(
                json.dumps(proj.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            report = {"format": "json", "path": str(out), "bytes": out.stat().st_size}
            check = {"ok": True, "problems": []}
        else:
            raise OpError(f"不支持的导出格式: {fmt}")

        self._json(
            {
                "ok": True,
                "format": fmt,
                "report": report,
                "check": check,
                "file": out.name,
                "url": f"/files/{out.name}",
                "sizeHint": out.stat().st_size,
            }
        )

    # ------------------------------------------------------------ 静态资源

    def _static(self, rel: str) -> None:
        rel = rel.split("?")[0].split("#")[0]
        if not rel:
            rel = "index.html"
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._error("非法路径", 403)
        if not target.is_file():
            return self._error(f"文件不存在: {rel}", 404)
        ctype, _ = mimetypes.guess_type(str(target))
        if ctype is None:
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in (
            "application/javascript",
            "application/json",
        ):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _serve_build(self, name: str) -> None:
        target = (BUILD_DIR / name).resolve()
        try:
            target.relative_to(BUILD_DIR.resolve())
        except ValueError:
            return self._error("非法路径", 403)
        if not target.is_file():
            return self._error(f"文件不存在: {name}", 404)
        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header(
            "Content-Disposition", f'attachment; filename="{target.name.encode("ascii", "replace").decode()}"'
        )
        self.end_headers()
        try:
            self.wfile.write(target.read_bytes())
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print("=" * 62)
    print("  scan2plan Web 编辑器")
    print(f"  打开: {url}")
    print(f"  静态资源: {WEB_DIR}")
    print(f"  导出目录: {BUILD_DIR}")
    print("  Ctrl+C 停止")
    print("=" * 62)
    if open_browser:
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
