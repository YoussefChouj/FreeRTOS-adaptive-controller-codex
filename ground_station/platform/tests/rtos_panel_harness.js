/* Offline render harness for resource-panel.js (task J1 verification).
 *
 * Executes the REAL, unmodified plugin in a minimal vm sandbox: stub DOM
 * (getElementById / innerHTML / textContent / style), no jsdom. Drives two
 * scenarios — bridge running with a faked SWD sample, and bridge absent —
 * and prints the rendered cell text verbatim.
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

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
function dumpCells() {
  CELL_IDS.forEach(function (k) {
    const el = getEl('res-' + k.replace(/\./g, '-'));
    console.log('  ' + k + ' = "' + el.textContent + '"');
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
console.log('  hint = "' + getEl('res-rtos-hint').textContent + '"');
console.log('  hint display = "' + getEl('res-rtos-hint').style.display + '"');

// ── Scenario B: bridge not running ──
panelFn(container);
onStateFn({ streams: {} });
console.log('SCENARIO B: RTOS bridge not running');
dumpCells();
console.log('  hint = "' + getEl('res-rtos-hint').textContent + '"');
console.log('  hint display = "' + getEl('res-rtos-hint').style.display + '"');
