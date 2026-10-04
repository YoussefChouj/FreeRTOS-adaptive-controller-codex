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

// A getComputedStyle stand-in that resolves --gs-* tokens from ui/tokens.css (first definition = dark theme), so
// canvas / WebGL code that reads tokens through GSUI.tokenColor sees the values a browser would.
function tokenStyle() {
  const css = fs.readFileSync(path.join(UI_DIR, 'tokens.css'), 'utf8');
  const vals = {};
  for (const m of css.matchAll(/(--gs-[\w-]+)\s*:\s*([^;]+);/g)) if (!(m[1] in vals)) vals[m[1]] = m[2].trim();
  return () => ({ getPropertyValue: (name) => vals[name] || '' });
}

module.exports = { KIT, ALARMS, preloadKit, preloadAlarms, tokenStyle };
