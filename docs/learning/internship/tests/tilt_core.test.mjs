// SPEC section 4 — W1 tilt and thrust vector. Pure functions, no DOM.

import test from 'node:test';
import assert from 'node:assert/strict';
import { thrustFromAccel, singleAxis } from '../sims/tilt_core.mjs';

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

test('SPEC 4 row 1 — thrustFromAccel(2, 3, 4, 0): T 22.02, theta 27.01, phi 53.13', () => {
  const r = thrustFromAccel(2, 3, 4, 0);
  near(r.T, 22.02, 0.01, 'T');
  near(r.thetaDeg, 27.01, 0.05, 'thetaDeg');
  near(r.phiDeg, 53.13, 0.05, 'phiDeg');
  // F = m * (ax, ay, az + g)
  near(r.Fx, 6, 0.01, 'Fx');
  near(r.Fy, 8, 0.01, 'Fy');
  near(r.Fz, 2 * 9.81, 0.01, 'Fz');
});

test('SPEC 4 row 2 — thrustFromAccel(2, 3, 4, 2): T 25.65, theta 22.94', () => {
  const r = thrustFromAccel(2, 3, 4, 2);
  near(r.T, 25.65, 0.01, 'T');
  near(r.thetaDeg, 22.94, 0.05, 'thetaDeg');
});

test('SPEC 4 row 3 — thrustFromAccel(2, 0, 0, 0): T 19.62, theta 0', () => {
  const r = thrustFromAccel(2, 0, 0, 0);
  near(r.T, 19.62, 0.01, 'T');
  near(r.thetaDeg, 0, 0.05, 'thetaDeg');
});

test('SPEC 4 row 4 — singleAxis(1.5, 20): T 15.66, a 3.57', () => {
  const r = singleAxis(1.5, 20);
  near(r.T, 15.66, 0.01, 'T');
  near(r.a, 3.57, 0.01, 'a');
  // Tvert = T cos(theta) = mg, Thoriz = T sin(theta) = m a
  near(r.Tvert, 1.5 * 9.81, 0.01, 'Tvert');
  near(r.Thoriz, 1.5 * r.a, 0.01, 'Thoriz');
});
