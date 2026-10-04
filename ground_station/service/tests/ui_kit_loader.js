'use strict';
/**
 * Loads docs/dashboard-platform/shell/ui/ui-kit.js into a vm context, the way index.html loads it before any
 * plugin, so plugin harnesses see window.GSUI. The context must define `window` (and the timers / fetch the
 * harness wants the kit to use).
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const KIT = path.join(__dirname, '..', '..', '..', 'docs', 'dashboard-platform', 'shell', 'ui', 'ui-kit.js');

function preloadKit(ctx) {
  vm.runInContext(fs.readFileSync(KIT, 'utf8'), ctx, { filename: KIT });
  return ctx;
}

module.exports = { KIT, preloadKit };
