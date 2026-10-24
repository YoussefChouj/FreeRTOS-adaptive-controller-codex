// SPEC section 5 — W2 payload step on the vertical loop.

import test from 'node:test';
import assert from 'node:assert/strict';
import { simulate, summarize } from '../sims/zloop_core.mjs';

function near(actual, expected, tol, what) {
  assert.ok(
    Number.isFinite(actual),
    `${what}: expected a finite number, got ${actual}`,
  );
  assert.ok(
    Math.abs(actual - expected) <= tol,
    `${what}: expected ${expected} +/- ${tol}, got ${actual}`,
  );
}

// Linear interpolation of z at an arbitrary time.
function zAt(res, tq) {
  const { t, z } = res;
  for (let i = 1; i < t.length; i++) {
    if (t[i] >= tq) {
      const f = (tq - t[i - 1]) / (t[i] - t[i - 1]);
      return z[i - 1] + f * (z[i] - z[i - 1]);
    }
  }
  return z[z.length - 1];
}

test('SPEC 5 row 1 — open loop: z at t = 3.0 s is -0.987 m', () => {
  const res = simulate({ openLoop: true });
  near(zAt(res, 3.0), -0.987, 0.01, 'z(3.0)');
});

test('SPEC 5 row 2 — useI=false: final z is -0.8175 m (P-only sag)', () => {
  const res = simulate({ useI: false });
  const s = summarize(res);
  near(s.finalZ, -0.8175, 0.002, 'finalZ');
});

test('SPEC 5 row 3 — defaults (Ki 2): lowest z is -0.476 m', () => {
  const res = simulate();
  const s = summarize(res);
  near(s.lowestZ, -0.476, 0.01, 'lowestZ');
});

test('SPEC 5 row 4 — defaults (Ki 2): last time |z| > 0.02 m is 9.03 s', () => {
  const res = simulate();
  const s = summarize(res, 0.02);
  near(s.settleTime, 9.03, 0.1, 'settleTime');
});

test('SPEC 5 row 5 — Ki 1: final z at 12 s is -0.042 m (not settled)', () => {
  const res = simulate({ Ki: 1 });
  const s = summarize(res);
  near(s.finalZ, -0.042, 0.005, 'finalZ');
});

test('SPEC 5 row 6 — Ki 4: lowest z -0.363 m, settle time 6.96 s', () => {
  const res = simulate({ Ki: 4 });
  const s = summarize(res, 0.02);
  near(s.lowestZ, -0.363, 0.01, 'lowestZ');
  near(s.settleTime, 6.96, 0.1, 'settleTime');
});

test('SPEC 5 row 7 — defaults: final T is 24.5 N', () => {
  const res = simulate();
  const s = summarize(res);
  near(s.finalT, 24.5, 0.1, 'finalT');
});
