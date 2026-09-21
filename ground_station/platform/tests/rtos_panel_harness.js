/* Offline render harness for resource-panel.js (task J1 verification;
 * assertions added for task 20260921-141231).
 *
 * Executes the REAL, unmodified plugin in a minimal vm sandbox: stub DOM
 * (getElementById / innerHTML / textContent / style), no jsdom. Drives two
 * scenarios — bridge running with a faked SWD sample, and bridge absent —
 * prints the rendered cell text and asserts:
 *   A. Bridge running: sourced cells carry the real values (never NO DATA,
 *      never a bare —); the structurally-unpublished cell stays n/p; the
 *      hint switches to its bridge-on note.
 *   B. Bridge absent: every sourced cell reads "NO DATA" with the
 *      res-no-data class (honesty rule); n/p cells remain n/p; the hint
 *      names the missing bridge.
 * Exit code 0 iff every assertion passes.
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const PANEL = path.resolve(__dirname,
  '../../../docs/dashboard-platform/shell/plugins/resource-panel.js');

function makeEl(id) {
  return { id, textContent: '', className: '', style: {} };
}
const elements = {};
function getEl(id) { return elements[id] || (elements[id] = makeEl(id)); }

const container = {
  _html: '',
  set innerHTML(html) {
    this._html = html;
    // Register each id-bearing tag; approximate its static textContent.
    const re = /<(\w+)([^>]*)\sid="([^"]+)"([^>]*)>([\s\S]*?)<\/\1>/g;
    let m;
    while ((m = re.exec(html))) {
      const el = getEl(m[3]);
      el.textContent = m[5].replace(/<[^>]+>/g, '')
        .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
        .replace(/\s+/g, ' ').trim();
    }
  },
  get innerHTML() { return this._html; },
};

const document = { getElementById: getEl };
const window = { __registerPlugin__: function () {} };
vm.runInNewContext(fs.readFileSync(PANEL, 'utf8'), { window, document, console });

let panelFn, onStateFn;
window.__PLUGIN_INIT__({
  registerPanel: function (name, fn) { panelFn = fn; },
  subscribe: function (fn) { onStateFn = fn; },
});

const CELL_IDS = [
  'rtos.scheduler_tick_count', 'rtos.heap_free_bytes',
  'rtos.usart3_tx_count', 'rtos.usart3_tx_bytes',
  'rtos.cmd_queue_depth', 'rtos.cmd_queue_max',
  'rtos.send_task_ticks', 'rtos.dma_busy',
  'rtos.queue_depth', 'rtos.usart3_tx_drops',
];
const SOURCED_IDS = CELL_IDS.filter((k) => k !== 'rtos.usart3_tx_bytes');
function cellEl(k) { return getEl('res-' + k.replace(/\./g, '-')); }
function dumpCells() {
  CELL_IDS.forEach(function (k) {
    const el = cellEl(k);
    console.log('  ' + k + ' = "' + el.textContent + '" class="' +
      el.className + '"');
  });
}

// ── Scenario A: bridge running, faked SWD sample ──
panelFn(container);
onStateFn({ streams: { rtos: { tag: 'rtos', values: {
  'rtos.send_task_ticks': 12345, 'rtos.queue_depth': 27, 'rtos.dma_busy': 1,
  'rtos.usart3_tx_drops': 3, 'rtos.cmd_queue_depth': 4, 'rtos.cmd_queue_max': 16,
  'rtos.heap_free_bytes': 100000, 'rtos.usart3_tx_count': 54321,
  'rtos.scheduler_tick_count': 888888,
} } } });
console.log('SCENARIO A: RTOS bridge running (faked SWD sample)');
dumpCells();

const expected = {
  'rtos.scheduler_tick_count': '888,888',
  'rtos.heap_free_bytes': '100,000',
  'rtos.usart3_tx_count': '54,321',
  'rtos.cmd_queue_depth': '4',
  'rtos.cmd_queue_max': '16',
  'rtos.send_task_ticks': '12,345',
  'rtos.dma_busy': 'YES',
  'rtos.queue_depth': '27',
  'rtos.usart3_tx_drops': '3',
};
SOURCED_IDS.forEach(function (k) {
  const el = cellEl(k);
  assert.strictEqual(el.textContent, expected[k],
    'A: ' + k + ' must render its real SWD value');
  assert.notStrictEqual(el.textContent, 'NO DATA');
  assert.notStrictEqual(el.textContent, '—');
  assert.notStrictEqual(el.className, 'res-no-data');
});
// Build limitation: n/p even with the bridge alive, and never NO DATA/0.
assert.strictEqual(cellEl('rtos.usart3_tx_bytes').textContent, 'n/p');
let hint = getEl('res-rtos-hint');
assert.ok(hint.textContent.indexOf('Fields marked n/p') !== -1,
  'A: bridge-on hint, got: ' + hint.textContent);
assert.strictEqual(hint.style.display, '');
console.log('  ASSERT PASS: 9 sourced cells hold real values; tx_bytes stays n/p; hint = bridge-on note');

// ── Scenario B: bridge not running ──
panelFn(container);
onStateFn({ streams: {} });
console.log('\nSCENARIO B: RTOS bridge not running');
dumpCells();

SOURCED_IDS.forEach(function (k) {
  const el = cellEl(k);
  assert.strictEqual(el.textContent, 'NO DATA',
    'B: ' + k + ' must read NO DATA, got: ' + el.textContent);
  assert.strictEqual(el.className, 'res-no-data',
    'B: ' + k + ' must carry res-no-data class');
});
// Structurally unpublished is a different state and must not be relabeled.
assert.strictEqual(cellEl('rtos.usart3_tx_bytes').textContent, 'n/p');
assert.notStrictEqual(cellEl('rtos.usart3_tx_bytes').className, 'res-no-data');
hint = getEl('res-rtos-hint');
assert.ok(hint.textContent.indexOf('RTOS bridge is not running') !== -1,
  'B: bridge-off hint, got: ' + hint.textContent);
assert.strictEqual(hint.style.color, 'var(--amber)');
console.log('  ASSERT PASS: 9 sourced cells read NO DATA with res-no-data; tx_bytes stays n/p;');
console.log('              hint names the missing --rtos-bridge flag (amber)');

console.log('\nALL ASSERTIONS PASSED.');
