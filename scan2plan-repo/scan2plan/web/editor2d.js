// 2D 平面图编辑器：渲染 + 命中测试 + 交互
// 只负责「画」和「把用户操作翻译成 op」，几何计算全部在 Python 端。

const COLORS = {
  bg: '#14161a',
  grid: 'rgba(255,255,255,0.035)',
  gridMajor: 'rgba(255,255,255,0.075)',
  wall: '#d8dde6',
  wallEdge: '#8d97a8',
  wallExt: '#e6ebf3',
  wallSel: '#4da3ff',
  roomFill: 'rgba(77,163,255,0.085)',
  roomStroke: 'rgba(77,163,255,0.42)',
  roomSel: 'rgba(77,163,255,0.22)',
  door: '#ffb454',
  window: '#63d2c0',
  passage: '#8d97a8',
  vertex: '#4da3ff',
  vertexFill: '#0f1620',
  dim: '#8f9aab',
  text: '#cfd6e0',
  origin: '#ff6b6b',
};

export class Editor2D {
  constructor(canvas, callbacks = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.cb = callbacks;
    this.derived = null;
    this.mode = 'select';
    this.options = { dims: true, rooms: true, grid: true, ortho: true };

    // 视图
    this.scale = 60;        // px per meter
    this.cx = 5;            // 视图中心（米）
    this.cy = 4;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);

    // 交互状态
    this.selection = null;   // {type:'wall'|'vertex'|'opening'|'room', id/ index}
    this.hover = null;
    this.drag = null;
    this.panning = false;
    this.spaceDown = false;
    this.pendingWall = null; // 画墙时的第一点
    this.mouse = { x: 0, y: 0, wx: 0, wy: 0 };

    this._bind();
    this._resize();
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(canvas.parentElement);
  }

  // ---------------------------------------------------------------- 数据

  setDerived(derived) {
    this.derived = derived;
    this.draw();
  }

  setMode(mode) {
    this.mode = mode;
    this.pendingWall = null;
    this.canvas.style.cursor = mode === 'select' ? 'default' : 'crosshair';
    this.draw();
  }

  setOptions(opts) {
    Object.assign(this.options, opts);
    this.draw();
  }

  setSelection(sel) {
    this.selection = sel;
    this.draw();
  }

  // ---------------------------------------------------------------- 坐标

  _resize() {
    const el = this.canvas.parentElement;
    const w = el.clientWidth || 800;
    const h = el.clientHeight || 600;
    this.w = w;
    this.h = h;
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
    this.draw();
  }

  worldToScreen(x, y) {
    return [this.w / 2 + (x - this.cx) * this.scale, this.h / 2 - (y - this.cy) * this.scale];
  }

  screenToWorld(px, py) {
    return [(px - this.w / 2) / this.scale + this.cx, -((py - this.h / 2) / this.scale) + this.cy];
  }

  fitView() {
    const d = this.derived;
    if (!d) return;
    let pts = [];
    (d.walls || []).forEach(w => pts.push(w.a, w.b));
    (d.rooms || []).forEach(r => pts.push(...r.polygon));
    if (!pts.length) return;
    let minx = 1e9, miny = 1e9, maxx = -1e9, maxy = -1e9;
    pts.forEach(([x, y]) => {
      minx = Math.min(minx, x); maxx = Math.max(maxx, x);
      miny = Math.min(miny, y); maxy = Math.max(maxy, y);
    });
    const w = Math.max(maxx - minx, 1), h = Math.max(maxy - miny, 1);
    this.cx = (minx + maxx) / 2;
    this.cy = (miny + maxy) / 2;
    this.scale = Math.min((this.w - 80) / w, (this.h - 80) / h);
    this.scale = Math.max(4, Math.min(this.scale, 400));
    this.draw();
    this.cb.onViewChange && this.cb.onViewChange(this.scale);
  }

  // ---------------------------------------------------------------- 事件

  _bind() {
    const c = this.canvas;
    c.addEventListener('pointerdown', e => this._down(e));
    c.addEventListener('pointermove', e => this._move(e));
    c.addEventListener('pointerup', e => this._up(e));
    c.addEventListener('pointercancel', e => this._up(e));
    c.addEventListener('wheel', e => this._wheel(e), { passive: false });
    c.addEventListener('pointerleave', () => { this.hover = null; this.draw(); });
    window.addEventListener('keydown', e => {
      if (e.code === 'Space') { this.spaceDown = true; }
    });
    window.addEventListener('keyup', e => {
      if (e.code === 'Space') { this.spaceDown = false; }
    });
    c.addEventListener('contextmenu', e => e.preventDefault());
  }

  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  _wheel(e) {
    e.preventDefault();
    const [px, py] = this._pos(e);
    const before = this.screenToWorld(px, py);
    const k = Math.exp(-e.deltaY * 0.0016);
    this.scale = Math.max(3, Math.min(600, this.scale * k));
    const after = this.screenToWorld(px, py);
    this.cx += before[0] - after[0];
    this.cy += before[1] - after[1];
    this.draw();
    this.cb.onViewChange && this.cb.onViewChange(this.scale);
  }

  _down(e) {
    this.canvas.setPointerCapture(e.pointerId);
    const [px, py] = this._pos(e);
    const [wx, wy] = this.screenToWorld(px, py);

    if (e.button === 1 || e.button === 2 || this.spaceDown) {
      this.panning = { px, py, cx: this.cx, cy: this.cy };
      return;
    }

    if (this.mode === 'wall') {
      const p = this._snapPoint(wx, wy);
      if (!this.pendingWall) {
        this.pendingWall = p;
        this.draw();
      } else {
        const q = this.pendingWall;
        if (Math.hypot(p[0] - q[0], p[1] - q[1]) > 0.05) {
          this.cb.onOp && this.cb.onOp({
            op: 'add_wall',
            a: [q[0], q[1]], b: [p[0], p[1]],
            thickness: 0.12, kind: 'interior',
          });
        }
        this.pendingWall = p;
        this.draw();
      }
      return;
    }

    const hit = this._hitTest(px, py);

    if ((this.mode === 'door' || this.mode === 'window') && hit && hit.type === 'wall') {
      const w = hit.wall;
      // 计算点击位置沿墙的参数
      const t = projectParam([wx, wy], w.a, w.b);
      const offset = t * w.length;
      this.cb.onOp && this.cb.onOp({
        op: 'add_opening', wall: w.id, kind: this.mode,
        offset: Math.max(0.3, Math.min(w.length - 0.3, offset)),
      });
      return;
    }

    if (!hit) {
      this.selection = null;
      this.cb.onSelect && this.cb.onSelect(null);
      this.draw();
      return;
    }

    this.selection = hit;
    this.cb.onSelect && this.cb.onSelect(hit);
    this.draw();

    if (this.mode !== 'select') return;

    if (hit.type === 'vertex') {
      this.drag = { kind: 'vertex', id: hit.id, from: [hit.p[0], hit.p[1]], cur: [hit.p[0], hit.p[1]] };
      this.canvas.style.cursor = 'grabbing';
    } else if (hit.type === 'opening') {
      this.drag = { kind: 'opening', id: hit.id, from: [wx, wy], cur: [wx, wy], wall: hit.wall };
      this.canvas.style.cursor = 'ew-resize';
    } else if (hit.type === 'wall') {
      this.drag = { kind: 'wall', id: hit.id, from: [wx, wy], cur: [wx, wy] };
      this.canvas.style.cursor = 'move';
    }
  }

  _move(e) {
    const [px, py] = this._pos(e);
    const [wx, wy] = this.screenToWorld(px, py);
    this.mouse = { x: px, y: py, wx, wy };
    this.cb.onCursor && this.cb.onCursor(wx, wy);

    if (this.panning) {
      this.cx = this.panning.cx - (px - this.panning.px) / this.scale;
      this.cy = this.panning.cy + (py - this.panning.py) / this.scale;
      this.draw();
      return;
    }

    if (this.drag) {
      const p = this._snapPoint(wx, wy);
      this.drag.cur = p;
      if (this.drag.kind === 'opening') {
        const w = this.derived.walls.find(x => x.id === this.drag.wall);
        if (w) {
          const t = projectParam(p, w.a, w.b);
          const off = Math.max(0.1, Math.min(1, t)) * w.length;
          this.drag.offset = off;
        }
      }
      this.draw();
      return;
    }

    const hit = this._hitTest(px, py);
    const key = hit ? `${hit.type}:${hit.id ?? hit.index}` : null;
    const oldKey = this.hover ? `${this.hover.type}:${this.hover.id ?? this.hover.index}` : null;
    if (key !== oldKey) {
      this.hover = hit;
      this.canvas.style.cursor =
        this.spaceDown ? 'grab' :
        hit ? (hit.type === 'vertex' ? 'grab' : hit.type === 'opening' ? 'ew-resize' : 'pointer') :
        this.mode === 'wall' ? 'crosshair' : 'default';
      this.draw();
    }
  }

  _up(e) {
    if (this.canvas.hasPointerCapture && this.canvas.hasPointerCapture(e.pointerId)) {
      this.canvas.releasePointerCapture(e.pointerId);
    }
    this.panning = false;
    const d = this.drag;
    this.drag = null;
    this.canvas.style.cursor = 'default';
    if (!d) { this.draw(); return; }

    const [wx, wy] = this.screenToWorld(...this._pos(e));
    const dist = Math.hypot((d.cur?.[0] ?? wx) - d.from[0], (d.cur?.[1] ?? wy) - d.from[1]);

    if (d.kind === 'vertex' && dist > 1e-4) {
      this.cb.onOp && this.cb.onOp({ op: 'move_vertex', id: d.id, xy: d.cur });
    } else if (d.kind === 'wall' && dist > 1e-4) {
      const delta = [d.cur[0] - d.from[0], d.cur[1] - d.from[1]];
      this.cb.onOp && this.cb.onOp({ op: 'move_wall', id: d.id, delta });
    } else if (d.kind === 'opening' && d.offset != null) {
      this.cb.onOp && this.cb.onOp({ op: 'move_opening', id: d.id, offset: d.offset });
    }
    this.draw();
  }

  // ---------------------------------------------------------------- 吸附

  _snapPoint(wx, wy) {
    const d = this.derived;
    const tolWorld = 12 / this.scale;

    // 顶点吸附优先
    if (d && d.vertices) {
      let best = null, bd = tolWorld;
      for (const v of d.vertices) {
        const dd = Math.hypot(v.xy[0] - wx, v.xy[1] - wy);
        if (dd < bd) { bd = dd; best = v.xy; }
      }
      if (best) return [best[0], best[1]];
    }

    // 画墙时正交吸附
    if (this.mode === 'wall' && this.pendingWall && this.options.ortho) {
      const [ax, ay] = this.pendingWall;
      const dx = wx - ax, dy = wy - ay;
      if (Math.abs(dx) > Math.abs(dy)) return [wx, ay];
      return [ax, wy];
    }

    // 墙角/门窗端点正交吸附：把坐标吸附到 1cm 网格
    if (this.options.grid) {
      return [Math.round(wx * 100) / 100, Math.round(wy * 100) / 100];
    }
    return [wx, wy];
  }

  // ---------------------------------------------------------------- 命中

  _hitTest(px, py) {
    const d = this.derived;
    if (!d) return null;
    const tolPx = 9;

    // 1) 顶点
    for (const v of d.vertices || []) {
      const [sx, sy] = this.worldToScreen(v.xy[0], v.xy[1]);
      if (Math.hypot(sx - px, sy - py) <= tolPx) {
        return { type: 'vertex', id: v.id, p: v.xy };
      }
    }
    // 2) 门窗
    for (const o of d.openings || []) {
      if (o.quad && o.quad.length >= 3 && pointInPoly([this.mouse.wx, this.mouse.wy], o.quad)) {
        return { type: 'opening', id: o.id, wall: o.wall, opening: o };
      }
    }
    // 3) 墙体
    for (const w of d.walls || []) {
      if (w.body && pointInPoly([this.mouse.wx, this.mouse.wy], w.body.polygon)) {
        return { type: 'wall', id: w.id, wall: w };
      }
    }
    // 4) 房间
    for (const r of d.rooms || []) {
      if (pointInPoly([this.mouse.wx, this.mouse.wy], r.polygon)) {
        return { type: 'room', index: r.index, room: r };
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- 绘制

  draw() {
    const ctx = this.ctx;
    const d = this.derived;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, this.w, this.h);

    if (this.options.grid) this._drawGrid();
    if (!d) return;

    if (this.options.rooms) this._drawRooms(d);
    this._drawWalls(d);
    this._drawOpenings(d);
    if (this.options.dims) this._drawDims(d);
    this._drawVertices(d);
    this._drawSelection(d);
    this._drawPreview();
    this._drawScaleBar();
  }

  _drawGrid() {
    const ctx = this.ctx;
    const s = this.scale;
    let step = 1;
    while (step * s < 26) step *= 2;
    while (step * s > 120) step /= 2;
    const [x0, y1] = this.screenToWorld(0, 0);
    const [x1, y0] = this.screenToWorld(this.w, this.h);
    ctx.lineWidth = 1;
    ctx.strokeStyle = COLORS.grid;
    ctx.beginPath();
    for (let x = Math.ceil(x0 / step) * step; x <= x1; x += step) {
      const [sx] = this.worldToScreen(x, 0);
      ctx.moveTo(sx, 0); ctx.lineTo(sx, this.h);
    }
    for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
      const [, sy] = this.worldToScreen(0, y);
      ctx.moveTo(0, sy); ctx.lineTo(this.w, sy);
    }
    ctx.stroke();

    // 原点十字
    const [ox, oy] = this.worldToScreen(0, 0);
    ctx.strokeStyle = 'rgba(255,107,107,0.5)';
    ctx.beginPath();
    ctx.moveTo(ox - 8, oy); ctx.lineTo(ox + 8, oy);
    ctx.moveTo(ox, oy - 8); ctx.lineTo(ox, oy + 8);
    ctx.stroke();
  }

  _drawRooms(d) {
    const ctx = this.ctx;
    for (const r of d.rooms || []) {
      if (!r.polygon || r.polygon.length < 3) continue;
      const sel = this.selection && this.selection.type === 'room' && this.selection.index === r.index;
      ctx.beginPath();
      r.polygon.forEach(([x, y], i) => {
        const [sx, sy] = this.worldToScreen(x, y);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.closePath();
      ctx.fillStyle = sel ? COLORS.roomSel : COLORS.roomFill;
      ctx.fill();
      ctx.strokeStyle = COLORS.roomStroke;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      // 房间名 + 面积
      const [cx, cy] = this.worldToScreen(r.centroid[0], r.centroid[1]);
      ctx.textAlign = 'center';
      ctx.fillStyle = sel ? '#eaf3ff' : COLORS.text;
      ctx.font = '600 13px "Segoe UI", "Microsoft YaHei", sans-serif';
      ctx.fillText(r.label, cx, cy - 3);
      ctx.font = '11px "Segoe UI", sans-serif';
      ctx.fillStyle = '#8ea3bb';
      ctx.fillText(r.net_area.toFixed(2) + ' m²', cx, cy + 12);
    }
  }

  _drawWalls(d) {
    const ctx = this.ctx;
    for (const w of d.walls || []) {
      const poly = w.body ? w.body.polygon : null;
      if (!poly || poly.length < 3) continue;
      ctx.beginPath();
      poly.forEach(([x, y], i) => {
        const [sx, sy] = this.worldToScreen(x, y);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.closePath();
      const isHover = this.hover && this.hover.type === 'wall' && this.hover.id === w.id;
      const isSel = this.selection && this.selection.type === 'wall' && this.selection.id === w.id;
      ctx.fillStyle = isSel ? '#2f6fa8' : isHover ? '#c9d4e3' : (w.kind === 'exterior' ? COLORS.wallExt : COLORS.wall);
      ctx.fill();
      ctx.strokeStyle = isSel ? COLORS.wallSel : COLORS.wallEdge;
      ctx.lineWidth = isSel ? 2 : 1;
      ctx.stroke();

      // 中线（细虚线，重建来源可核对）
      const [ax, ay] = this.worldToScreen(w.a[0], w.a[1]);
      const [bx, by] = this.worldToScreen(w.b[0], w.b[1]);
      ctx.beginPath();
      ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
      ctx.strokeStyle = 'rgba(255,180,84,0.25)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  _drawOpenings(d) {
    const ctx = this.ctx;
    for (const o of d.openings || []) {
      const q = o.quad;
      if (!q || q.length < 3) continue;
      ctx.beginPath();
      q.forEach(([x, y], i) => {
        const [sx, sy] = this.worldToScreen(x, y);
        i ? ctx.lineTo(sx, sy) : ctx.moveTo(sx, sy);
      });
      ctx.closePath();
      const col = o.kind === 'door' ? COLORS.door : o.kind === 'window' ? COLORS.window : COLORS.passage;
      ctx.fillStyle = o.kind === 'window' ? 'rgba(99,210,192,0.55)' : o.kind === 'door' ? 'rgba(255,180,84,0.5)' : 'rgba(141,151,168,0.4)';
      ctx.fill();
      const isSel = this.selection && this.selection.type === 'opening' && this.selection.id === o.id;
      const isHover = this.hover && this.hover.type === 'opening' && this.hover.id === o.id;
      ctx.strokeStyle = isSel ? '#ffffff' : isHover ? col : 'rgba(0,0,0,0.35)';
      ctx.lineWidth = isSel ? 2 : 1;
      ctx.stroke();

      // 门的开启弧
      if (o.kind === 'door' && q.length === 4) {
        const w = d.walls.find(x => x.id === o.wall);
        if (w) this._drawDoorArc(ctx, w, o);
      }
    }
  }

  _drawDoorArc(ctx, w, o) {
    const h0 = o.quad[0], h1 = o.quad[1];
    // 用墙中线定位铰链：取洞口在墙上的两端
    const len = w.length;
    const t0 = Math.max(0, (o.offset - o.width / 2) / len);
    const t1 = Math.min(1, (o.offset + o.width / 2) / len);
    const p0 = [w.a[0] + (w.b[0] - w.a[0]) * t0, w.a[1] + (w.b[1] - w.a[1]) * t0];
    const p1 = [w.a[0] + (w.b[0] - w.a[0]) * t1, w.a[1] + (w.b[1] - w.a[1]) * t1];
    const hinge = o.swing === 'right' ? p1 : p0;
    const dir = [w.b[0] - w.a[0], w.b[1] - w.a[1]];
    const dl = Math.hypot(dir[0], dir[1]) || 1;
    const ux = dir[0] / dl, uy = dir[1] / dl;
    const nx = -uy, ny = ux;
    const sgn = o.swing_in ? 1 : -1;
    const leafEnd = [hinge[0] + nx * sgn * o.width, hinge[1] + ny * sgn * o.width];

    const [hx, hy] = this.worldToScreen(hinge[0], hinge[1]);
    const [lx, ly] = this.worldToScreen(leafEnd[0], leafEnd[1]);
    const r = Math.hypot(lx - hx, ly - hy);
    const a0 = Math.atan2(hy - hy, hx - hx);
    const aLeaf = Math.atan2(ly - hy, lx - hx);
    const other = o.swing === 'right' ? p0 : p1;
    const [ox, oy] = this.worldToScreen(other[0], other[1]);
    const aWall = Math.atan2(oy - hy, ox - hx);

    ctx.beginPath();
    ctx.moveTo(hx, hy);
    ctx.lineTo(lx, ly);
    let delta = (aWall - aLeaf + Math.PI * 2) % (Math.PI * 2);
    if (delta > Math.PI) ctx.arc(hx, hy, r, aWall, aLeaf, false);
    else ctx.arc(hx, hy, r, aLeaf, aWall, false);
    ctx.strokeStyle = 'rgba(255,180,84,0.55)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  _drawDims(d) {
    const ctx = this.ctx;
    if (this.scale < 12) return;
    ctx.font = '11px "Segoe UI", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const w of d.walls || []) {
      const mx = (w.a[0] + w.b[0]) / 2;
      const my = (w.a[1] + w.b[1]) / 2;
      const [sx, sy] = this.worldToScreen(mx, my);
      const ang = Math.atan2(
        -(w.b[1] - w.a[1]), w.b[0] - w.a[0]
      );
      let deg = ang * 180 / Math.PI;
      if (deg > 90 || deg < -90) deg += 180;
      const txt = w.length.toFixed(w.length < 10 ? 3 : 2) + 'm';
      const off = Math.max(9, w.thickness * this.scale * 0.5 + 9);
      const nx = Math.sin(ang), ny = -Math.cos(ang);
      ctx.save();
      ctx.translate(sx + nx * off, sy + ny * off);
      ctx.rotate(ang);
      const tw = ctx.measureText(txt).width;
      ctx.fillStyle = 'rgba(20,22,26,0.82)';
      ctx.fillRect(-tw / 2 - 3, -7, tw + 6, 14);
      ctx.fillStyle = COLORS.dim;
      ctx.fillText(txt, 0, 0);
      ctx.restore();
    }
  }

  _drawVertices(d) {
    if (this.mode !== 'select' && this.mode !== 'wall') return;
    const ctx = this.ctx;
    const r = 3.2;
    for (const v of d.vertices || []) {
      const [sx, sy] = this.worldToScreen(v.xy[0], v.xy[1]);
      if (sx < -20 || sy < -20 || sx > this.w + 20 || sy > this.h + 20) continue;
      const isSel = this.selection && this.selection.type === 'vertex' && this.selection.id === v.id;
      const isHover = this.hover && this.hover.type === 'vertex' && this.hover.id === v.id;
      ctx.beginPath();
      ctx.rect(sx - r, sy - r, r * 2, r * 2);
      ctx.fillStyle = isSel || isHover ? COLORS.vertex : COLORS.vertexFill;
      ctx.fill();
      ctx.strokeStyle = COLORS.vertex;
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }
  }

  _drawSelection(d) {
    const s = this.selection;
    if (!s) return;
    const ctx = this.ctx;
    if (s.type === 'wall') {
      const w = d.walls.find(x => x.id === s.id);
      if (!w) return;
      const [ax, ay] = this.worldToScreen(w.a[0], w.a[1]);
      const [bx, by] = this.worldToScreen(w.b[0], w.b[1]);
      ctx.strokeStyle = COLORS.wallSel;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
      ctx.fillStyle = COLORS.wallSel;
      [ [ax, ay], [bx, by] ].forEach(([x, y]) => { ctx.beginPath(); ctx.arc(x, y, 4, 0, 7); ctx.fill(); });
    }
  }

  _drawPreview() {
    const ctx = this.ctx;
    // 正在画墙
    if (this.mode === 'wall' && this.pendingWall) {
      const [ax, ay] = this.worldToScreen(this.pendingWall[0], this.pendingWall[1]);
      const p = this._snapPoint(this.mouse.wx, this.mouse.wy);
      const [bx, by] = this.worldToScreen(p[0], p[1]);
      ctx.strokeStyle = '#4da3ff';
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
      ctx.setLineDash([]);
      const L = Math.hypot(p[0] - this.pendingWall[0], p[1] - this.pendingWall[1]);
      ctx.fillStyle = '#9ecbff';
      ctx.font = '12px "Segoe UI", sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(L.toFixed(3) + ' m', bx + 10, by - 10);
    }
    // 拖动顶点/墙的幽灵
    if (this.drag) {
      const ctx2 = this.ctx;
      ctx2.fillStyle = 'rgba(77,163,255,0.85)';
      const [sx, sy] = this.worldToScreen(this.drag.cur[0], this.drag.cur[1]);
      ctx2.beginPath(); ctx2.arc(sx, sy, 5, 0, 7); ctx2.fill();
      const [fx, fy] = this.worldToScreen(this.drag.from[0], this.drag.from[1]);
      ctx2.strokeStyle = 'rgba(77,163,255,0.5)';
      ctx2.setLineDash([3, 3]);
      ctx2.beginPath(); ctx2.moveTo(fx, fy); ctx2.lineTo(sx, sy); ctx2.stroke();
      ctx2.setLineDash([]);
      const dx = this.drag.cur[0] - this.drag.from[0];
      const dy = this.drag.cur[1] - this.drag.from[1];
      if (this.drag.kind === 'vertex' && (Math.abs(dx) > 1e-4 || Math.abs(dy) > 1e-4)) {
        ctx2.fillStyle = '#9ecbff';
        ctx2.font = '12px "Segoe UI", sans-serif';
        ctx2.textAlign = 'left';
        ctx2.fillText(`Δx ${(dx * 1000).toFixed(0)}mm  Δy ${(dy * 1000).toFixed(0)}mm`, sx + 10, sy - 12);
      }
    }
  }

  _drawScaleBar() {
    const ctx = this.ctx;
    // 选一个好看的整数长度
    let target = 90 / this.scale;
    const nice = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50];
    let L = nice[0];
    for (const n of nice) { if (Math.abs(n - target) < Math.abs(L - target)) L = n; }
    const px = L * this.scale;
    const x0 = 16, y0 = this.h - 22;
    ctx.strokeStyle = '#8f9aab';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x0, y0); ctx.lineTo(x0 + px, y0);
    ctx.moveTo(x0, y0 - 4); ctx.lineTo(x0, y0 + 4);
    ctx.moveTo(x0 + px, y0 - 4); ctx.lineTo(x0 + px, y0 + 4);
    ctx.stroke();
    ctx.fillStyle = '#8f9aab';
    ctx.font = '11px "Segoe UI", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(L >= 1 ? `${L} m` : `${L * 1000} mm`, x0 + px / 2, y0 - 9);
  }
}

// ---------------------------------------------------------------- 工具

export function pointInPoly(p, poly) {
  let inside = false;
  const [x, y] = p;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

export function projectParam(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const L2 = dx * dx + dy * dy;
  if (L2 < 1e-12) return 0;
  return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2;
}
