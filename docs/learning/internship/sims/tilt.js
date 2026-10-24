// W1 tilt and thrust vector — canvas view. SPEC section 4.

import { thrustFromAccel, singleAxis } from './tilt_core.mjs';

const el = (tag, attrs = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
};

const fmt = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : '—');

/**
 * Mount the tilt widget into `host`.
 */
export function mountTilt(host) {
  const st = {
    m: 2.0,
    ax: 3,
    ay: 4,
    az: 0,
    theta: 20,      // side-view tilt slider (degrees)
    side: false,    // side view toggle
    yaw: -0.5,      // camera azimuth (radians)
    pitch: 0.35,    // camera elevation (radians)
    dragging: false,
  };

  host.innerHTML = '';
  host.classList.add('sim-widget');

  /* ---------------- controls ---------------- */

  const sliders = {};

  function makeSlider(key, label, min, max, step, value, unit, onChange) {
    const input = el('input', {
      type: 'range', min, max, step, value,
      id: `tilt-${key}`,
    });
    const out = el('output', { text: `${value}${unit}` });
    input.addEventListener('input', () => {
      const v = parseFloat(input.value);
      out.textContent = `${v}${unit}`;
      st[key] = v;
      onChange();
      draw();
      updateReadouts();
    });
    sliders[key] = { input, out };
    return el('div', { class: 'ctrl' }, [
      el('label', { for: `tilt-${key}`, text: `${label} (${key})` }),
      input,
      out,
    ]);
  }

  const sideToggle = el('input', { type: 'checkbox', id: 'tilt-side' });
  sideToggle.addEventListener('change', () => {
    st.side = sideToggle.checked;
    draw();
    updateReadouts();
  });

  const controls = el('div', { class: 'controls' }, [
    makeSlider('m', '质量 m', 0.5, 5, 0.1, st.m, ' kg', () => {}),
    makeSlider('ax', '加速度 ax', -8, 8, 0.1, st.ax, ' m/s²', () => {}),
    makeSlider('ay', '加速度 ay', -8, 8, 0.1, st.ay, ' m/s²', () => {}),
    makeSlider('az', '加速度 az', -5, 5, 0.1, st.az, ' m/s²', () => {}),
    el('div', { class: 'ctrl' }, [
      el('label', { for: 'tilt-side', text: '侧视图 side view (one axis)' }),
      sideToggle,
    ]),
  ]);

  const sideControls = el('div', { class: 'controls', style: 'margin-top:10px' }, [
    makeSlider('theta', '倾角 θ', -60, 60, 1, st.theta, '°', () => {}),
  ]);

  const canvas = el('canvas', { class: 'sim-canvas', id: 'tilt-canvas' });
  const readouts = el('div', { class: 'readouts' });

  host.appendChild(el('h3', { text: 'W1 · 倾角与推力矢量 Tilt and thrust vector' }));
  host.appendChild(controls);
  host.appendChild(sideControls);
  host.appendChild(el('div', { class: 'sim-grid' }, [
    el('div', {}, [
      canvas,
      el('p', { class: 'legend', text: '拖动画布可旋转视角 drag to orbit the camera' }),
    ]),
    el('div', {}, [readouts]),
  ]));

  /* ---------------- readouts ---------------- */

  const RO = [
    ['推力 T', 'T', 'N'],
    ['倾角 θ', 'theta', '°'],
    ['方向 φ', 'phi', '°'],
    ['分量 Fx', 'Fx', 'N'],
    ['分量 Fy', 'Fy', 'N'],
    ['分量 Fz', 'Fz', 'N'],
    ['分量 T·cosθ (vertical)', 'Tvert', 'N'],
    ['分量 T·sinθ (horizontal)', 'Thoriz', 'N'],
    ['加速度 a', 'a', 'm/s²'],
  ];

  const roNodes = {};
  for (const [label, key, unit] of RO) {
    const v = el('div', { class: 'v', text: '—' });
    roNodes[key] = v;
    readouts.appendChild(el('div', { class: 'readout' }, [
      el('div', { class: 'k', text: `${label} (${unit})` }),
      v,
    ]));
  }

  function updateReadouts() {
    if (st.side) {
      const r = singleAxis(st.m, st.theta);
      roNodes.T.textContent = fmt(r.T);
      roNodes.theta.textContent = fmt(st.theta);
      roNodes.phi.textContent = '—';
      roNodes.Fx.textContent = '—';
      roNodes.Fy.textContent = '—';
      roNodes.Fz.textContent = '—';
      roNodes.Tvert.textContent = fmt(r.Tvert);
      roNodes.Thoriz.textContent = fmt(r.Thoriz);
      roNodes.a.textContent = fmt(r.a);
      return;
    }
    const r = thrustFromAccel(st.m, st.ax, st.ay, st.az);
    roNodes.T.textContent = fmt(r.T);
    roNodes.theta.textContent = fmt(r.thetaDeg);
    roNodes.phi.textContent = fmt(r.phiDeg);
    roNodes.Fx.textContent = fmt(r.Fx);
    roNodes.Fy.textContent = fmt(r.Fy);
    roNodes.Fz.textContent = fmt(r.Fz);
    // Horizontal/vertical split of the thrust, in the tilt plane.
    const horiz = Math.sqrt(r.Fx * r.Fx + r.Fy * r.Fy);
    roNodes.Tvert.textContent = fmt(r.Fz);
    roNodes.Thoriz.textContent = fmt(horiz);
    roNodes.a.textContent = fmt(Math.hypot(st.ax, st.ay, st.az));
  }

  /* ---------------- canvas ---------------- */

  const ctx = canvas.getContext('2d');
  let W = 0;
  let H = 0;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    W = Math.max(240, rect.width || 320);
    H = Math.round(W * 0.68);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  // Simple 3D -> 2D projection (yaw about the world up axis, then pitch).
  function project(p) {
    const cy = Math.cos(st.yaw), sy = Math.sin(st.yaw);
    const cp = Math.cos(st.pitch), sp = Math.sin(st.pitch);
    const x1 = p[0] * cy - p[1] * sy;
    const y1 = p[0] * sy + p[1] * cy;
    const z1 = p[2];
    const y2 = y1 * cp - z1 * sp;
    const z2 = y1 * sp + z1 * cp;
    const scale = Math.min(W, H) * 0.33;
    return [W / 2 + x1 * scale, H / 2 - y2 * scale, z2];
  }

  function line3(a, b, color, width = 2) {
    const A = project(a);
    const B = project(b);
    ctx.beginPath();
    ctx.moveTo(A[0], A[1]);
    ctx.lineTo(B[0], B[1]);
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.stroke();
    return [A, B];
  }

  function label3(p, text, color) {
    const P = project(p);
    ctx.font = '12px system-ui, sans-serif';
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText(text, P[0], P[1] - 4);
  }

  function arrow3(from, to, color, width = 2.5) {
    line3(from, to, color, width);
    const A = project(from);
    const B = project(to);
    const ang = Math.atan2(B[1] - A[1], B[0] - A[0]);
    const h = 9;
    ctx.beginPath();
    ctx.moveTo(B[0], B[1]);
    ctx.lineTo(B[0] - h * Math.cos(ang - 0.4), B[1] - h * Math.sin(ang - 0.4));
    ctx.lineTo(B[0] - h * Math.cos(ang + 0.4), B[1] - h * Math.sin(ang + 0.4));
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
  }

  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (err) {
      return fallback;
    }
  }

  function draw() {
    if (!ctx) return;
    const cText = cssVar('--text', '#222');
    const cLine = cssVar('--line', '#888');
    const cAccent = cssVar('--accent', '#1f5fd0');
    const cMuted = cssVar('--muted', '#777');
    const cPanel = cssVar('--panel-2', '#eee');

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = cPanel;
    ctx.fillRect(0, 0, W, H);

    // Ground grid (z = 0 plane).
    ctx.globalAlpha = 0.5;
    for (let gx = -1; gx <= 1; gx++) {
      for (let gy = -1; gy <= 1; gy++) {
        line3([gx, gy, 0], [gx + (gx === -1 ? 2 : gx === 1 ? 0 : 1), gy, 0], cLine, 1);
      }
    }
    ctx.globalAlpha = 1;
    line3([-1.2, 0, 0], [1.2, 0, 0], cLine, 1);
    line3([0, -1.2, 0], [0, 1.2, 0], cLine, 1);

    if (st.side) {
      // Side view: single axis, tilt in the x-z plane.
      const r = singleAxis(st.m, st.theta);
      const th = (st.theta * Math.PI) / 180;
      const len = 1.0;
      const dir = [Math.sin(th), 0, Math.cos(th)];
      const tip = [dir[0] * len, 0, dir[1] * len];

      // Drone body, tilted.
      const bodyHalf = 0.42;
      const bx = Math.cos(th) * bodyHalf;
      const bz = -Math.sin(th) * bodyHalf;
      line3([-bx, 0, -bz], [bx, 0, -bz], cText, 3);
      line3([-bx, -0.16, -bz], [-bx, 0.16, -bz], cText, 2);
      line3([bx, -0.16, -bz], [bx, 0.16, -bz], cText, 2);

      // Thrust arrow along the body z.
      arrow3([0, 0, 0], tip, cAccent, 3);

      // Vertical component.
      arrow3([0, 0, 0], [0, 0, Math.max(0, dir[1] * len)], '#2e7d32', 2.5);
      label3([0, 0, Math.max(0, dir[1] * len)], 'T·cosθ (vertical)', '#2e7d32');

      // Horizontal component.
      arrow3([0, 0, 0], [dir[0] * len, 0, 0], '#c62828', 2.5);
      label3([dir[0] * len, 0, 0], 'T·sinθ (horizontal)', '#c62828');

      // Gravity.
      arrow3([0, 0, 0], [0, 0, -0.6], cMuted, 2);
      label3([0, 0, -0.6], 'mg 重力', cMuted);

      ctx.font = '12px system-ui, sans-serif';
      ctx.fillStyle = cText;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(`侧视图 side view · T=${fmt(r.T)} N · Tvert=${fmt(r.Tvert)} N · Thoriz=${fmt(r.Thoriz)} N · a=${fmt(r.a)} m/s²`, 8, 8);
      return;
    }

    // Main view: X-frame drone tilted to match the thrust vector.
    const r = thrustFromAccel(st.m, st.ax, st.ay, st.az);
    // Thrust direction (body z) points along F.
    const F = [r.Fx, r.Fy, r.Fz];
    const T = r.T || 1;
    const zAxis = [F[0] / T, F[1] / T, F[2] / T];

    // Build a frame: body z = thrust direction; body x/y roughly perpendicular.
    const up = Math.abs(zAxis[2]) > 0.95 ? [1, 0, 0] : [0, 0, 1];
    const xAxis = cross(up, zAxis);
    const yAxis = cross(zAxis, xAxis);

    const arm = 0.45;
    const corners = [
      [-1, -1], [1, -1], [1, 1], [-1, 1],
    ];
    const pts = corners.map(([sx, sy]) => [
      (xAxis[0] * sx + yAxis[0] * sy) * arm,
      (xAxis[1] * sx + yAxis[1] * sy) * arm,
      (xAxis[2] * sx + yAxis[2] * sy) * arm,
    ]);

    // Frame arms.
    for (let i = 0; i < 4; i++) {
      line3([0, 0, 0], pts[i], cText, 2.5);
      // Motor pod.
      const p = pts[i];
      line3([p[0] - 0.06, p[1], p[2]], [p[0] + 0.06, p[1], p[2]], cAccent, 3);
    }
    // Body.
    line3([-0.12, -0.12, 0], [0.12, 0.12, 0], cMuted, 2);

    // Thrust arrow along the body z.
    const tip = [zAxis[0] * 0.95, zAxis[1] * 0.95, zAxis[2] * 0.95];
    arrow3([0, 0, 0], tip, cAccent, 3);
    label3(tip, 'T 推力 thrust', cAccent);

    // Vertical (world z) component of the thrust.
    const Fz = r.Fz;
    arrow3([0, 0, 0], [0, 0, Fz / T * 0.95], '#2e7d32', 2.5);
    label3([0, 0, Fz / T * 0.95], 'T·cosθ (vertical)', '#2e7d32');

    // Horizontal component of the thrust.
    const hx = r.Fx;
    const hy = r.Fy;
    const hlen = Math.hypot(hx, hy) / T * 0.95;
    if (hlen > 1e-6) {
      arrow3([0, 0, 0], [hx / (Math.hypot(hx, hy) || 1) * hlen, hy / (Math.hypot(hx, hy) || 1) * hlen, 0], '#c62828', 2.5);
      label3([hx / (Math.hypot(hx, hy) || 1) * hlen, hy / (Math.hypot(hx, hy) || 1) * hlen, 0], 'T·sinθ (horizontal)', '#c62828');
    }

    // Gravity.
    arrow3([0, 0, 0], [0, 0, -0.6], cMuted, 2);
    label3([0, 0, -0.6], 'mg 重力', cMuted);

    ctx.font = '12px system-ui, sans-serif';
    ctx.fillStyle = cText;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(
      `T=${fmt(r.T)} N · θ=${fmt(r.thetaDeg)}° · φ=${fmt(r.phiDeg)}° · F=(${fmt(r.Fx)}, ${fmt(r.Fy)}, ${fmt(r.Fz)}) N`,
      8, 8);
  }

  function cross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }

  /* ---------------- orbit drag ---------------- */

  let lastX = 0;
  let lastY = 0;
  canvas.addEventListener('pointerdown', (e) => {
    st.dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    canvas.classList.add('dragging');
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!st.dragging) return;
    st.yaw += (e.clientX - lastX) * 0.01;
    st.pitch = Math.max(-1.2, Math.min(1.2, st.pitch + (e.clientY - lastY) * 0.01));
    lastX = e.clientX;
    lastY = e.clientY;
    draw();
  });
  const endDrag = () => {
    st.dragging = false;
    canvas.classList.remove('dragging');
  };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('pointerleave', endDrag);

  const onResize = () => { resize(); draw(); };
  window.addEventListener('resize', onResize);

  resize();
  draw();
  updateReadouts();

  return { destroy() { window.removeEventListener('resize', onResize); } };
}
