// scan2plan Web 编辑器主程序
import { Editor2D } from './editor2d.js';
import { Viewer3D } from './viewer3d.js';

const API = {
  async post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({ ok: false, error: '响应不是 JSON' }));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  },
  async get(path) {
    const r = await fetch(path);
    const j = await r.json().catch(() => ({ ok: false, error: '响应不是 JSON' }));
    if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  },
};

const state = {
  project: null,
  derived: null,
  undo: [],
  redo: [],
  selection: null,
  mode: 'select',
  busy: false,
};

let editor, viewer;
const $ = id => document.getElementById(id);

// ---------------------------------------------------------------- 启动

function boot() {
  editor = new Editor2D($('canvas2d'), {
    onOp: ops => applyOps(Array.isArray(ops) ? ops : [ops]),
    onSelect: sel => { state.selection = sel; renderSelection(); },
    onCursor: (x, y) => {
      $('cursorInfo').textContent = `x ${x.toFixed(3)}  y ${y.toFixed(3)}`;
    },
    onViewChange: s => { $('zoomInfo').textContent = Math.round(s / 60 * 100) + '%'; },
  });
  viewer = new Viewer3D($('canvas3d'));

  bindTopbar();
  bindKeys();
  bindSplitter();
  loadSample('apartment');
  setInterval(flushQueue, 60);
}

// ---------------------------------------------------------------- API 流

// 编辑请求串行化：拖拽时可能连发多个 op，避免乱序覆盖
let pendingOps = [];
let inflight = false;

function applyOps(ops) {
  if (!state.project || !ops.length) return;
  pendingOps.push(...ops);
  if (!inflight) flushQueue();
}

async function flushQueue() {
  if (inflight || !pendingOps.length || !state.project) return;
  inflight = true;
  const ops = pendingOps.splice(0, pendingOps.length);
  const snapshot = state.project;
  try {
    const res = await API.post('/api/edit', { project: state.project, ops });
    state.undo.push(JSON.stringify(snapshot));
    if (state.undo.length > 100) state.undo.shift();
    state.redo.length = 0;
    setState(res.project, res.derived);
    updateUndoButtons();
  } catch (err) {
    toast('操作失败：' + err.message, true);
    // 回滚到快照
    setState(snapshot, state.derived, { skipMesh: true });
    refresh(true);
  } finally {
    inflight = false;
    if (pendingOps.length) flushQueue();
  }
}

function setState(project, derived, opts = {}) {
  state.project = project;
  state.derived = derived;
  const plan = derived.plans[0];
  if (!opts.skipMesh) {
    editor.setDerived(plan);
    viewer.setMesh(plan.mesh);
    $('triInfo').textContent = plan.mesh
      ? `${plan.mesh.report.triangles} 三角面 · ${plan.mesh.report.objects} 构件`
      : '—';
  }
  renderPanels(plan);
  if (state.selection) refreshSelectionFromDerived();
}

async function refresh(silent = false) {
  if (!state.project) return;
  try {
    const res = await API.post('/api/derive', { project: state.project });
    setState(state.project, res.derived);
  } catch (err) {
    if (!silent) toast('刷新失败：' + err.message, true);
  }
}

async function loadSample(name) {
  try {
    const res = await API.get('/api/sample?name=' + encodeURIComponent(name));
    state.undo.length = 0;
    state.redo.length = 0;
    state.selection = null;
    setState(res.project, res.derived);
    editor.fitView();
    updateUndoButtons();
    renderSelection();
    toast('已载入样例：' + (res.project.name || name));
  } catch (err) {
    toast('载入失败：' + err.message, true);
  }
}

// ---------------------------------------------------------------- UI

function bindTopbar() {
  $('sampleSel').addEventListener('change', e => loadSample(e.target.value));

  $('btnUndo').addEventListener('click', undo);
  $('btnRedo').addEventListener('click', redo);

  document.querySelectorAll('.modes .mode').forEach(btn => {
    btn.addEventListener('click', () => setMode(btn.dataset.mode));
  });

  $('chkDims').addEventListener('change', e => editor.setOptions({ dims: e.target.checked }));
  $('chkRooms').addEventListener('change', e => editor.setOptions({ rooms: e.target.checked }));
  $('chkGrid').addEventListener('change', e => editor.setOptions({ grid: e.target.checked }));
  $('chkOrtho').addEventListener('change', e => editor.setOptions({ ortho: e.target.checked }));

  $('btnExportDxf').addEventListener('click', () => exportFormat('dxf'));
  $('btnExportIfc').addEventListener('click', () => exportFormat('ifc'));
  $('btnExportGlb').addEventListener('click', () => exportFormat('glb'));
  $('btnExportObj').addEventListener('click', () => exportFormat('obj'));
  $('btnExportFbx').addEventListener('click', () => exportFormat('fbx'));
  $('btnSaveProject').addEventListener('click', saveProject);

  $('btnLoad').addEventListener('click', () => $('fileInput').click());
  $('fileInput').addEventListener('change', async e => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const text = await f.text();
      const project = JSON.parse(text);
      const res = await API.post('/api/derive', { project });
      state.undo.length = 0; state.redo.length = 0; state.selection = null;
      setState(project, res.derived);
      editor.fitView();
      renderSelection();
      toast('已打开：' + f.name);
    } catch (err) {
      toast('打开失败：' + err.message, true);
    }
    e.target.value = '';
  });
}

function bindKeys() {
  window.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      e.shiftKey ? redo() : undo();
      return;
    }
    if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteSelection(); return; }
    if (e.key === 'Escape') { state.selection = null; editor.setSelection(null); renderSelection(); return; }
    const k = e.key.toLowerCase();
    if (k === 'v') setMode('select');
    if (k === 'w') setMode('wall');
    if (k === 'd') setMode('door');
    if (k === 'n') setMode('window');
    if (k === 'f') editor.fitView();
  });
}

function bindSplitter() {
  const sp = $('splitter');
  const v2 = $('viewport2d');
  const v3 = $('viewport3d');
  let dragging = false;
  sp.addEventListener('pointerdown', e => {
    dragging = true;
    sp.classList.add('drag');
    sp.setPointerCapture(e.pointerId);
  });
  window.addEventListener('pointermove', e => {
    if (!dragging) return;
    const layout = document.querySelector('.layout').getBoundingClientRect();
    const ratio = Math.max(0.2, Math.min(0.8, (e.clientX - layout.left) / layout.width));
    v2.style.flex = `0 0 ${ratio * 100}%`;
    v3.style.flex = '1 1 auto';
    editor._resize();
    viewer._resize();
    viewer.render();
  });
  window.addEventListener('pointerup', () => {
    dragging = false;
    sp.classList.remove('drag');
  });
}

function setMode(mode) {
  state.mode = mode;
  editor.setMode(mode);
  document.querySelectorAll('.modes .mode').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
}

function undo() {
  if (!state.undo.length) return toast('没有可撤销的操作');
  const snap = state.undo.pop();
  state.redo.push(JSON.stringify(state.project));
  const project = JSON.parse(snap);
  API.post('/api/derive', { project }).then(res => {
    setState(project, res.derived);
    updateUndoButtons();
    refresh(true);
  }).catch(err => toast('撤销失败：' + err.message, true));
}

function redo() {
  if (!state.redo.length) return toast('没有可重做的操作');
  const snap = state.redo.pop();
  state.undo.push(JSON.stringify(state.project));
  const project = JSON.parse(snap);
  API.post('/api/derive', { project }).then(res => {
    setState(project, res.derived);
    updateUndoButtons();
  }).catch(err => toast('重做失败：' + err.message, true));
}

function updateUndoButtons() {
  $('btnUndo').disabled = state.undo.length === 0;
  $('btnRedo').disabled = state.redo.length === 0;
}

// ---------------------------------------------------------------- 面板

function renderPanels(plan) {
  if (!plan) return;
  $('roomTotal').textContent = `${plan.rooms.length} 间 · 净 ${plan.stats.net_area_m2.toFixed(2)} m²`;
  const list = $('roomList');
  list.innerHTML = '';
  plan.rooms.forEach(r => {
    const row = document.createElement('div');
    row.className = 'room-row' + (state.selection && state.selection.type === 'room' && state.selection.index === r.index ? ' active' : '');
    const name = document.createElement('input');
    name.value = r.label;
    name.title = '点击重命名';
    name.addEventListener('change', () => {
      applyOps([{ op: 'rename_room', index: r.index, label: name.value.trim() || r.label }]);
    });
    name.addEventListener('click', e => e.stopPropagation());
    const area = document.createElement('span');
    area.className = 'area';
    area.textContent = r.net_area.toFixed(2) + ' m²';
    row.appendChild(name);
    row.appendChild(area);
    row.addEventListener('click', () => {
      state.selection = { type: 'room', index: r.index, room: r };
      editor.setSelection(state.selection);
      renderSelection();
    });
    list.appendChild(row);
  });

  const st = plan.stats;
  const rows = [
    ['房间数', st.rooms],
    ['净面积', st.net_area_m2.toFixed(2) + ' m²'],
    ['毛面积', st.gross_area_m2.toFixed(2) + ' m²'],
    ['墙体总长', st.wall_length_m.toFixed(2) + ' m'],
    ['墙面面积', st.wall_area_m2.toFixed(2) + ' m²'],
    ['墙 / 顶点', `${st.walls} / ${st.vertices}`],
    ['门 / 窗', `${st.doors} / ${st.windows}`],
    ['层高', plan.ceilingHeight.toFixed(2) + ' m'],
  ];
  $('statsTable').innerHTML = rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join('');

  if (plan.problems && plan.problems.length) {
    $('statsTable').innerHTML +=
      `<tr><td style="color:#ffb454">检查</td><td style="color:#ffb454;text-align:right">${plan.problems.length} 项警告</td></tr>`;
  }
}

function field(label, value, opts = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  const lab = document.createElement('label');
  lab.textContent = label;
  const inp = document.createElement('input');
  inp.type = opts.type || 'number';
  if (opts.step != null) inp.step = opts.step;
  inp.value = value;
  if (opts.min != null) inp.min = opts.min;
  if (opts.max != null) inp.max = opts.max;
  if (opts.options) {
    const sel = document.createElement('select');
    opts.options.forEach(([v, t]) => {
      const o = document.createElement('option');
      o.value = v; o.textContent = t;
      if (String(v) === String(value)) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', () => opts.onChange(sel.value));
    wrap.appendChild(lab); wrap.appendChild(sel);
    if (opts.unit) { const u = document.createElement('span'); u.className = 'unit'; u.textContent = opts.unit; wrap.appendChild(u); }
    return wrap;
  }
  inp.addEventListener('change', () => {
    const v = parseFloat(inp.value);
    if (!Number.isNaN(v)) opts.onChange(v);
  });
  wrap.appendChild(lab);
  wrap.appendChild(inp);
  if (opts.unit) {
    const u = document.createElement('span');
    u.className = 'unit';
    u.textContent = opts.unit;
    wrap.appendChild(u);
  }
  return wrap;
}

function button(text, cls, onClick) {
  const b = document.createElement('button');
  b.textContent = text;
  if (cls) b.className = cls;
  b.style.marginRight = '6px';
  b.addEventListener('click', onClick);
  return b;
}

function renderSelection() {
  const body = $('selBody');
  const empty = $('selEmpty');
  const sel = state.selection;
  const plan = state.derived && state.derived.plans[0];
  if (!sel || !plan) {
    body.hidden = true; empty.hidden = false; return;
  }
  empty.hidden = true; body.hidden = false;
  body.innerHTML = '';

  if (sel.type === 'wall') {
    const w = plan.walls.find(x => x.id === sel.id);
    if (!w) return;
    body.appendChild(hint(`墙体 · ${w.kind === 'exterior' ? '外墙' : '内墙'} · ${w.body ? w.body.startKind + '/' + w.body.endKind : ''}`));
    body.appendChild(field('长度', w.length.toFixed(3), {
      step: 0.001, unit: 'm', min: 0.05,
      onChange: v => applyOps([{ op: 'set_wall_length', id: w.id, length: v }]),
    }));
    body.appendChild(field('角度', w.angle.toFixed(2), {
      step: 0.5, unit: '°',
      onChange: v => applyOps([{ op: 'set_wall_angle', id: w.id, angle: v }]),
    }));
    body.appendChild(field('厚度', w.thickness.toFixed(3), {
      step: 0.01, unit: 'm', min: 0.02,
      onChange: v => applyOps([{ op: 'set_wall_thickness', id: w.id, thickness: v }]),
    }));
    body.appendChild(field('类型', w.kind, {
      options: [['interior', '内墙'], ['exterior', '外墙'], ['partition', '隔断']],
      onChange: v => applyOps([{ op: 'set_wall_meta', id: w.id, kind: v }]),
    }));
    body.appendChild(field('墙高', w.height.toFixed(2), {
      step: 0.05, unit: 'm', min: 1.8, max: 6,
      onChange: v => applyOps([{ op: 'set_wall_meta', id: w.id, height: v }]),
    }));
    const row = document.createElement('div');
    row.style.marginTop = '8px';
    row.appendChild(button('在中间打断', '', () => applyOps([{ op: 'split_wall', id: w.id, at: 0.5 }])));
    row.appendChild(button('删除墙', 'danger', () => applyOps([{ op: 'delete_wall', id: w.id }])));
    body.appendChild(row);
    body.appendChild(hint('提示：拖动墙体线段可整体平移；拖动蓝色角点可改尺寸。'));
  } else if (sel.type === 'vertex') {
    const v = plan.vertices.find(x => x.id === sel.id);
    if (!v) return;
    body.appendChild(hint('墙角（被多面墙共享，拖动会带动相连墙体）'));
    body.appendChild(field('X', v.xy[0].toFixed(3), {
      step: 0.001, unit: 'm',
      onChange: val => applyOps([{ op: 'move_vertex_free', id: v.id, xy: [val, v.xy[1]] }]),
    }));
    body.appendChild(field('Y', v.xy[1].toFixed(3), {
      step: 0.001, unit: 'm',
      onChange: val => applyOps([{ op: 'move_vertex_free', id: v.id, xy: [v.xy[0], val] }]),
    }));
  } else if (sel.type === 'opening') {
    const o = plan.openings.find(x => x.id === sel.id);
    if (!o) return;
    const w = plan.walls.find(x => x.id === o.wall);
    body.appendChild(hint(`门窗 · 位于 ${w ? w.length.toFixed(2) + 'm 的墙' : o.wall}`));
    body.appendChild(field('类型', o.kind, {
      options: [['door', '门'], ['window', '窗'], ['passage', '垭口']],
      onChange: v => applyOps([{ op: 'update_opening', id: o.id, kind: v }]),
    }));
    body.appendChild(field('离墙起点', o.offset.toFixed(3), {
      step: 0.01, unit: 'm', min: 0,
      onChange: v => applyOps([{ op: 'update_opening', id: o.id, offset: v }]),
    }));
    body.appendChild(field('宽度', o.width.toFixed(3), {
      step: 0.01, unit: 'm', min: 0.1,
      onChange: v => applyOps([{ op: 'update_opening', id: o.id, width: v }]),
    }));
    body.appendChild(field('高度', o.height.toFixed(3), {
      step: 0.01, unit: 'm', min: 0.1,
      onChange: v => applyOps([{ op: 'update_opening', id: o.id, height: v }]),
    }));
    body.appendChild(field('离地高', o.sill.toFixed(3), {
      step: 0.01, unit: 'm', min: 0,
      onChange: v => applyOps([{ op: 'update_opening', id: o.id, sill: v }]),
    }));
    if (o.kind === 'door') {
      body.appendChild(field('开向', o.swing_in ? 'in' : 'out', {
        options: [['in', '向房内'], ['out', '向房外']],
        onChange: v => applyOps([{ op: 'update_opening', id: o.id, swing_in: v === 'in' }]),
      }));
    }
    body.appendChild(button('删除', 'danger', () => applyOps([{ op: 'delete_opening', id: o.id }])));
  } else if (sel.type === 'room') {
    const r = plan.rooms.find(x => x.index === sel.index);
    if (!r) return;
    body.appendChild(hint(`房间 #${r.index + 1}`));
    body.appendChild(field('名称', r.label, {
      type: 'text',
      onChange: v => applyOps([{ op: 'rename_room', index: r.index, label: v }]),
    }));
    body.appendChild(readonly('净面积', r.net_area.toFixed(3) + ' m²'));
    body.appendChild(readonly('毛面积', r.gross_area.toFixed(3) + ' m²'));
    body.appendChild(readonly('净周长', r.perimeter.toFixed(3) + ' m'));
    body.appendChild(readonly('顶点数', r.polygon.length));
    body.appendChild(hint('房间面积由墙体自动派生：改墙 → 面积立刻更新。'));
  }

  // 全局清理工具
  if (sel.type === 'wall') {
    const tools = document.createElement('div');
    tools.style.marginTop = '10px';
    tools.appendChild(hint('清理与标定'));
    tools.appendChild(button('全图正交化', '', () => applyOps([{ op: 'snap_orthogonal' }])));
    tools.appendChild(button('合并共线墙', '', () => applyOps([{ op: 'merge_collinear' }])));
    body.appendChild(tools);
  }
}

function refreshSelectionFromDerived() {
  renderSelection();
}

function hint(text) {
  const p = document.createElement('div');
  p.className = 'empty';
  p.style.margin = '0 0 6px';
  p.textContent = text;
  return p;
}

function readonly(label, value) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  wrap.innerHTML = `<label>${label}</label><span style="flex:1;color:#9aa4b2">${value}</span>`;
  return wrap;
}

// ---------------------------------------------------------------- 导出

async function exportFormat(fmt) {
  if (state.busy) return;
  state.busy = true;
  toast('正在导出 ' + fmt.toUpperCase() + ' …');
  try {
    const res = await API.post('/api/export', { project: state.project, format: fmt, options: {} });
    const ok = res.check && res.check.ok;
    const item = document.createElement('div');
    item.className = 'item';
    item.innerHTML =
      `<a href="${res.url}" download="${res.file}">${res.file}</a>` +
      ` <span class="${ok ? 'good' : 'bad'}">${ok ? '✓' : '✗'}</span>` +
      ` <span style="color:#5f6b7a">${(res.sizeHint / 1024).toFixed(1)} KB</span>`;
    $('exportLog').prepend(item);
    toast(ok ? `已导出 ${res.file}` : `导出完成但有警告：${(res.check.problems || []).join('; ')}`, !ok);
    if (res.check && res.check.problems && res.check.problems.length) {
      const p = document.createElement('div');
      p.className = 'item bad';
      p.textContent = res.check.problems.join('; ');
      $('exportLog').prepend(p);
    }
  } catch (err) {
    toast('导出失败：' + err.message, true);
  } finally {
    state.busy = false;
  }
}

function saveProject() {
  const blob = new Blob([JSON.stringify(state.project, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (state.project.name || 'floorplan') + '.s2p.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast('工程文件已保存（可再次打开继续编辑）');
}

// ---------------------------------------------------------------- 其它

function deleteSelection() {
  const s = state.selection;
  if (!s) return;
  if (s.type === 'wall') applyOps([{ op: 'delete_wall', id: s.id }]);
  else if (s.type === 'opening') applyOps([{ op: 'delete_opening', id: s.id }]);
  else toast('该对象不支持直接删除', true);
  state.selection = null;
  editor.setSelection(null);
  renderSelection();
}

let toastTimer = null;
function toast(msg, bad = false) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.toggle('bad', !!bad);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

boot();
