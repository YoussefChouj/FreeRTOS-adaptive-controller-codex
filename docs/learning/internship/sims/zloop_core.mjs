// W2 — payload step on the vertical loop. Pure simulation, no DOM.
// SPEC section 5. The algorithm below is written exactly as the spec states it.

export const DEFAULTS = Object.freeze({
  g: 9.81,
  m_nom: 2.0,
  m_add: 0.5,
  t_add: 2.0,
  dt: 0.002,
  t_end: 12.0,
  Kp: 1.0,
  Kv: 3.0,
  Ki: 2.0,
  useI: true,
  hoverFF: true,
  openLoop: false,
  tau: 0.05,
  Tmax: 2 * 2.0 * 9.81,
  z_sp: 0.0,
});

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

/**
 * Run the vertical loop. Returns parallel arrays {t, z, vz, T, Tcmd}.
 */
export function simulate(params = {}) {
  const p = { ...DEFAULTS, ...params };
  const g = p.g;
  const m_nom = p.m_nom;
  const m_add = p.m_add;
  const t_add = p.t_add;
  const dt = p.dt;
  const t_end = p.t_end;
  const Kp = p.Kp;
  const Kv = p.Kv;
  const Ki = p.Ki;
  const useI = p.useI;
  const hoverFF = p.hoverFF;
  const openLoop = p.openLoop;
  const tau = p.tau;
  const z_sp = p.z_sp;
  const Tmax = p.Tmax === undefined || p.Tmax === null ? 2 * m_nom * g : p.Tmax;

  const res = { t: [], z: [], vz: [], T: [], Tcmd: [] };

  let z = 0;
  let vz = 0;
  let T = m_nom * g;
  let I = 0;
  let t = 0;

  while (t < t_end) {
    const m = t < t_add ? m_nom : m_nom + m_add;

    const e = z_sp - z;
    const vsp = Kp * e;
    const ev = vsp - vz;
    if (useI) I += Ki * ev * dt;
    const a = Kv * ev + (useI ? I : 0);

    let Tcmd;
    if (openLoop) Tcmd = m_nom * g;
    else if (hoverFF) Tcmd = m_nom * (g + a);
    else Tcmd = m_nom * a;
    Tcmd = clamp(Tcmd, 0, Tmax);

    T += ((Tcmd - T) * dt) / tau; // motor lag

    vz += (T / m - g) * dt;
    z += vz * dt;
    t += dt;

    res.t.push(t);
    res.z.push(z);
    res.vz.push(vz);
    res.T.push(T);
    res.Tcmd.push(Tcmd);
  }

  res.Tmax = Tmax;
  return res;
}

/**
 * Derived numbers the view and the tests both need.
 *   lowestZ    — most negative height reached
 *   finalZ     — height at the end of the run
 *   settleTime — last time |z| was above tol, so |z| stays <= tol after it
 *   finalT     — thrust at the end of the run
 *   saturated  — true when the commanded thrust was clipped at Tmax
 */
export function summarize(res, tol = 0.02) {
  let lowestZ = Infinity;
  let settleTime = 0;
  let saturated = false;
  const lim = res.Tmax === undefined ? Infinity : res.Tmax;
  for (let i = 0; i < res.z.length; i++) {
    if (res.z[i] < lowestZ) lowestZ = res.z[i];
    if (Math.abs(res.z[i]) > tol) settleTime = res.t[i];
    if (res.Tcmd[i] >= lim - 1e-9) saturated = true;
  }
  return {
    lowestZ,
    finalZ: res.z[res.z.length - 1],
    settleTime,
    finalT: res.T[res.T.length - 1],
    saturated,
  };
}
