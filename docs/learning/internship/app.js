// Internship learning hub — SPEC section 3 (views), pinyin tones, audio, SRS.
// Plain ES module, no framework, no build step.

import { mountTilt } from './sims/tilt.js';
import { mountZloop } from './sims/zloop.js';

/* ------------------------------------------------------------------ */
/* storage (every read and write wrapped in try/catch)                */
/* ------------------------------------------------------------------ */

const lsGet = (key, fallback) => {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    return JSON.parse(raw);
  } catch (err) {
    console.warn('localStorage read failed for', key, err);
    return fallback;
  }
};

const lsSet = (key, value) => {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (err) {
    console.warn('localStorage write failed for', key, err);
    return false;
  }
};

const KEY_TAB = 'lih.tab.v1';
const KEY_SRS = 'lih.srs.v1';
const KEY_FC = 'lih.flash.v1';

/* ------------------------------------------------------------------ */
/* small helpers                                                       */
/* ------------------------------------------------------------------ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

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

const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
const cjkCount = (s) => (String(s || '').match(/[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g) || []).length;

const stripTones = (s) =>
  String(s || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();

const fmt = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : '—');

/* ------------------------------------------------------------------ */
/* pinyin: syllable split + tone detection                            */
/* ------------------------------------------------------------------ */

const INITIALS_2 = ['zh', 'ch', 'sh'];
const INITIALS_1 = [
  'b', 'p', 'm', 'f', 'd', 't', 'n', 'l', 'g', 'k', 'h',
  'j', 'q', 'x', 'r', 'z', 'c', 's', 'y', 'w',
];

// Standard finals, longest first so greedy matching prefers the longest
// legal syllable (iang before ian before ia, ang before an, ...).
const FINALS = [
  'ueng', 'iong', 'uang', 'iang',
  'uai', 'uan', 'ian', 'iao', 'ing', 'ang', 'eng', 'ong', 'van', 'uei', 'uen',
  'ai', 'ei', 'ao', 'ou', 'an', 'en', 'er', 'ia', 'ie', 'iu', 'in',
  'ua', 'uo', 'ui', 'un', 've',
  'a', 'o', 'e', 'i', 'u', 'v', 'n',
];

const TONE_CHARS = {
  1: 'āēīōūǖĀĒĪŌŪǕ',
  2: 'áéíóúǘÁÉÍÓÚǗ',
  3: 'ǎěǐǒǔǚǍĚǏǑǓǙ',
  4: 'àèìòùǜÀÈÌÒÙǛ',
};

const toneOf = (text) => {
  for (const [tone, chars] of Object.entries(TONE_CHARS)) {
    for (const ch of text) if (chars.includes(ch)) return Number(tone);
  }
  return 0; // no mark = neutral
};

// Base letters used for matching; ü is written v in source text.
const baseLetters = (s) =>
  s
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/ü/g, 'v')
    .replace(/Ü/g, 'V')
    .toLowerCase();

/**
 * Split one pinyin word into syllables.
 * Returns [{text, tone}]. Unparsable tails become one neutral syllable, so
 * nothing ever throws.
 */
function splitSyllables(word) {
  const src = String(word || '').trim();
  if (!src) return [];
  const base = baseLetters(src);
  const out = [];
  let i = 0;
  let guard = 0;

  while (i < src.length && guard++ < 64) {
    let initLen = 0;
    const two = base.slice(i, i + 2);
    const one = base.slice(i, i + 1);
    if (INITIALS_2.includes(two)) initLen = 2;
    else if (INITIALS_1.includes(one)) initLen = 1;

    const restBase = base.slice(i + initLen);
    let finalLen = 0;
    for (const f of FINALS) {
      if (restBase.startsWith(f)) { finalLen = f.length; break; }
    }
    if (finalLen === 0) {
      const text = src.slice(i);
      out.push({ text, tone: toneOf(text) });
      break;
    }
    const text = src.slice(i, i + initLen + finalLen);
    out.push({ text, tone: toneOf(text) });
    i += initLen + finalLen;
  }
  return out;
}

/** Tokenise a pinyin string: {type:'syl',text,tone} | {type:'space'}. */
function tokenisePinyin(pinyin) {
  const tokens = [];
  const words = String(pinyin || '').split(/\s+/).filter(Boolean);
  words.forEach((word, wi) => {
    if (wi > 0) tokens.push({ type: 'space' });
    for (const s of splitSyllables(word)) tokens.push({ type: 'syl', ...s });
  });
  return tokens;
}

/** HTML for a tone-coloured pinyin string. */
function pinyinHtml(pinyin) {
  return tokenisePinyin(pinyin)
    .map((tk) =>
      tk.type === 'space'
        ? ' '
        : `<span class="tone-${tk.tone}">${esc(tk.text)}</span>`)
    .join('');
}

/**
 * HTML for the hanzi, tone-coloured when the syllable count matches the CJK
 * character count. When it does not match, plain text (no throw).
 */
function hanziHtml(zh, pinyin) {
  const chars = Array.from(String(zh || ''));
  const cjkIdx = [];
  chars.forEach((ch, i) => { if (CJK.test(ch)) cjkIdx.push(i); });
  const syllables = tokenisePinyin(pinyin).filter((t) => t.type === 'syl');
  const match = cjkIdx.length > 0 && cjkIdx.length === syllables.length;
  if (!match) return chars.map((c) => esc(c)).join('');
  const toneByIndex = new Map();
  cjkIdx.forEach((charIdx, k) => toneByIndex.set(charIdx, syllables[k].tone));
  return chars
    .map((c, i) =>
      toneByIndex.has(i) ? `<span class="tone-${toneByIndex.get(i)}">${esc(c)}</span>` : esc(c))
    .join('');
}

/* ------------------------------------------------------------------ */
/* speech synthesis                                                    */
/* ------------------------------------------------------------------ */

const speech = {
  voices: [],
  zhVoice: null,
  refresh() {
    if (!('speechSynthesis' in window)) return;
    try {
      this.voices = window.speechSynthesis.getVoices() || [];
    } catch (err) {
      this.voices = [];
    }
    this.zhVoice =
      this.voices.find((v) => /^zh([-_]|$)/i.test(v.lang)) ||
      this.voices.find((v) => /chinese|mandarin|普通话|中文/i.test(v.name || '')) ||
      null;
  },
  supported() {
    return 'speechSynthesis' in window;
  },
  available() {
    return this.supported() && !!this.zhVoice;
  },
  speak(text) {
    if (!this.available()) return false;
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'zh-CN';
      u.voice = this.zhVoice;
      u.rate = 0.8;
      window.speechSynthesis.speak(u);
      return true;
    } catch (err) {
      console.warn('speech failed', err);
      return false;
    }
  },
};

if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
  speech.refresh();
  try {
    window.speechSynthesis.addEventListener('voiceschanged', () => {
      speech.refresh();
      renderGlossary();
    });
  } catch (err) {
    console.warn('voiceschanged listener failed', err);
  }
}

/* ------------------------------------------------------------------ */
/* data loading                                                        */
/* ------------------------------------------------------------------ */

const state = {
  progress: null,
  glossary: [],
  activeTab: lsGet(KEY_TAB, 'roadmap'),
  currentLesson: null,
};

async function loadJson(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function loadText(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.text();
}

/* ------------------------------------------------------------------ */
/* tabs                                                                */
/* ------------------------------------------------------------------ */

const TABS = [
  { id: 'roadmap', zh: '路线图', en: 'Roadmap' },
  { id: 'lessons', zh: '课程', en: 'Lessons' },
  { id: 'glossary', zh: '术语表', en: 'Glossary' },
  { id: 'flashcards', zh: '闪卡', en: 'Flashcards' },
  { id: 'breakpoints', zh: '断点', en: 'Break points' },
  { id: 'sims', zh: '仿真', en: 'Sims' },
];

function buildTabs() {
  const nav = $('#tabs');
  nav.innerHTML = '';
  for (const t of TABS) {
    nav.appendChild(el('button', {
      class: 'tab',
      type: 'button',
      role: 'tab',
      id: `tab-${t.id}`,
      'data-tab': t.id,
      'aria-selected': 'false',
      onclick: () => selectTab(t.id),
    }, [`${t.zh} ${t.en}`]));
  }
}

function selectTab(id) {
  if (!TABS.some((t) => t.id === id)) id = 'roadmap';
  state.activeTab = id;
  lsSet(KEY_TAB, id);
  for (const t of TABS) {
    const btn = $(`#tab-${t.id}`);
    if (btn) btn.setAttribute('aria-selected', t.id === id ? 'true' : 'false');
    const view = $(`#view-${t.id}`);
    if (view) view.classList.toggle('active', t.id === id);
  }
  window.scrollTo({ top: 0, behavior: 'auto' });
}

/* ------------------------------------------------------------------ */
/* view 1 — roadmap                                                    */
/* ------------------------------------------------------------------ */

const STATUS_LABEL = {
  planned: 'planned', taught: 'taught', quizzed: 'quizzed', passed: 'passed',
};

function statusChip(status) {
  const s = String(status || 'planned');
  return `<span class="chip ${esc(s)}">${esc(STATUS_LABEL[s] || s)}</span>`;
}

function scoreText(scores) {
  const entries = Object.entries(scores || {});
  if (!entries.length) return '';
  return entries.map(([k, v]) => `${esc(k)}:${esc(v)}`).join(' ');
}

function renderRoadmap() {
  const host = $('#view-roadmap');
  const p = state.progress;
  if (!p) { host.innerHTML = ''; return; }

  const lessonsByModule = new Map();
  for (const l of p.lessons || []) {
    if (!lessonsByModule.has(l.module)) lessonsByModule.set(l.module, []);
    lessonsByModule.get(l.module).push(l);
  }

  const parts = [];
  parts.push('<h2>路线图 · Roadmap</h2>');
  parts.push(
    `<p class="legend">目标：${esc((p.target || {}).company || '')} · ${esc((p.target || {}).role || '')}` +
    ` · 语言 ${esc((p.target || {}).language || '')} · 更新 ${esc(p.updated || '')}</p>`);

  parts.push('<div class="grid">');
  for (const m of p.modules || []) {
    const lessons = lessonsByModule.get(m.id) || [];
    const rows = lessons.map((l) => {
      const sc = scoreText(l.scores);
      const open = l.file
        ? `<button type="button" data-lesson="${esc(l.id)}">打开 open</button>`
        : '';
      return `<div class="lesson-row">
        <span class="lid">${esc(l.id)}</span>
        <span class="lname">${esc(l.en)}${l.zh ? ` · ${esc(l.zh)}` : ''}</span>
        ${statusChip(l.status)}
        ${sc ? `<span class="scores">${sc}</span>` : ''}
        ${open}
      </div>`;
    }).join('');

    parts.push(`<div class="card module-card">
      <h3>${esc(m.id)} · ${esc(m.en)}</h3>
      <div class="zh">${esc(m.zh)}</div>
      <p class="goal">目标 goal：${esc(m.goal || '')}</p>
      <div>${(m.jd || []).map((k) => `<span class="kw">${esc(k)}</span>`).join('')}</div>
      ${rows ? `<div style="margin-top:8px">${rows}</div>` : '<p class="legend">（暂无课程 no lessons yet）</p>'}
    </div>`);
  }
  parts.push('</div>');

  host.innerHTML = parts.join('');
  $$('[data-lesson]', host).forEach((btn) => {
    btn.addEventListener('click', () => openLesson(btn.dataset.lesson));
  });
}

/* ------------------------------------------------------------------ */
/* view 2 — lessons (marked + KaTeX)                                   */
/* ------------------------------------------------------------------ */

/**
 * Pull math out of the markdown before marked sees it, then put rendered
 * KaTeX back afterwards. Display ($$…$$) first, then inline ($…$).
 */
function extractMath(md) {
  const blocks = [];
  let text = String(md || '').replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => {
    blocks.push({ tex, display: true });
    return `MATHPLACEHOLDER${blocks.length - 1}ENDMATH`;
  });
  text = text.replace(/\$(?!\$)([^$\n]+?)\$(?!\$)/g, (_, tex) => {
    blocks.push({ tex, display: false });
    return `MATHPLACEHOLDER${blocks.length - 1}ENDMATH`;
  });
  return { text, blocks };
}

function renderMathBlocks(md) {
  const { text, blocks } = extractMath(md);
  let html;
  try {
    html = window.marked.parse(text, { gfm: true, breaks: false });
  } catch (err) {
    console.error('marked failed', err);
    html = `<pre>${esc(text)}</pre>`;
  }
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i];
    let rendered;
    try {
      rendered = window.katex.renderToString(b.tex, {
        throwOnError: false,
        displayMode: b.display,
      });
    } catch (err) {
      console.error('katex failed', err);
      rendered = `<code>${esc(b.tex)}</code>`;
    }
    html = html.split(`MATHPLACEHOLDER${i}ENDMATH`).join(rendered);
  }
  return html;
}

function lessonById(id) {
  return ((state.progress || {}).lessons || []).find((l) => l.id === id) || null;
}

async function openLesson(id) {
  const lesson = lessonById(id);
  if (!lesson) return;
  const host = $('#view-lessons');
  host.innerHTML = '<h2>课程 · Lessons</h2><div class="card">载入中 loading…</div>';
  selectTab('lessons');
  try {
    const md = await loadText(`data/${lesson.file}`);
    const body = el('div', { class: 'lesson-body' });
    body.innerHTML = renderMathBlocks(
      md.replace(/<!--\s*sim:(tilt|zloop)\s*-->/g,
        (_, kind) => `<div class="sim-mount" data-sim="${kind}"></div>`));
    host.innerHTML = '';
    host.appendChild(el('h2', { text: `课程 · Lessons — ${lesson.id} ${lesson.en || ''}` }));
    host.appendChild(el('div', { class: 'toolbar' }, [
      el('button', { class: 'btn', type: 'button', onclick: () => renderLessons() }, ['← 返回列表 list']),
      el('span', { class: 'legend', text: `${lesson.zh || ''} · ${lesson.date || ''} · ${lesson.status || ''}` }),
    ]));
    host.appendChild(body);
    mountSimsIn(body);
    state.currentLesson = lesson.id;
  } catch (err) {
    console.error(err);
    host.innerHTML = `<h2>课程 · Lessons</h2><div class="error-box">无法载入 lesson ${esc(id)}: ${esc(err.message)}</div>`;
  }
}

function renderLessons() {
  const host = $('#view-lessons');
  const p = state.progress;
  const parts = ['<h2>课程 · Lessons</h2>'];
  if (!p) { host.innerHTML = parts.join(''); return; }
  for (const l of p.lessons || []) {
    const open = l.file
      ? `<button type="button" data-lesson="${esc(l.id)}">打开 open</button>`
      : '<span class="legend">（未写 not written yet）</span>';
    parts.push(`<div class="card">
      <div class="lesson-row">
        <span class="lid">${esc(l.id)}</span>
        <span class="lname"><strong>${esc(l.en || '')}</strong>${l.zh ? ` · ${esc(l.zh)}` : ''}</span>
        ${statusChip(l.status)}
        ${scoreText(l.scores) ? `<span class="scores">${scoreText(l.scores)}</span>` : ''}
        ${open}
      </div>
    </div>`);
  }
  host.innerHTML = parts.join('');
  $$('[data-lesson]', host).forEach((btn) => {
    btn.addEventListener('click', () => openLesson(btn.dataset.lesson));
  });
}

/* ------------------------------------------------------------------ */
/* view 3 — glossary                                                   */
/* ------------------------------------------------------------------ */

const glossaryState = { q: '', module: '', kind: '' };

function glossaryMatches(entry) {
  const q = glossaryState.q.trim();
  if (!q) return true;
  const needle = stripTones(q);
  const hay = [
    stripTones(entry.zh),
    stripTones(entry.pinyin),
    stripTones(entry.pinyin).replace(/\s+/g, ''),
    stripTones(entry.en),
  ];
  return hay.some((h) => h.includes(needle));
}

function glossaryPool() {
  return state.glossary.filter((e) =>
    glossaryMatches(e) &&
    (!glossaryState.module || e.module === glossaryState.module) &&
    (!glossaryState.kind || e.kind === glossaryState.kind));
}

function renderGlossary() {
  const host = $('#view-glossary');
  host.innerHTML = '';
  host.appendChild(el('h2', { text: '术语表 · Glossary' }));
  host.appendChild(el('p', { class: 'legend', id: 'glossary-count' }));

  const modules = [...new Set(state.glossary.map((e) => e.module))].sort();

  const search = el('input', {
    type: 'search', id: 'glossary-search',
    placeholder: '搜索 汉字 / 拼音 pinyin / English',
    value: glossaryState.q, 'aria-label': 'Search glossary',
  });
  search.addEventListener('input', () => {
    glossaryState.q = search.value;
    renderGlossaryRows();
  });

  const modSel = el('select', { id: 'glossary-module', 'aria-label': 'Module filter' });
  modSel.appendChild(el('option', { value: '', text: '全部模块 all modules' }));
  for (const m of modules) modSel.appendChild(el('option', { value: m, text: m }));
  modSel.value = glossaryState.module;
  modSel.addEventListener('change', () => {
    glossaryState.module = modSel.value;
    renderGlossaryRows();
  });

  const kindSel = el('select', { id: 'glossary-kind', 'aria-label': 'Kind filter' });
  kindSel.appendChild(el('option', { value: '', text: '全部 kind' }));
  kindSel.appendChild(el('option', { value: 'term', text: 'term 术语' }));
  kindSel.appendChild(el('option', { value: 'phrase', text: 'phrase 短语' }));
  kindSel.value = glossaryState.kind;
  kindSel.addEventListener('change', () => {
    glossaryState.kind = kindSel.value;
    renderGlossaryRows();
  });

  host.appendChild(el('div', { class: 'toolbar' }, [
    el('div', { class: 'ctrl' }, [el('label', { text: '搜索 search' }), search]),
    el('div', { class: 'ctrl' }, [el('label', { text: '模块 module' }), modSel]),
    el('div', { class: 'ctrl' }, [el('label', { text: '类型 kind' }), kindSel]),
  ]));

  if (!speech.available()) {
    host.appendChild(el('p', {
      class: 'notice',
      html: '没有找到中文语音，▶ 按钮已禁用。请在 Windows 设置 → 时间和语言 → 语音 中安装中文语音包。 ' +
            '(No Chinese voice found; install one in Windows Settings → Time &amp; language → Speech.)',
    }));
  }

  host.appendChild(el('div', { id: 'glossary-rows' }));
  renderGlossaryRows();
}

function renderGlossaryRows() {
  const host = $('#glossary-rows');
  if (!host) return;
  const all = state.glossary || [];
  const shown = glossaryPool();

  const count = $('#glossary-count');
  if (count) count.textContent = `共 ${all.length} 条 · 当前显示 ${shown.length} 条`;

  if (!shown.length) {
    host.innerHTML = '<p class="legend">没有匹配的条目 no matches</p>';
    return;
  }

  const audioOk = speech.available();
  host.innerHTML = shown.map((e) => `<div class="gloss-row">
      <span class="hanzi">${hanziHtml(e.zh, e.pinyin)}</span>
      <span class="pinyin">${pinyinHtml(e.pinyin)}</span>
      <span class="en">${esc(e.en)}</span>
      <span class="tag">${esc(e.module)} · ${esc(e.lesson)}${e.kind === 'phrase' ? ' · phrase' : ''}</span>
      <button class="speak" type="button" data-say="${esc(e.zh)}"${audioOk ? '' : ' disabled'} title="朗读 read aloud">▶</button>
    </div>`).join('');

  $$('[data-say]', host).forEach((btn) => {
    btn.addEventListener('click', () => speech.speak(btn.dataset.say));
  });
}

/* ------------------------------------------------------------------ */
/* view 4 — flashcards (Leitner)                                       */
/* ------------------------------------------------------------------ */

const BOX_INTERVALS = [0, 1, 3, 7, 14]; // days, boxes 1..5

const todayKey = (d = new Date()) => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

const addDays = (key, days) => {
  const d = new Date(`${key}T00:00:00`);
  d.setDate(d.getDate() + days);
  return todayKey(d);
};

function loadSrs() {
  const raw = lsGet(KEY_SRS, {});
  return raw && typeof raw === 'object' ? raw : {};
}

const cardState = (id) => {
  const srs = loadSrs();
  return srs[id] || { box: 1, due: todayKey() };
};

const flashPrefs = lsGet(KEY_FC, {});
const flashState = {
  direction: flashPrefs && flashPrefs.direction ? flashPrefs.direction : 'zh2en',
  lesson: flashPrefs && flashPrefs.lesson ? flashPrefs.lesson : '',
  hint: false,
  revealed: false,
  current: null,
};

const saveFlashPrefs = () =>
  lsSet(KEY_FC, { direction: flashState.direction, lesson: flashState.lesson });

function flashPool() {
  return state.glossary.filter((e) => !flashState.lesson || e.lesson === flashState.lesson);
}

function dueEntries() {
  const srs = loadSrs();
  const today = todayKey();
  return flashPool().filter((e) => !srs[e.id] || String(srs[e.id].due) <= today);
}

function renderFlashcards() {
  const host = $('#view-flashcards');
  const lessons = [...new Set(state.glossary.map((e) => e.lesson))].sort();
  const pool = flashPool();
  const due = dueEntries();

  host.innerHTML = '';
  host.appendChild(el('h2', { text: '闪卡 · Flashcards' }));
  host.appendChild(el('p', {
    class: 'legend',
    html: `Leitner 5 盒 · 间隔 0/1/3/7/14 天 · 今天到期 due today: <strong>${due.length}</strong> / ${pool.length}`,
  }));

  const dirSel = el('select', { id: 'fc-direction', 'aria-label': 'Direction' });
  dirSel.appendChild(el('option', { value: 'zh2en', text: '中文 → English' }));
  dirSel.appendChild(el('option', { value: 'en2zh', text: 'English → 中文' }));
  dirSel.value = flashState.direction;
  dirSel.addEventListener('change', () => {
    flashState.direction = dirSel.value;
    flashState.current = null;
    flashState.revealed = false;
    saveFlashPrefs();
    renderFlashcards();
  });

  const lesSel = el('select', { id: 'fc-lesson', 'aria-label': 'Lesson filter' });
  lesSel.appendChild(el('option', { value: '', text: '全部课程 all lessons' }));
  for (const l of lessons) lesSel.appendChild(el('option', { value: l, text: l }));
  lesSel.value = flashState.lesson;
  lesSel.addEventListener('change', () => {
    flashState.lesson = lesSel.value;
    flashState.current = null;
    flashState.revealed = false;
    saveFlashPrefs();
    renderFlashcards();
  });

  const hintBtn = el('button', {
    class: 'btn', type: 'button',
    onclick: () => { flashState.hint = !flashState.hint; renderFlashCard(); },
  }, [flashState.hint ? '隐藏拼音 hide pinyin' : '提示拼音 pinyin hint']);

  host.appendChild(el('div', { class: 'toolbar' }, [
    el('div', { class: 'ctrl' }, [el('label', { text: '方向 direction' }), dirSel]),
    el('div', { class: 'ctrl' }, [el('label', { text: '课程 lesson' }), lesSel]),
    hintBtn,
  ]));

  host.appendChild(el('div', { id: 'fc-card' }));
  renderFlashCard();
}

function pickNextCard() {
  const pool = flashPool();
  if (!pool.length) return null;
  const due = dueEntries();
  const list = due.length ? due : pool;
  return list[Math.floor(Math.random() * list.length)];
}

function renderFlashCard() {
  const host = $('#fc-card');
  if (!host) return;
  if (!flashState.current) flashState.current = pickNextCard();
  const entry = flashState.current;
  if (!entry) {
    host.innerHTML = '<p class="legend">没有卡片 no cards</p>';
    return;
  }
  const rec = cardState(entry.id);
  const zh2en = flashState.direction === 'zh2en';
  const prompt = zh2en ? entry.zh : entry.en;
  const answer = zh2en ? entry.en : entry.zh;

  const answerHtml = flashState.revealed
    ? `<div class="answer">${esc(answer)}</div>
       ${zh2en ? '' : `<div class="hint">${pinyinHtml(entry.pinyin)}</div>`}`
    : '';

  const hintHtml = flashState.hint && !flashState.revealed
    ? `<div class="hint">${pinyinHtml(entry.pinyin)}</div>`
    : '';

  host.innerHTML = `<div class="card-face">
    <div class="prompt${zh2en ? '' : ' small'}">${esc(prompt)}</div>
    ${hintHtml}
    ${answerHtml}
    <div class="meta">${esc(entry.module)} · ${esc(entry.lesson)} · 盒 box ${rec.box}/5${rec.due ? ` · 到期 due ${esc(rec.due)}` : ''}</div>
    <div class="toolbar" style="justify-content:center">
      ${!flashState.revealed
        ? '<button class="btn primary" type="button" id="fc-reveal">显示答案 show answer</button>'
        : '<button class="btn" type="button" id="fc-missed">missed 没记住</button>' +
          '<button class="btn primary" type="button" id="fc-knew">knew it 记得了</button>'}
      <button class="btn" type="button" id="fc-skip">跳过 skip</button>
    </div>
  </div>`;

  const reveal = $('#fc-reveal');
  if (reveal) reveal.addEventListener('click', () => { flashState.revealed = true; renderFlashCard(); });

  const knew = $('#fc-knew');
  if (knew) knew.addEventListener('click', () => {
    const srs = loadSrs();
    const box = Math.min(5, (srs[entry.id] ? srs[entry.id].box : 1) + 1);
    srs[entry.id] = { box, due: addDays(todayKey(), BOX_INTERVALS[box - 1]) };
    lsSet(KEY_SRS, srs);
    flashState.current = null; flashState.revealed = false; flashState.hint = false;
    renderFlashcards();
  });

  const missed = $('#fc-missed');
  if (missed) missed.addEventListener('click', () => {
    const srs = loadSrs();
    srs[entry.id] = { box: 1, due: addDays(todayKey(), BOX_INTERVALS[0]) };
    lsSet(KEY_SRS, srs);
    flashState.current = null; flashState.revealed = false; flashState.hint = false;
    renderFlashcards();
  });

  const skip = $('#fc-skip');
  if (skip) skip.addEventListener('click', () => {
    flashState.current = null; flashState.revealed = false;
    renderFlashCard();
  });
}

/* ------------------------------------------------------------------ */
/* view 5 — break points                                              */
/* ------------------------------------------------------------------ */

function renderBreakpoints() {
  const host = $('#view-breakpoints');
  const p = state.progress;
  if (!p) { host.innerHTML = ''; return; }
  const order = { open: 0, retested: 1, fixed: 2 };
  const bps = [...(p.breakpoints || [])].sort((a, b) => {
    const d = (order[a.status] ?? 9) - (order[b.status] ?? 9);
    if (d !== 0) return d;
    return String(a.date || '').localeCompare(String(b.date || ''));
  });

  const parts = ['<h2>断点 · Break points</h2>'];
  if (!bps.length) parts.push('<p class="legend">暂无断点 no break points</p>');
  for (const b of bps) {
    parts.push(`<div class="card">
      <div class="lesson-row">
        <span class="lid">${esc(b.date || '')}</span>
        <span class="lname"><strong>${esc(b.topic || '')}</strong></span>
        <span class="chip ${esc(b.status || 'open')}">${esc(b.status || 'open')}</span>
        <span class="tag">${esc(b.lesson || '')}</span>
      </div>
      <p class="goal">${esc(b.gap || '')}</p>
    </div>`);
  }

  parts.push('<h2>测验成绩 · Quiz scores</h2>');
  const scored = (p.lessons || []).filter((l) => l.scores && Object.keys(l.scores).length);
  if (!scored.length) {
    parts.push('<p class="legend">暂无成绩 no scores yet</p>');
  } else {
    parts.push('<div class="table-wrap"><table><thead><tr><th>lesson</th><th>module</th><th>题 question</th><th>分数 score</th></tr></thead><tbody>');
    for (const l of scored) {
      for (const [q, s] of Object.entries(l.scores)) {
        parts.push(`<tr><td>${esc(l.id)}</td><td>${esc(l.module)}</td><td>${esc(q)}</td><td>${esc(s)}</td></tr>`);
      }
    }
    parts.push('</tbody></table></div>');
  }
  host.innerHTML = parts.join('');
}

/* ------------------------------------------------------------------ */
/* view 6 — sims (free play)                                          */
/* ------------------------------------------------------------------ */

function mountSimsIn(root) {
  for (const host of $$('.sim-mount', root)) {
    const kind = host.dataset.sim;
    try {
      if (kind === 'tilt') mountTilt(host);
      else if (kind === 'zloop') mountZloop(host);
    } catch (err) {
      console.error('sim mount failed', kind, err);
      host.innerHTML = `<div class="error-box">仿真组件加载失败 sim failed: ${esc(err.message)}</div>`;
    }
  }
}

function renderSims() {
  const host = $('#view-sims');
  host.innerHTML = `<h2>仿真 · Sims</h2>
    <p class="legend">两个小仿真 free play：W1 倾角与推力矢量 · W2 垂直环载荷阶跃</p>
    <div class="sim-mount" data-sim="tilt"></div>
    <div class="sim-mount" data-sim="zloop"></div>`;
  mountSimsIn(host);
}

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */

async function boot() {
  buildTabs();
  try {
    const [progress, glossary] = await Promise.all([
      loadJson('data/progress.json'),
      loadJson('data/glossary.json'),
    ]);
    state.progress = progress;
    state.glossary = Array.isArray(glossary) ? glossary : [];
  } catch (err) {
    console.error(err);
    document.querySelector('main').innerHTML =
      `<div class="error-box">数据载入失败 failed to load data: ${esc(err.message)}</div>`;
    selectTab('roadmap');
    return;
  }

  renderRoadmap();
  renderLessons();
  renderGlossary();
  renderFlashcards();
  renderBreakpoints();
  renderSims();
  selectTab(state.activeTab);
}

boot();
