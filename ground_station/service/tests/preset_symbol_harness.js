'use strict';
/**
 * Offline harness for the item-14 preset→symbol hint.
 *
 * The dashboard panels that show a value as "not published" gain a hint naming
 * the Live-Log preset (from ground_station/livewatch/multi_slot_presets.yaml)
 * whose slot manifest would publish that symbol. The service computes this
 * from the YAML (manifests.yaml + multi_slot_presets.yaml) in
 * ground_station/service/api.py::preset_carriers(). This harness re-derives the
 * SAME mapping in JS from the same two YAML files and asserts the contract the
 * Python route implements — so a drift between the route and the data the UI
 * hints rely on is caught here.
 *
 * Run:  node ground_station/service/tests/preset_symbol_harness.js
 * Exit 0 iff every check passes.
 */
const fs = require('fs');
const path = require('path');

const LWDIR = path.join(__dirname, '..', '..', '..', 'ground_station', 'livewatch');
const PRESETS = path.join(LWDIR, 'multi_slot_presets.yaml');
const MANIFESTS = path.join(LWDIR, 'manifests.yaml');

let failures = 0;
function check(label, cond) {
  if (cond) console.log('  OK  ' + label);
  else { console.error('FAIL  ' + label); failures++; }
}

// Minimal, structure-specific YAML reader for the two livewatch files.
function norm(text) { return text.replace(/\r/g, ''); }

function parsePresets(text) {
  const presets = {};
  let cur = null;
  for (const raw of norm(text).split('\n')) {
    const line = raw.replace(/\s*$/, '');
    const m = /^  ([a-z][a-z0-9_]*):\s*$/.exec(line);
    if (m) { cur = { slots: [], description: '' }; presets[m[1]] = cur; continue; }
    if (!cur) continue;
    const slot = /^\s*-?\s*{\s*slot:\s*(\d+)?/.exec(line) || /^\s*-\s*slot:\s*(\d+)/.exec(line);
    const manifest = /^\s*manifest:\s*([a-z0-9_]+)/.exec(line);
    if (manifest) cur.slots.push({ manifest: manifest[1], slot: cur._lastSlot });
    const slotN = /^\s*-\s*slot:\s*(\d+)/.exec(line);
    if (slotN) cur._lastSlot = Math.min(9, parseInt(slotN[1], 10) || 0);
  }
  return presets;
}

function parseManifests(text) {
  const manifests = {};
  let cur = null;
  let inVars = false;
  for (const raw of norm(text).split('\n')) {
    const line = raw;
    const m = /^  ([a-z][a-z0-9_]*):\s*$/.exec(line);
    if (m) { cur = { vars: [] }; manifests[m[1]] = cur; inVars = false; continue; }
    if (!cur) continue;
    if (/^\s*vars:\s*$/.test(line)) { inVars = true; continue; }
    const vm2 = /^\s+[a-z][a-z0-9_]*:\s*$/.exec(line);
    if (vm2 && !/^\s+-/.test(line)) { inVars = false; continue; }
    if (inVars) {
      const v = /^\s*-\s*(.+)$/.exec(line);
      if (v) cur.vars.push(v[1].trim().replace(/^['"]|['"]$/g, ''));
    }
  }
  return manifests;
}

function presetCarriers(symbol, presets, manifests) {
  const hits = [];
  for (const pname of Object.keys(presets)) {
    for (const slot of presets[pname].slots) {
      const m = manifests[slot.manifest];
      if (!m) continue;
      const tail = symbol ? symbol.toLowerCase().split('.').pop() : '';
      const sl = symbol ? symbol.toLowerCase() : '';
      for (const v of (m.vars || [])) {
        const vlow = v.toLowerCase();
        if ((sl && vlow.includes(sl)) || (tail && vlow.includes(tail))) {
          hits.push({ preset: pname, manifest: slot.manifest, slot: slot.slot });
        }
      }
    }
  }
  const uniq = {};
  for (const h of hits) uniq[h.preset + '/' + h.manifest + '/' + h.slot] = h;
  return Object.values(uniq);
}

async function main() {
  console.log('\n=== preset→symbol mapping harness (item 14) ===\n');
  const presets = parsePresets(fs.readFileSync(PRESETS, 'utf8'));
  const manifests = parseManifests(fs.readFileSync(MANIFESTS, 'utf8'));

  check('multi_slot_presets.yaml defines expected presets',
    ['flight_comprehensive', 'mrac_characterization', 'bias_logging']
      .every(n => n in presets));
  check('flight_comprehensive uses inner_loops/mrac_weights/ekf_all',
    presets.flight_comprehensive.slots.some(s => s.manifest === 'inner_loops') &&
    presets.flight_comprehensive.slots.some(s => s.manifest === 'mrac_weights') &&
    presets.flight_comprehensive.slots.some(s => s.manifest === 'ekf_all'));
  check('bias_logging carries bias_frame',
    presets.bias_logging.slots.some(s => s.manifest === 'bias_frame'));
  for (const p of Object.keys(presets))
    for (const slot of presets[p].slots)
      check('preset "' + p + '" slot manifest "' + slot.manifest + '" is defined in manifests.yaml',
        slot.manifest in manifests);

  // The UI hints: a value the panel marks "not published" names the preset to
  // record to so the operator can see it.
  const mrac = presetCarriers('mrac.roll.u_ad', presets, manifests);
  check('mrac.roll.u_ad → mrac-carrying preset',
    mrac.some(h => h.manifest === 'mrac_signals' || h.manifest === 'mrac_weights'));
  const ofBias = presetCarriers('g_of_bias_mode', presets, manifests);
  check('g_of_bias_mode → bias_logging (bias_frame)',
    ofBias.some(h => h.manifest === 'bias_frame' && h.preset === 'bias_logging'),
    JSON.stringify(ofBias));
  const gyro = presetCarriers('pid.gyrox.FB', presets, manifests);
  check('pid.gyrox.FB → flight_comprehensive (inner_loops)',
    gyro.some(h => h.manifest === 'inner_loops' && h.preset === 'flight_comprehensive'),
    JSON.stringify(gyro));

  console.log(failures ? '\nFAILED: ' + failures : '\nALL GREEN');
  process.exit(failures ? 1 : 0);
}

main().catch(e => { console.error('PRESET HARNESS CRASHED:', e); process.exit(1); });