// .agent-ops/verify_d1.js — Verification harness for D1 (Branching command parameter form, range validation, fallback, readback)
var fs = require('fs');
var path = require('path');

console.log('=== D1 VERIFICATION START ===');

var elementRegistry = {};

// Minimal DOM implementation for Node.js
function createElement(tag) {
  var listeners = {};
  var children = [];
  var styleObj = {};
  var attrs = {};
  var el = {
    tagName: tag.toUpperCase(),
    style: styleObj,
    children: children,
    parentNode: null,
    parentElement: null,
    _value: '',
    get value() { return this._value; },
    set value(v) { this._value = String(v); },
    _innerHTML: '',
    get innerHTML() { return this._innerHTML; },
    set innerHTML(html) {
      this._innerHTML = String(html);
      this.children.length = 0;
      this._textContent = '';
      parseHtml(this._innerHTML, this);
    },
    _textContent: '',
    get textContent() {
      var own = this._textContent || '';
      if (!this.children || this.children.length === 0) return own;
      var kids = this.children.map(function(c) { return c.textContent; }).join(' ');
      return (own + ' ' + kids).replace(/\s+/g, ' ').trim();
    },
    set textContent(v) { this._textContent = String(v); },
    className: '',
    id: '',
    dataset: {},
    addEventListener: function(evt, fn) {
      if (!listeners[evt]) listeners[evt] = [];
      listeners[evt].push(fn);
    },
    dispatchEvent: function(evt) {
      var name = typeof evt === 'string' ? evt : evt.type;
      var fns = listeners[name] || [];
      for (var i = 0; i < fns.length; i++) {
        fns[i].call(this, evt);
      }
    },
    setAttribute: function(k, v) {
      attrs[k] = String(v);
      if (k === 'id') {
        this.id = v;
        elementRegistry[v] = this;
      }
      if (k === 'class') this.className = v;
      if (k.indexOf('data-') === 0) {
        var dKey = k.slice(5).replace(/-([a-z])/g, function(_, c) { return c.toUpperCase(); });
        this.dataset[dKey] = v;
      }
    },
    getAttribute: function(k) { return attrs[k] || (k === 'id' ? this.id : null); },
    removeAttribute: function(k) { delete attrs[k]; },
    querySelector: function(sel) {
      return querySelect(this, sel);
    },
    querySelectorAll: function(sel) {
      var res = [];
      querySelectAll(this, sel, res);
      return res;
    },
    appendChild: function(c) {
      c.parentNode = this;
      c.parentElement = this;
      this.children.push(c);
      return c;
    }
  };
  var classList = {
    add: function(c) {
      var arr = el.className ? el.className.split(/\s+/) : [];
      if (!arr.includes(c)) arr.push(c);
      el.className = arr.join(' ');
    },
    remove: function(c) {
      var arr = el.className ? el.className.split(/\s+/) : [];
      arr = arr.filter(function(x) { return x !== c; });
      el.className = arr.join(' ');
    },
    contains: function(c) {
      var arr = el.className ? el.className.split(/\s+/) : [];
      return arr.includes(c);
    },
    toggle: function(c, force) {
      if (force === undefined) force = !this.contains(c);
      if (force) this.add(c); else this.remove(c);
    }
  };
  el.classList = classList;
  return el;
}

// Stack-based HTML parser to mock nested elements accurately
function parseHtml(html, parent) {
  var root = parent || createElement('div');
  var stack = [root];
  var tagRegex = /<!--[\s\S]*?-->|<(\/)?([a-zA-Z0-9\-]+)([^>]*)>/g;
  var lastIdx = 0;
  var match;

  while ((match = tagRegex.exec(html)) !== null) {
    var text = html.slice(lastIdx, match.index);
    if (text && stack.length > 0) {
      var trimmed = text.trim();
      if (trimmed) {
        var cur = stack[stack.length - 1];
        cur._textContent = (cur._textContent ? cur._textContent + ' ' : '') + trimmed;
      }
    }
    lastIdx = tagRegex.lastIndex;

    var isComment = match[0].startsWith('<!--');
    if (isComment) continue;

    var isClosing = match[1] === '/';
    var tagName = match[2].toLowerCase();
    var attrStr = match[3] || '';

    var isVoid = ['input', 'img', 'br', 'hr', 'meta', 'link'].includes(tagName) || attrStr.trim().endsWith('/');

    if (isClosing) {
      for (var i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName.toLowerCase() === tagName) {
          stack.length = i;
          break;
        }
      }
    } else {
      var el = createElement(tagName);
      // Parse attributes
      var attrRegex = /([a-zA-Z0-9\-]+)(?:=["']([^"']*)["'])?/g;
      var aMatch;
      while ((aMatch = attrRegex.exec(attrStr)) !== null) {
        el.setAttribute(aMatch[1], aMatch[2] !== undefined ? aMatch[2] : '');
      }
      var parentNode = stack[stack.length - 1];
      parentNode.appendChild(el);
      if (!isVoid) {
        stack.push(el);
      }
    }
  }
  var trailingText = html.slice(lastIdx).trim();
  if (trailingText && stack.length > 0) {
    var last = stack[stack.length - 1];
    last._textContent = (last._textContent ? last._textContent + ' ' : '') + trailingText;
  }
  return root.children;
}

function querySelect(root, sel) {
  var all = [];
  querySelectAll(root, sel, all);
  return all[0] || null;
}

function querySelectAll(node, sel, res) {
  if (!node) return;
  // Check self
  var match = false;
  if (sel.startsWith('#') && node.id === sel.slice(1)) match = true;
  else if (sel.startsWith('.') && node.className && node.className.split(/\s+/).includes(sel.slice(1))) match = true;
  else if (sel.startsWith('[data-') && sel.endsWith('"]')) {
    var parts = sel.slice(1, -2).split('="');
    var aName = parts[0];
    var aVal = parts[1];
    if (node.getAttribute && node.getAttribute(aName) === aVal) match = true;
  } else if (sel.includes('.')) {
    var parts = sel.split('.');
    var tName = parts[0].toUpperCase();
    var cName = parts[1];
    if (parts[0] && node.tagName === tName && node.className && node.className.split(/\s+/).includes(cName)) match = true;
  } else if (node.tagName && node.tagName.toLowerCase() === sel.toLowerCase()) {
    match = true;
  }
  if (match) res.push(node);

  var ch = node.children || [];
  for (var i = 0; i < ch.length; i++) {
    querySelectAll(ch[i], sel, res);
  }
}

// Set up globals
var elementRegistry = {};
global.document = {
  getElementById: function(id) {
    if (elementRegistry[id]) return elementRegistry[id];
    var all = [];
    querySelectAll(bodyContainer, '#' + id, all);
    if (all[0]) {
      elementRegistry[id] = all[0];
      return all[0];
    }
    return null;
  },
  querySelectorAll: function(sel) {
    var all = [];
    querySelectAll(bodyContainer, sel, all);
    return all;
  },
  createElement: createElement
};

global.window = {
  addEventListener: function() {},
  __registerPlugin__: function(name, init, destroy) {
    window.__PLUGIN_NAME__ = name;
    window.__PLUGIN_INIT__ = init;
    window.__PLUGIN_DESTROY__ = destroy;
  }
};

global.localStorage = {
  _store: {},
  getItem: function(k) { return this._store[k] || null; },
  setItem: function(k, v) { this._store[k] = String(v); },
  removeItem: function(k) { delete this._store[k]; }
};

var submittedCommands = [];
var subscribedSlots = [];

global.fetch = function(url, opts) {
  opts = opts || {};
  if (url === '/api/contract') {
    return Promise.resolve({
      json: function() {
        return Promise.resolve({
          contract_version: '1.0.0',
          commands: {
            '0x07': {
              name: 'BENCH_MODE',
              params: [{ index: 0, name: 'enable', unit: 'bool', min_val: 0, max_val: 1, symbol: null }]
            },
            '0x1E': {
              name: 'OF_BIAS_MODE',
              params: [
                { index: 0, name: 'bias_mode', unit: 'enum', min_val: 0, max_val: 2, symbol: 'g_of_bias_mode' },
                { index: 1, name: 'ema_freeze', unit: 'bool', min_val: 0, max_val: 1, symbol: 'g_of_bias_ema_freeze' }
              ]
            }
          }
        });
      }
    });
  }
  if (url === '/subscribe') {
    var body = JSON.parse(opts.body || '{}');
    subscribedSlots.push(body);
    return Promise.resolve({ json: function() { return Promise.resolve({ status: 'ok' }); } });
  }
  return Promise.resolve({ json: function() { return Promise.resolve({}); } });
};

var bodyContainer = createElement('div');
var mockApi = {
  registerPanel: function(name, fn) {
    fn(bodyContainer);
  },
  submitCommand: function(cmdId, index, value) {
    submittedCommands.push({ cmdId: cmdId, index: index, value: value });
    return Promise.resolve({ transaction_id: 'tx-' + Date.now() });
  },
  subscribe: function(cb) {
    mockApi._subscriber = cb;
  },
  getState: function() {
    return mockApi._state || null;
  }
};

// Load command-panel.js
var panelCode = fs.readFileSync(path.join(__dirname, '..', 'docs', 'dashboard-platform', 'shell', 'plugins', 'command-panel.js'), 'utf8');
eval(panelCode);

// Initialize plugin
window.__PLUGIN_INIT__(mockApi);

var cmdSelect = document.getElementById('cp-cmd-id');
var branchContainer = document.getElementById('cp-branching-container');
var rawContainer = document.getElementById('cp-raw-container');
var modeToggle = document.getElementById('cp-mode-toggle');
var resultBox = document.getElementById('cp-result-box');
var readbackVal = document.getElementById('cp-readback-val');

function dumpRenderedFields(container) {
  var cards = container.querySelectorAll('.cp-param-card');
  var out = [];
  cards.forEach(function(card) {
    var label = card.querySelector('label');
    var range = card.querySelector('.cp-param-range');
    var input = card.querySelector('.cp-param-input');
    var readback = card.querySelector('.cp-param-readback');
    out.push({
      label: label ? label.textContent.trim() : '',
      range: range ? range.textContent.trim() : '',
      min: input ? input.getAttribute('min') : null,
      max: input ? input.getAttribute('max') : null,
      step: input ? input.getAttribute('step') : null,
      readback: readback ? readback.textContent.trim() : null
    });
  });
  return out;
}

// ── Test 1: Command 0x07 (BENCH_MODE) ───────────────────────────────────────
console.log('\n--- Test 1: Selecting Command 0x07 (Bench Mode) ---');
cmdSelect.value = 0x07;
cmdSelect.dispatchEvent({ type: 'change' });

var fields07 = dumpRenderedFields(branchContainer);
console.log('Rendered fields for 0x07 (' + fields07.length + ' parameter):');
fields07.forEach(function(f, i) {
  console.log('  [' + i + '] Label: "' + f.label + '", ' + f.range + ', min=' + f.min + ', max=' + f.max);
});
if (fields07.length !== 1 || fields07[0].label.indexOf('enable') === -1 || fields07[0].label.indexOf('bool') === -1) {
  throw new Error('Test 1 failed: 0x07 did not render expected enable (bool) field');
}
console.log('PASS: Command 0x07 renders only its parameter with real name "enable" and unit "bool"');

// ── Test 2: Command 0x1E (OF_BIAS_MODE) ─────────────────────────────────────
console.log('\n--- Test 2: Selecting Command 0x1E (OF Bias Estimator Mode) ---');
cmdSelect.value = 0x1E;
cmdSelect.dispatchEvent({ type: 'change' });

var fields1E = dumpRenderedFields(branchContainer);
console.log('Rendered fields for 0x1E (' + fields1E.length + ' parameters):');
fields1E.forEach(function(f, i) {
  console.log('  [' + i + '] Label: "' + f.label + '", ' + f.range + ', min=' + f.min + ', max=' + f.max);
});
if (fields1E.length !== 2 || fields1E[0].label.indexOf('bias_mode') === -1 || fields1E[1].label.indexOf('ema_freeze') === -1) {
  throw new Error('Test 2 failed: 0x1E did not render expected bias_mode and ema_freeze fields');
}
console.log('PASS: Commands 0x07 and 0x1E differ and are labelled with real names and units');

// ── Test 3: Out-of-Range Rejection Before Send ──────────────────────────────
console.log('\n--- Test 3: Out-of-Range Rejection ---');
submittedCommands = [];
var inputBiasMode = document.getElementById('cp-param-input-0');
inputBiasMode.value = '3'; // Out of range: max is 2

// Find Send button for bias_mode
var sendBiasBtn = document.getElementById('cp-param-send-btn-0');

sendBiasBtn.dispatchEvent({ type: 'click' });

console.log('Result box content after submitting value 3 (max 2):');
console.log('  ' + resultBox.innerHTML);
console.log('Commands sent to API: ' + submittedCommands.length);

if (submittedCommands.length !== 0) {
  throw new Error('Test 3 failed: Out-of-range command was sent to API!');
}
if (resultBox.innerHTML.indexOf('Refused') === -1 && resultBox.innerHTML.indexOf('exceeds maximum') === -1) {
  throw new Error('Test 3 failed: Out-of-range rejection message not rendered in result box');
}
console.log('PASS: Out-of-range value 3 was refused before send with visible rejection message');

// Test lower bound out-of-range rejection:
inputBiasMode.value = '-1'; // Below min 0
sendBiasBtn.dispatchEvent({ type: 'click' });
console.log('Result box content after submitting value -1 (min 0):');
console.log('  ' + resultBox.innerHTML);
if (submittedCommands.length !== 0) {
  throw new Error('Test 3 failed: Negative out-of-range command was sent to API!');
}
console.log('PASS: Out-of-range value -1 was refused before send');

// ── Test 4: Raw Index/Value Fallback ────────────────────────────────────────
console.log('\n--- Test 4: Raw Index/Value Fallback ---');
console.log('Initial visibility: branching=' + branchContainer.style.display + ', raw=' + rawContainer.style.display);
console.log('Clicking mode toggle to switch to raw fallback mode...');
modeToggle.dispatchEvent({ type: 'click' });

console.log('After toggle: branching=' + branchContainer.style.display + ', raw=' + rawContainer.style.display);
console.log('Mode toggle button text: "' + modeToggle.textContent + '"');

if (rawContainer.style.display !== '' || branchContainer.style.display !== 'none') {
  throw new Error('Test 4 failed: Raw container is not visible after toggle');
}

// Submit via raw form
var rawIdxInput = document.getElementById('cp-cmd-idx');
var rawValInput = document.getElementById('cp-cmd-val');
var rawForm = document.getElementById('cp-form');

rawIdxInput.value = '1';
rawValInput.value = '0';
rawForm.dispatchEvent({ type: 'submit', preventDefault: function() {} });

console.log('Submitted commands count via raw form: ' + submittedCommands.length);
console.log('Submitted command via raw form:', submittedCommands[0]);

if (submittedCommands.length !== 1 || submittedCommands[0].cmdId !== 0x1E || submittedCommands[0].index !== 1 || submittedCommands[0].value !== 0) {
  throw new Error('Test 4 failed: Raw form submission did not dispatch expected command');
}
console.log('PASS: Raw index/value fallback successfully submitted command');

// Toggle back to parameter form
modeToggle.dispatchEvent({ type: 'click' });
console.log('Toggled back to parameter form: branching=' + branchContainer.style.display + ', raw=' + rawContainer.style.display);
if (branchContainer.style.display !== '' || rawContainer.style.display !== 'none') {
  throw new Error('Test 4 failed: Parameter form did not restore after toggle');
}
console.log('PASS: Toggled back to branching parameter form');

// ── Test 5: D2 Readback States for Command with Symbol (0x1E) ───────────────
console.log('\n--- Test 5: D2 Readback States (0x1E) ---');
// State 1: Mapped, but not currently being read
cmdSelect.value = 0x1E;
cmdSelect.dispatchEvent({ type: 'change' });
var readbackInput0 = document.getElementById('cp-param-input-0');
readbackInput0.dispatchEvent({ type: 'focus' });

console.log('State 1 (unsubscribed/no stream):');
console.log('  #cp-readback-val: ' + readbackVal.innerHTML);
if (readbackVal.innerHTML.indexOf('mapped, but not currently being read') === -1) {
  throw new Error('Test 5 failed: State 1 readback does not show "mapped, but not currently being read"');
}
console.log('PASS: State 1 verified ("mapped, but not currently being read")');

// State 2: Live readback
console.log('\nState 2 (live telemetry with symbol value):');
var nowNs = 1700000000 * 1e9;
var stateLive = {
  now_ns: nowNs,
  streams: {
    1: {
      last_update_ns: nowNs - 0.2 * 1e9, // 0.2s ago (< 2.0s => live)
      values: {
        'slot1.g_of_bias_mode': 2
      }
    }
  }
};
mockApi._subscriber(stateLive);
console.log('  #cp-readback-val: ' + readbackVal.innerHTML);
if (readbackVal.innerHTML.indexOf('font-weight:bold') === -1 || readbackVal.innerHTML.indexOf('2') === -1) {
  throw new Error('Test 5 failed: State 2 readback does not show live green value');
}
console.log('PASS: State 2 verified (live green value "2")');

// State 3: Stale readback (> 2.0s ago)
console.log('\nState 3 (stale telemetry > 2.0s ago):');
var stateStale = {
  now_ns: nowNs,
  streams: {
    1: {
      last_update_ns: nowNs - 4.5 * 1e9, // 4.5s ago (> 2.0s => stale)
      values: {
        'slot1.g_of_bias_mode': 2
      }
    }
  }
};
mockApi._subscriber(stateStale);
console.log('  #cp-readback-val: ' + readbackVal.innerHTML);
if (readbackVal.innerHTML.indexOf('read 4.5s ago') === -1 || readbackVal.innerHTML.indexOf('var(--amber)') === -1) {
  throw new Error('Test 5 failed: State 3 readback does not show stale amber age indicator');
}
console.log('PASS: State 3 verified (stale amber value "2 (read 4.5s ago)")');

// State 4: Command without symbol (0x07)
console.log('\nState 4 (command without symbol: 0x07):');
cmdSelect.value = 0x07;
cmdSelect.dispatchEvent({ type: 'change' });
console.log('  #cp-readback-val: ' + readbackVal.innerHTML);
if (readbackVal.innerHTML.indexOf('no readback mapping for this command') === -1) {
  throw new Error('Test 5 failed: State 4 readback does not show "no readback mapping for this command"');
}
console.log('PASS: State 4 verified ("no readback mapping for this command")');

console.log('\n=== D1 ALL CHECKS PASSED ===');
