// 3D 预览：手写 WebGL1 渲染器，零依赖。
// 数据来自 Python 端打包的 mesh（base64 Float32 位置 + Uint32 索引，按材质分组）。

const VS = `
attribute vec3 aPos;
attribute vec3 aNormal;
uniform mat4 uMVP;
varying vec3 vN;
void main() {
  vN = aNormal;
  gl_Position = uMVP * vec4(aPos, 1.0);
}`;

const FS = `
precision mediump float;
varying vec3 vN;
uniform vec4 uColor;
uniform vec3 uLight;
void main() {
  vec3 n = normalize(vN);
  vec3 l = normalize(uLight);
  float d = dot(n, l);
  if (!gl_FrontFacing) { n = -n; d = dot(n, l); }
  float shade = 0.42 + 0.58 * max(d, 0.0);
  // 轻微的环境遮蔽感：朝下的面压暗一点
  if (n.z < -0.3) shade *= 0.82;
  gl_FragColor = vec4(uColor.rgb * shade, uColor.a);
}`;

function mat4() { return new Float32Array(16); }
function identity(m) {
  m.fill(0); m[0] = m[5] = m[10] = m[15] = 1; return m;
}
function multiply(out, a, b) {
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      out[c * 4 + r] =
        a[0 * 4 + r] * b[c * 4 + 0] +
        a[1 * 4 + r] * b[c * 4 + 1] +
        a[2 * 4 + r] * b[c * 4 + 2] +
        a[3 * 4 + r] * b[c * 4 + 3];
    }
  }
  return out;
}
function perspective(out, fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  out.fill(0);
  out[0] = f / aspect;
  out[5] = f;
  out[10] = (far + near) / (near - far);
  out[11] = -1;
  out[14] = (2 * far * near) / (near - far);
  return out;
}
function lookAt(out, eye, target, up) {
  let zx = eye[0] - target[0], zy = eye[1] - target[1], zz = eye[2] - target[2];
  let zl = Math.hypot(zx, zy, zz) || 1;
  zx /= zl; zy /= zl; zz /= zl;
  let xx = up[1] * zz - up[2] * zy;
  let xy = up[2] * zx - up[0] * zz;
  let xz = up[0] * zy - up[1] * zx;
  let xl = Math.hypot(xx, xy, xz) || 1;
  xx /= xl; xy /= xl; xz /= xl;
  const yx = zy * xz - zz * xy;
  const yy = zz * xx - zx * xz;
  const yz = zx * xy - zy * xx;
  out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
  out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
  out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
  out[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
  out[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
  out[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
  out[15] = 1;
  return out;
}

function b64ToBytes(b64) {
  const raw = atob(b64);
  const buf = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
  return buf;
}

export class Viewer3D {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = canvas.getContext('webgl', { antialias: true, alpha: false });
    this.ready = false;
    this.batches = [];
    this.tris = 0;

    // 相机
    this.yaw = -0.9;
    this.pitch = 0.62;
    this.distance = 20;
    this.target = [5, 4, 1.2];

    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this._resize();
    this._ro = new ResizeObserver(() => { this._resize(); this.render(); });
    this._ro.observe(canvas.parentElement);

    this._initGL();
    this._bind();
  }

  _initGL() {
    const gl = this.gl;
    if (!gl) {
      console.error('WebGL 不可用');
      return;
    }
    const vs = this._shader(gl.VERTEX_SHADER, VS);
    const fs = this._shader(gl.FRAGMENT_SHADER, FS);
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error('程序链接失败', gl.getProgramInfoLog(prog));
      return;
    }
    this.prog = prog;
    this.aPos = gl.getAttribLocation(prog, 'aPos');
    this.aNormal = gl.getAttribLocation(prog, 'aNormal');
    this.uMVP = gl.getUniformLocation(prog, 'uMVP');
    this.uColor = gl.getUniformLocation(prog, 'uColor');
    this.uLight = gl.getUniformLocation(prog, 'uLight');
    gl.enable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.clearColor(0.059, 0.067, 0.078, 1);
    this.ready = true;
  }

  _shader(type, src) {
    const gl = this.gl;
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.error(gl.getShaderInfoLog(sh));
    }
    return sh;
  }

  _resize() {
    const el = this.canvas.parentElement;
    const w = el.clientWidth || 600, h = el.clientHeight || 400;
    this.w = w; this.h = h;
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
  }

  // ---------------------------------------------------------------- 数据

  setMesh(mesh) {
    const gl = this.gl;
    if (!gl || !this.ready) return;
    // 释放旧缓冲
    for (const b of this.batches) {
      gl.deleteBuffer(b.pos);
      gl.deleteBuffer(b.nrm);
    }
    this.batches = [];
    this.tris = 0;
    if (!mesh || !mesh.primitives) { this.render(); return; }

    for (const prim of mesh.primitives) {
      const pos = new Float32Array(b64ToBytes(prim.positions).buffer);
      const idx = new Uint32Array(b64ToBytes(prim.indices).buffer);
      // 展开成非索引 + 计算面法向（保证硬边）
      const nTri = idx.length / 3;
      const outPos = new Float32Array(nTri * 9);
      const outNrm = new Float32Array(nTri * 9);
      for (let t = 0; t < nTri; t++) {
        const i0 = idx[t * 3], i1 = idx[t * 3 + 1], i2 = idx[t * 3 + 2];
        const ax = pos[i0 * 3], ay = pos[i0 * 3 + 1], az = pos[i0 * 3 + 2];
        const bx = pos[i1 * 3], by = pos[i1 * 3 + 1], bz = pos[i1 * 3 + 2];
        const cx = pos[i2 * 3], cy = pos[i2 * 3 + 1], cz = pos[i2 * 3 + 2];
        const ux = bx - ax, uy = by - ay, uz = bz - az;
        const vx = cx - ax, vy = cy - ay, vz = cz - az;
        let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        const nl = Math.hypot(nx, ny, nz) || 1;
        nx /= nl; ny /= nl; nz /= nl;
        const o = t * 9;
        outPos[o] = ax; outPos[o + 1] = ay; outPos[o + 2] = az;
        outPos[o + 3] = bx; outPos[o + 4] = by; outPos[o + 5] = bz;
        outPos[o + 6] = cx; outPos[o + 7] = cy; outPos[o + 8] = cz;
        for (let k = 0; k < 3; k++) {
          outNrm[o + k * 3] = nx; outNrm[o + k * 3 + 1] = ny; outNrm[o + k * 3 + 2] = nz;
        }
      }
      const posBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
      gl.bufferData(gl.ARRAY_BUFFER, outPos, gl.STATIC_DRAW);
      const nrmBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, nrmBuf);
      gl.bufferData(gl.ARRAY_BUFFER, outNrm, gl.STATIC_DRAW);
      this.batches.push({
        pos: posBuf, nrm: nrmBuf, count: nTri * 3,
        color: prim.color || [0.8, 0.8, 0.8, 1.0],
        material: prim.material,
      });
      this.tris += nTri;
    }

    // 相机对准包围盒
    if (mesh.bboxMin && mesh.bboxMax) {
      const [x0, y0, z0] = mesh.bboxMin, [x1, y1, z1] = mesh.bboxMax;
      this.target = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2];
      const size = Math.hypot(x1 - x0, y1 - y0, z1 - z0);
      this.distance = Math.max(6, size * 1.25);
    }
    this.render();
  }

  // ---------------------------------------------------------------- 相机

  _bind() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener('pointerdown', e => {
      c.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, yaw: this.yaw, pitch: this.pitch, btn: e.button };
    });
    c.addEventListener('pointermove', e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (drag.btn === 0) {
        this.yaw = drag.yaw - dx * 0.008;
        this.pitch = Math.max(-1.45, Math.min(1.45, drag.pitch + dy * 0.008));
      }
      this.render();
    });
    const end = () => { drag = null; };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('wheel', e => {
      e.preventDefault();
      this.distance = Math.max(2, Math.min(200, this.distance * Math.exp(e.deltaY * 0.0012)));
      this.render();
    }, { passive: false });
    c.addEventListener('contextmenu', e => e.preventDefault());
  }

  setView(kind) {
    if (kind === 'top') { this.pitch = 1.45; this.yaw = -Math.PI / 2; }
    else if (kind === 'iso') { this.pitch = 0.62; this.yaw = -0.9; }
    else if (kind === 'front') { this.pitch = 0.05; this.yaw = -Math.PI / 2; }
    this.render();
  }

  // ---------------------------------------------------------------- 渲染

  render() {
    const gl = this.gl;
    if (!gl || !this.ready) return;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!this.batches.length) return;

    const eye = [
      this.target[0] + Math.cos(this.pitch) * Math.cos(this.yaw) * this.distance,
      this.target[1] + Math.cos(this.pitch) * Math.sin(this.yaw) * this.distance,
      this.target[2] + Math.sin(this.pitch) * this.distance,
    ];
    const proj = mat4();
    perspective(proj, Math.PI / 4, this.w / Math.max(this.h, 1), 0.05, 4000);
    const view = mat4();
    lookAt(view, eye, this.target, [0, 0, 1]);
    const mvp = mat4();
    multiply(mvp, proj, view);

    gl.useProgram(this.prog);
    gl.uniformMatrix4fv(this.uMVP, false, mvp);
    gl.uniform3f(this.uLight, 0.45, 0.35, 0.82);

    for (const b of this.batches) {
      gl.bindBuffer(gl.ARRAY_BUFFER, b.pos);
      gl.enableVertexAttribArray(this.aPos);
      gl.vertexAttribPointer(this.aPos, 3, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, b.nrm);
      gl.enableVertexAttribArray(this.aNormal);
      gl.vertexAttribPointer(this.aNormal, 3, gl.FLOAT, false, 0, 0);
      gl.uniform4f(this.uColor, b.color[0], b.color[1], b.color[2], b.color[3]);
      gl.drawArrays(gl.TRIANGLES, 0, b.count);
    }
  }
}
