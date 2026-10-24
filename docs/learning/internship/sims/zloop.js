// W2 payload step on the vertical loop — canvas plots. SPEC section 5.

import { simulate, summarize } from './zloop_core.mjs';

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
 * Mount the z-loop widget into `host`.
 */
export function mountZloop(host) {
  const st = {
    Kp: 1.0,
    Kv: 3.0,
    Ki: 2.0,
    m_add: 0.5,
    Tmax: 2 * 2.0 * 9.81,
    tau: 0.05,
    useI: true,
    hoverFF: true,
    openLoop: false,
  };

  host.innerHTML = '';
  host.classList.add('sim-widget');

  const results = { res: null, sum: null };

  /* ---------------- controls ---------------- */

  const presetRow = el('div', { class: 'toolbar' });

  function applyPreset(p) {
    Object.assign(st, p);
    syncControls();
    run();
  }

  const PRESETS = [
    { label: 'P only', p: { useI: false, hoverFF: true, openLoop: false } },
    { label: 'PI', p: { useI: true, hoverFF: true, openLoop: false } },
    { label: 'open loop', p: { openLoop: true, useI: true, hoverFF: true } },
    { label: 'heavy payload (m_add 2.5)', p: { m_add: 2.5, openLoop: false, useI: true, hoverFF: true } },
  ];

  const controlRefs = [];

  function syncControls() {
    for (const ref of controlRefs) {
      if (ref.kind === 'slider') {
        ref.input.value = st[ref.key];
        ref.out.textContent = `${st[ref.key]}${ref.unit}`;
      } else {
        ref.input.checked = st[ref.key];
      }
    }
  }

  function slider(key, label, min, max, step, unit) {
    const input = el('input', { type: 'range', min, max, step, value: st[key], id: `zl-${key}` });
    const out = el('output', { text: `${st[key]}${unit}` });
    input.addEventListener('input', () => {
      st[key] = parseFloat(input.value);
      out.textContent = `${st[key]}${unit}`;
      run();
    });
    controlRefs.push({ kind: 'slider', key, input, out, unit });
    return el('div', { class: 'ctrl' }, [
      el('label', { for: `zl-${key}`, text: `${label} (${key})` }),
      input,
      out,
    ]);
  }

  function toggle(key, label) {
    const input = el('input', { type: 'checkbox', id: `zl-${key}` });
    input.checked = st[key];
    input.addEventListener('change', () => {
      st[key] = input.checked;
      run();
    });
    controlRefs.push({ kind: 'toggle', key, input });
    return el('label', { class: 'check', for: `zl-${key}` }, [input, label]);
  }

  for (const preset of PRESETS) {
    presetRow.appendChild(el('button', {
      class: 'btn', type: 'button',
      onclick: () => applyPreset(preset.p),
    }, [preset.label]));
  }

  const controls = el('div', { class: 'controls' }, [
    slider('Kp', '比例 Kp', 0, 5, 0.05, ''),
    slider('Kv', '微分 Kv', 0, 10, 0.05, ''),
    slider('Ki', '积分 Ki', 0, 8, 0.05, ''),
    slider('m_add', '载荷 m_add', 0, 3, 0.05, ' kg'),
    slider('Tmax', '上限 Tmax', 10, 100, 0.5, ' N'),
    slider('tau', '电机滞后 tau', 0.005, 0.5, 0.005, ' s'),
  ]);

  const toggles = el('div', { class: 'toolbar' }, [
    toggle('useI', '积分项 on/off (useI)'),
    toggle('hoverFF', '悬停前馈 hoverFF'),
    toggle('openLoop', '开环 openLoop'),
  ]);

  /* ---------------- plots ---------------- */

  const plots = [
    { key: 'z', title: '高度 z', unit: 'm', color: '#1f5fd0' },
    { key: 'vz', title: '速度 vz', unit: 'm/s', color: '#c62828' },
    { key: 'T', title: '推力 T', unit: 'N', color: '#2e7d32' },
  ];

  const plotNodes = [];
  for (const p of plots) {
    const canvas = el('canvas', { class: 'plot-canvas' });
    const node = el('div', { class: 'plot' }, [
      el('div', { class: 'plot-title', text: `${p.title} (${p.unit})` }),
      canvas,
    ]);
    plotNodes.push({ p, canvas, ctx: canvas.getContext('2d'), node });
  }

  const weightLegend = el('div', { class: 'legend' });
  const readouts = el('div', { class: 'readouts' });

  const RO = [
    ['最低高度 lowest z', 'lowestZ', 'm'],
    ['最终高度 final z', 'finalZ', 'm'],
    ['稳定时间 settle time (|z|≤2cm)', 'settle', 's'],
    ['最终推力 final T', 'finalT', 'N'],
    ['积分项 I', 'integral', 'on/off'],
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

  host.appendChild(el('h3', { text: 'W2 · 垂直环载荷阶跃 Payload step on the vertical loop' }));
  host.appendChild(controls);
  host.appendChild(toggles);
  host.appendChild(el('div', { class: 'plot-title', text: '预设 presets' }));
  host.appendChild(presetRow);
  host.appendChild(el('div', { class: 'plot-title', text: '曲线 plots' }));
  for (const pn of plotNodes) host.appendChild(pn.node);
  host.appendChild(weightLegend);
  host.appendChild(readouts);

  /* ---------------- drawing ---------------- */

  function cssVar(name, fallback) {
    try {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (err) {
      return fallback;
    }
  }

  function drawPlot(pn) {
    const { canvas, ctx, p } = pn;
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    const W = Math.max(240, rect.width || 320);
    const H = Math.round(Math.max(90, Math.min(180, W * 0.28)));
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.height = `${H}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const cLine = cssVar('--line', '#888');
    const cMuted = cssVar('--muted', '#777');
    const cPanel = cssVar('--panel-2', '#eee');
    const res = results.res;

    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = cPanel;
    ctx.fillRect(0, 0, W, H);

    const padL = 44;
    const padR = 8;
    const padT = 8;
    const padB = 18;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;

    if (!res || !res.t.length) return;

    const tMax = res.t[res.t.length - 1];
    const series = res[p.key];
    let lo = Infinity;
    let hi = -Infinity;
    for (const v of series) { if (v < lo) lo = v; if (v > hi) hi = v; }

    // Reference lines.
    const extra = [];
    if (p.key === 'T') extra.push({ v: (2.0 + st.m_add) * 9.81, color: '#b45309', dash: true, label: '重量 weight' });
    if (p.key === 'z') extra.push({ v: 0, color: cMuted, dash: false });
    if (p.key === 'z') extra.push({ v: 0.02, color: cMuted, dash: true });
    if (p.key === 'z') extra.push({ v: -0.02, color: cMuted, dash: true });
    for (const e of extra) { if (e.v < lo) lo = e.v; if (e.v > hi) hi = e.v; }

    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const span = hi - lo;
    lo -= span * 0.08;
    hi += span * 0.08;

    const X = (t) => padL + (t / tMax) * plotW;
    const Y = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

    // Axes.
    ctx.strokeStyle = cLine;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT);
    ctx.lineTo(padL, padT + plotH);
    ctx.lineTo(padL + plotW, padT + plotH);
    ctx.stroke();

    // Y labels.
    ctx.font = '10px ui-monospace, monospace';
    ctx.fillStyle = cMuted;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let i = 0; i <= 2; i++) {
      const v = lo + ((hi - lo) * i) / 2;
      ctx.fillText(v.toFixed(2), padL - 4, Y(v));
    }

    // Step time marker (payload added).
    const tAdd = 2.0;
    ctx.strokeStyle = '#b45309';
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(X(tAdd), padT);
    ctx.lineTo(X(tAdd), padT + plotH);
    ctx.stroke();
    ctx.setLineDash([]);

    // Reference lines.
    for (const e of extra) {
      ctx.strokeStyle = e.color;
      ctx.setLineDash(e.dash ? [5, 4] : []);
      ctx.beginPath();
      ctx.moveTo(padL, Y(e.v));
      ctx.lineTo(padL + plotW, Y(e.v));
      ctx.stroke();
      ctx.setLineDash([]);
      if (e.label) {
        ctx.fillStyle = e.color;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.font = '10px system-ui, sans-serif';
        ctx.fillText(e.label, padL + 4, Y(e.v) - 2);
      }
    }

    // Series.
    ctx.strokeStyle = p.color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    const stride = Math.max(1, Math.floor(res.t.length / (plotW * 2)));
    for (let i = 0; i < res.t.length; i += stride) {
      const x = X(res.t[i]);
      const y = Y(series[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Time axis labels.
    ctx.fillStyle = cMuted;
    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText('0', padL, padT + plotH + 3);
    ctx.textAlign = 'right';
    ctx.fillText(`${tMax.toFixed(1)} s`, padL + plotW, padT + plotH + 3);
  }

  function updateReadouts() {
    const s = results.sum;
    if (!s) return;
    roNodes.lowestZ.textContent = fmt(s.lowestZ, 3);
    roNodes.finalZ.textContent = fmt(s.finalZ, 3);
    roNodes.settle.textContent = fmt(s.settleTime, 2);
    roNodes.finalT.textContent = fmt(s.finalT, 2);
    roNodes.integral.textContent = st.useI ? 'on (积分项 on)' : 'off (积分项 off)';
    weightLegend.textContent =
      `重量线 weight line = (m_nom + m_add)·g = ${fmt((2.0 + st.m_add) * 9.81, 2)} N` +
      (s.saturated ? ' · 饱和 saturated: Tcmd 已达到 Tmax' : '');
  }

  function run() {
    results.res = simulate(st);
    results.sum = summarize(results.res, 0.02);
    for (const pn of plotNodes) drawPlot(pn);
    updateReadouts();
  }

  const onResize = () => { for (const pn of plotNodes) drawPlot(pn); };
  window.addEventListener('resize', onResize);

  syncControls();
  run();

  return { destroy() { window.removeEventListener('resize', onResize); } };
}
