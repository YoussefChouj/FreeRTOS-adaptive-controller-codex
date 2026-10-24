// W1 — tilt and thrust vector. Pure functions, no DOM.
// SPEC section 4.

export const G_DEFAULT = 9.81;

/**
 * Thrust vector that produces the acceleration (ax, ay, az).
 *   F = m * (ax, ay, az + g)
 *   T = |F|
 *   theta = atan2(sqrt(ax^2 + ay^2), az + g)
 *   phi   = atan2(ay, ax)
 */
export function thrustFromAccel(m, ax, ay, az, g = G_DEFAULT) {
  const Fx = m * ax;
  const Fy = m * ay;
  const Fz = m * (az + g);
  const T = Math.sqrt(Fx * Fx + Fy * Fy + Fz * Fz);
  const thetaDeg = (Math.atan2(Math.sqrt(ax * ax + ay * ay), az + g) * 180) / Math.PI;
  const phiDeg = (Math.atan2(ay, ax) * 180) / Math.PI;
  return { Fx, Fy, Fz, T, thetaDeg, phiDeg };
}

/**
 * One-axis tilt.
 *   T = m g / cos(theta),  Tvert = T cos(theta),  Thoriz = T sin(theta),  a = g tan(theta)
 */
export function singleAxis(m, thetaDeg, g = G_DEFAULT) {
  const th = (thetaDeg * Math.PI) / 180;
  const T = (m * g) / Math.cos(th);
  const Tvert = T * Math.cos(th);
  const Thoriz = T * Math.sin(th);
  const a = g * Math.tan(th);
  return { T, Tvert, Thoriz, a };
}
