'use strict';
/**
 * Loads docs/dashboard-platform/shell/ui/ui-kit.js and ui/alarms.js into a vm context, the way index.html loads them
 * before any plugin, so plugin harnesses see window.GSUI and window.GSAlarms. The context must define `window` (and
 * the timers / fetch the harness wants the kit to use).
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const UI_DIR = path.join(__dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'ui');
const KIT = path.join(UI_DIR, 'ui-kit.js');
const ALARMS = path.join(UI_DIR, 'alarms.js');

function preloadAlarms(ctx) {
  vm.runInContext(fs.readFileSync(ALARMS, 'utf8'), ctx, { filename: ALARMS });
  return ctx;
}

function preloadKit(ctx) {
  vm.runInContext(fs.readFileSync(KIT, 'utf8'), ctx, { filename: KIT });
  return preloadAlarms(ctx);
}

module.exports = { KIT, ALARMS, preloadKit, preloadAlarms };
