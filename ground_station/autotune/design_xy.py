"""SysID -> model-based xy outer-loop controller gains (LQR, ADRC, model, MPC).

Firmware VAR 75-83 & Predictor Delay Semantics (`TASK/AutoflyTask.c:471-594`, `API/xy_ctrl.h`, `API/xy_ctrl.c`)
================================================================================================================
All xy outer-loop controllers run at `XYC_DT = 0.01 s` (100 Hz) in `StabilizerTask.c:2465-2504`, replacing the
`locxPID` + `locxsPID` cascade output: position `pos, des` in `cm`, velocity `vel, vff` in `cm/s`, and
horizontal acceleration command `u, aff` in `cm/s^2` (`Ctrler.locxsPID.U`), clamped to `+-XYC_AMAX = +-262.858158
cm/s^2` (15 deg tilt cap at `g = 981 cm/s^2`). All VAR 47 and VAR 75-83 writes use GS `CMD_VAR_WRITE` (`0x2D`)
and are strictly ground-only (refused with `VAR_WRITE_REJECT_BUSY` when `FlightFSM_GetState() == FLIGHT_STATE_ARMED`
or `DroneStatus.ARM_Status == Armed`, and refused in `XyCtrl_WriteParam` when `armed != 0`).

| VAR | C Symbol           | Type   | Range            | `xy_ctrl_gains.h` | `candidate.h` | Units               | Firmware Meaning & Discretisation |
|-----|--------------------|--------|------------------|-------------------|---------------|---------------------|-----------------------------------|
|  47 | `g_xyc_delay_ticks`| `VW_U8`| `[1, 48]`        | `33` (`12` task)  | `18`          | `ticks` (10 ms)     | Smith-predictor pipe length `d`; calls `XyCtrl_SetDelay(d)` to rebuild `s_phi = A^d` and `s_gam[:,j] = A^(d-1-j) B`. |
|  74 | `g_xyc_pred_lead`  | `VW_U8`| `[0, 1]`         | `0`               | `0`           | `bool`              | `0`: compare predicted state `xp` against current ref `(des, vff)`; `1`: lead ref by `d*dt` + `aff`. |
|  75 | `g_xyc_lqr_k0`     | `VW_F32`| `[0.0, 200.0]`  | `29.7682778`      | `6.57910454`  | `(cm/s^2)/cm`       | LQR position error gain `K[0]` multiplying `x[0] = xp[0] - des_ref` (`cm`). |
|  76 | `g_xyc_lqr_k1`     | `VW_F32`| `[0.0, 100.0]`  | `10.0523956`      | `5.13574747`  | `(cm/s^2)/(cm/s)`   | LQR velocity error gain `K[1]` multiplying `x[1] = xp[1] - vff_ref` (`cm/s`). |
|  77 | `g_xyc_lqr_k2`     | `VW_F32`| `[-10.0, 50.0]` | `0.35933145`      | `0.477461929` | `(cm/s^2)/(cm/s^2)` | LQR lag-accel error gain `K[2]` multiplying `x[2] = xp[2] - (aff_ref + drag*vff_ref)/k` (`cm/s^2`). |
|  78 | `g_xyc_lqr_k3`     | `VW_F32`| `[0.0, 50.0]`   | `2.2333094`       | `0.539490884` | `(cm/s^2)/(cm*s)`   | LQR integral gain `K[3]` multiplying `x[3] = z = sum(dt*(pos - des))` (`cm*s`, gated for `|pos-des| < 10 cm`, clamped `+-300`). |
|  79 | `g_xyc_adrc_wc`    | `VW_F32`| `[0.1, 20.0]`   | `1.34060242`      | `1.25`        | `rad/s`             | ADRC controller bandwidth `wc`: `u0 = wc^2*(des - z1) + 2*wc*(vff - z2) + aff`. |
|  80 | `g_xyc_adrc_b0`    | `VW_F32`| `[0.05, 10.0]`  | `0.572712305`     | `2.304`       | `(cm/s^2)/(cm/s^2)` | ADRC input gain `b0`: scales `u = (u0 - z3) / b0` and ESO velocity update `z2 += dt*(z3 - b2*e + b0*pipe[head])`. |
|  81 | `g_xyc_model_k`    | `VW_F32`| `[0.05, 5.0]`   | `0.674429432`     | `0.72`        | `(cm/s^2)/(cm/s^2)` | Plant steady-state accel gain `k`; writing calls `XyCtrl_SetDelay(s_d)` to rebuild `s_phi, s_gam` and scales `uff = (aff + drag*vff)/k`. |
|  82 | `g_xyc_model_al`   | `VW_F32`| `[0.01, 0.999]` | `0.862128296`     | `0.94119394`  | `-`                 | Plant discrete lag pole `al = exp(-dt/tau_s)` at `dt = 0.01 s`; writing calls `XyCtrl_SetDelay(s_d)` to rebuild `s_phi, s_gam`. |
|  83 | `g_xyc_model_drag` | `VW_F32`| `[0.0, 5.0]`    | `0.42558222`      | `0.38`        | `1/s`               | Plant linear velocity drag `drag`; writing calls `XyCtrl_SetDelay(s_d)` to rebuild `s_phi, s_gam` and updates `uff`. |

Discretisation, Sign Convention, and Delay Entry
------------------------------------------------
1. **Plant Discretisation (`XyCtrl_SetDelay` in `API/xy_ctrl.c:54-87`, `model_ad` in `xy_ctrl.py:43-50`)**:
   One 10 ms tick updates lag state `a` first from the delayed command `u_{k-d}`, then velocity `v` using the new `a`,
   then position `p` using the new `v`:
       a_{k+1} = al * a_k + (1 - al) * u_{k-d}
       v_{k+1} = (1 - drag * dt) * v_k + dt * k * a_{k+1}
       p_{k+1} = p_k + dt * v_{k+1}
   yielding 3x3 matrix `A` and 3x1 vector `B`:
       A = [[1, dt*(1 - drag*dt), dt^2*k*al],
            [0, 1 - drag*dt,      dt*k*al  ],
            [0, 0,                al       ]]
       B = [dt^2*k*(1 - al), dt*k*(1 - al), 1 - al]^T
   Augmented 4-state error model `aug_ad` appends integral state `z_{k+1} = z_k + dt * e_pos`:
       A_aug = [[A, 0], [[dt, 0, 0], 1]],  B_aug = [B^T, 0]^T.
2. **Sign Convention**:
   Error state `x = [pos_pred - des_ref, vel_pred - vff_ref, a_pred - a_ff, z]^T` is `measurement - reference`.
   Consequently, LQR control law is `u = clamp(uff - K @ x, -AMAX, AMAX)` with positive `K = [k0, k1, k2, k3]`.
   In ADRC, `u0 = wc^2 * (des - z1) + 2 * wc * (vff - z2) + aff` and `u = clamp((u0 - z3) / b0, -AMAX, AMAX)`.
3. **How Delay `d` Enters**:
   - **LQR & MPC**: Smith predictor integrates the last `d` applied commands in `s->pipe[0..d-1]` through `(s_phi, s_gam)`
     to predict `[pos, vel, a]` at `t + d*dt` (when the command computed now will begin acting). Because the predictor
     removes the `d`-tick transport delay from the loop state, the DARE gain `K` is solved on `(A_aug, B_aug)`.
   - **ADRC**: The 3rd-order ESO receives the delayed command `s->pipe[s->head]` (`u` delayed by `d` ticks) in the `z2`
     observer update (`s->z2 += dt * (s->z3 - B2*e + s_adrc_b0 * s->pipe[s->head])`), time-aligning the control input
     with the delayed position measurement. Observer gains `XYC_ADRC_B1..B3` (`3*wo, 3*wo^2, wo^3`) are compile-time
     constants in `API/xy_ctrl_gains.h` (`wo = 3.2 rad/s` in `candidate.h`).
   - **MPC Compile-Time vs Runtime**: Runtime writes to `VAR 47, 81, 82, 83` update the Smith predictor (`s_phi, s_gam`)
     and feedforward `uff` used by MPC (`API/xy_ctrl.c:339-341`), but the condensed QP matrices `XYC_MPC_H[10][10]`,
     `XYC_MPC_F[10][4]`, `XYC_MPC_STEP`, and `XYC_MPC_RD` are compile-time RO-data in `API/xy_ctrl_gains.h`
     (`API/xy_ctrl.h:55-58`). Changing MPC QP matrices requires regenerating `API/xy_ctrl_gains.h` and reflashing.

Fair Comparison & Q/R Choice (PROPOSED)
---------------------------------------
To compare PID, LQR, ADRC, and MPC at equal closed-loop bandwidth on the identified model:
- We evaluate the approved vel/pos PID cascade on the identified model to determine its position loop crossover
  bandwidth `wc_target` (`rad/s`; default reference `1.25 rad/s` from `xy_ctrl_gains_candidate.h`).
- **ADRC**: `wc = wc_target` (or stepped down if needed to satisfy PM/overshoot/saturation checks on the delayed plant),
  and `b0 = k` (the identified plant input gain).
- **LQR Q/R Choice (PROPOSED)**: Starting from the `xy_ctrl_gains_candidate.h` baseline weights at `wc_ref = 1.25 rad/s`
  (`q_pos = 38.0, q_vel = 7.5, q_acc = 0.0, q_int = 0.3, r = 1.0`), state weights scale with bandwidth ratio
  `rho = wc_target / 1.25` via `q_pos = 38.0 * rho^4`, `q_vel = 7.5 * rho^2`, `q_acc = 0.0`, `q_int = 0.3 * rho^6`,
  `r = 1.0` (so `rho = 1.0` recovers `candidate.h` Q/R exactly).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.linalg import solve_discrete_are

from ground_station.analysis.xy_ctrl import AMAX, DT, INT_BAND, INT_MAX, MPC_ITERS, MPC_N, mpc_matrices
from ground_station.autotune.design import PidRow, Plant, margins, pid_frf
from ground_station.service.campaign_fly import CANDIDATE_XY_GAINS

# All acceptance check thresholds below are PROPOSED (n28 W3 spec), not measured on the airframe.
PM_MIN_DEG_PROPOSED = 30.0          # PROPOSED minimum phase margin [deg]
OVERSHOOT_MAX_PCT_PROPOSED = 20.0   # PROPOSED maximum step overshoot [%]
STEP_M_PROPOSED = 0.30              # PROPOSED step amplitude [m] (30.0 cm) for saturation & overshoot check
STEP_CM_PROPOSED = STEP_M_PROPOSED * 100.0
SIM_DURATION_S_PROPOSED = 12.0      # PROPOSED closed-loop step simulation horizon [s]

# Candidate baseline from API/xy_ctrl_gains_candidate.h (PROPOSED)
CANDIDATE_MODEL = {
    "k": 0.72,
    "tau_s": 0.165,
    "al": 0.94119394,
    "drag": 0.38,
    "d": 18,
}
CANDIDATE_LQR_Q = (38.0, 7.5, 0.0, 0.3)  # PROPOSED baseline Q at wc_ref = 1.25 rad/s
CANDIDATE_LQR_R = 1.0
CANDIDATE_WC_REF_RPS = 1.25               # PROPOSED reference bandwidth [rad/s]
CANDIDATE_ADRC_WO_RPS = 3.2               # API/xy_ctrl_gains_candidate.h XYC_ADRC_B1..B3 observer bw [rad/s]
CANDIDATE_MPC_Q = (48.0, 9.0, 0.0, 0.45)  # API/xy_ctrl_gains_candidate.h MPC block weights
CANDIDATE_MPC_RD = 1.4


@dataclass(frozen=True)
class ControllerCheck:
    """Simulated closed-loop and frequency-domain check results on the delayed identified plant (PROPOSED gates)."""

    controller: str
    label: str
    stable: bool
    spectral_radius: float
    pm_deg: float
    gm_db: float
    wc_rps: float
    overshoot_pct: float
    peak_u_cms2: float
    no_saturation: bool
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float):
                d[k] = round(v, 4) if math.isfinite(v) else None
        return d


@dataclass
class XyDesignResult:
    """Output of `design_xy`: model parameters, LQR/ADRC/MPC gains, VAR 75-83 dict, and side-by-side checks."""

    model: dict[str, float | int]
    target_wc_rps: float
    lqr_q: tuple[float, float, float, float]
    lqr_r: float
    lqr_k: tuple[float, float, float, float]
    adrc_wc: float
    adrc_b0: float
    adrc_wo_compile_time: float
    model_k: float
    model_al: float
    model_drag: float
    delay_ticks: int
    mpc_runtime_params: dict[str, float | int]
    mpc_compile_time_only: dict[str, Any]
    var_gains: dict[str, float]
    candidate_var_gains: dict[str, float]
    checks_new: dict[str, ControllerCheck]
    checks_candidate: dict[str, ControllerCheck]
    table_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "proposed" if all(c.passed for c in self.checks_new.values()) else "warning",
            "proposed_note": "All check thresholds (PM >= 30 deg, overshoot <= 20%, no saturation on 0.3 m step) are PROPOSED.",
            "model": dict(self.model),
            "target_wc_rps": round(self.target_wc_rps, 4),
            "lqr": {
                "q": list(self.lqr_q),
                "r": self.lqr_r,
                "k": [round(v, 8) for v in self.lqr_k],
            },
            "adrc": {
                "wc": round(self.adrc_wc, 6),
                "b0": round(self.adrc_b0, 6),
                "wo_compile_time": self.adrc_wo_compile_time,
            },
            "mpc": {
                "runtime_params": dict(self.mpc_runtime_params),
                "compile_time_only": self.mpc_compile_time_only,
            },
            "var_gains": {k: round(float(v), 8) for k, v in self.var_gains.items()},
            "candidate_var_gains": dict(self.candidate_var_gains),
            "checks_new": {k: v.to_dict() for k, v in self.checks_new.items()},
            "checks_candidate": {k: v.to_dict() for k, v in self.checks_candidate.items()},
            "table_text": self.table_text,
        }


def _load_json_or_dict(item: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(item, (str, Path)):
        return json.loads(Path(item).read_text(encoding="utf-8"))
    if isinstance(item, Mapping):
        return dict(item)
    raise TypeError(f"Expected path or mapping, got {type(item)!r}")


def _as_list(items: str | Path | Mapping[str, Any] | Sequence[str | Path | Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if items is None:
        return []
    if isinstance(items, (str, Path, Mapping)):
        return [_load_json_or_dict(items)]
    return [_load_json_or_dict(x) for x in items]


def extract_xy_model(
    vel_models: str | Path | Mapping[str, Any] | Sequence[str | Path | Mapping[str, Any]],
    pos_models: str | Path | Mapping[str, Any] | Sequence[str | Path | Mapping[str, Any]] | None = None,
    delay_ticks: int | None = None,
) -> dict[str, float | int]:
    """Extract unified xy plant dict `{'k', 'tau_s', 'al', 'drag', 'd'}` from `model_vel_{x,y}.json` / `model_pos_{x,y}.json`."""
    v_list = _as_list(vel_models)
    p_list = _as_list(pos_models)
    if not v_list and not p_list:
        raise ValueError("At least one velocity or position model must be provided")

    ks, taus, drags, als, delays_s = [], [], [], [], []
    for d in v_list or p_list:
        fit = d.get("fit") if isinstance(d.get("fit"), Mapping) else d
        if "k" in fit and fit["k"] is not None:
            ks.append(float(fit["k"]))
        if "tau_s" in fit and fit["tau_s"] is not None:
            taus.append(float(fit["tau_s"]))
        elif "tau" in fit and fit["tau"] is not None:
            taus.append(float(fit["tau"]))
        if "al" in fit and fit["al"] is not None:
            als.append(float(fit["al"]))
        if "drag" in fit and fit["drag"] is not None:
            drags.append(float(fit["drag"]))
        if "delay_s" in fit and fit["delay_s"] is not None:
            delays_s.append(float(fit["delay_s"]))

    # If vel fits did not estimate drag (or drag==0) and pos fits have drag > 0, fallback to pos drag
    if (not drags or all(dr == 0.0 for dr in drags)) and p_list:
        p_drags = []
        for d in p_list:
            fit = d.get("fit") if isinstance(d.get("fit"), Mapping) else d
            if "drag" in fit and fit["drag"] is not None and float(fit["drag"]) > 0.0:
                p_drags.append(float(fit["drag"]))
        if p_drags:
            drags = p_drags

    k_val = float(np.mean(ks)) if ks else CANDIDATE_MODEL["k"]
    drag_val = float(np.mean(drags)) if drags else 0.0
    if als:
        al_val = float(np.mean(als))
        tau_val = -DT / math.log(al_val) if 0.0 < al_val < 1.0 else 0.0
    elif taus:
        tau_val = float(np.mean(taus))
        al_val = math.exp(-DT / tau_val) if tau_val > 0.0 else 0.0
    else:
        tau_val = 0.0
        al_val = 0.0

    if delay_ticks is not None:
        d_ticks = int(delay_ticks)
    elif v_list and "d" in (v_list[0].get("fit") or v_list[0]):
        fit0 = v_list[0].get("fit") if isinstance(v_list[0].get("fit"), Mapping) else v_list[0]
        d_ticks = int(fit0["d"])
    elif delays_s:
        d_ticks = max(1, int(round(float(np.mean(delays_s)) / DT)))
    else:
        d_ticks = int(CANDIDATE_MODEL["d"])

    return {
        "k": k_val,
        "tau_s": tau_val,
        "tau": tau_val,
        "al": al_val,
        "drag": drag_val,
        "d": d_ticks,
    }


def model_ad_exact(m: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """One 10 ms tick of `[pos, vel, a]` (`API/xy_ctrl.c:57-61`), supporting `al = 0` (pure double integrator)."""
    k = float(m["k"])
    dr = float(m.get("drag", 0.0))
    if "al" in m and m["al"] is not None:
        al = float(m["al"])
    else:
        tau = float(m.get("tau_s", m.get("tau", 0.0)))
        al = math.exp(-DT / tau) if tau > 0.0 else 0.0
    a = np.array([
        [1.0, DT * (1.0 - dr * DT), DT * DT * k * al],
        [0.0, 1.0 - dr * DT, DT * k * al],
        [0.0, 0.0, al],
    ], dtype=float)
    b = np.array([DT * DT * k * (1.0 - al), DT * k * (1.0 - al), 1.0 - al], dtype=float)
    return a, b


def aug_ad_exact(m: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """4-state discrete LQR/MPC design model `[e_pos, e_vel, a - a_ff, z_int]` at `DT = 0.01 s`."""
    a, b = model_ad_exact(m)
    aa = np.zeros((4, 4), dtype=float)
    aa[:3, :3] = a
    aa[3, 0] = DT
    aa[3, 3] = 1.0
    bb = np.append(b, 0.0)
    return aa, bb


def solve_lqr_gain(
    m: Mapping[str, Any],
    q: Sequence[float] = CANDIDATE_LQR_Q,
    r: float = CANDIDATE_LQR_R,
) -> np.ndarray:
    """Discrete LQR gain `k[0..3]` via `scipy.linalg.solve_discrete_are` on the 4-state augmented model."""
    a, b = aug_ad_exact(m)
    b_col = b.reshape(4, 1)
    q_mat = np.diag(np.asarray(q, dtype=float))
    r_mat = np.array([[float(r)]], dtype=float)
    p = solve_discrete_are(a, b_col, q_mat, r_mat)
    k = np.linalg.solve(r_mat + b_col.T @ p @ b_col, b_col.T @ p @ a).ravel()
    return k


def estimate_pid_bandwidth_rps(
    m: Mapping[str, Any],
    vel_models: Sequence[Mapping[str, Any]] | None = None,
    pos_models: Sequence[Mapping[str, Any]] | None = None,
) -> float:
    """Compute the closed-loop position bandwidth (open-loop crossover `wc_rps`) of the approved vel+pos PID cascade."""
    # 1. Check if pos_models already carry proposed/current crossover frequency from autotune
    if pos_models:
        wcs = []
        for pm in pos_models:
            m_dict = pm.get("margins") or {}
            for key in ("proposed", "current"):
                wc = (m_dict.get(key) or {}).get("wc_rps")
                if wc is not None and math.isfinite(float(wc)) and float(wc) > 0.0:
                    wcs.append(float(wc))
                    break
        if wcs:
            return float(np.clip(np.mean(wcs), 0.4, 3.0))

    # 2. Otherwise evaluate cascade L_pos(jw) = C_pos(z) * T_vel(jw) / (jw) on the identified plant
    vel_kp, vel_ki, vel_kd = 3.2242, 0.0157, 4.7283
    pos_kp, pos_ki, pos_kd = 0.5, 0.001, 6.0
    if vel_models:
        for vm in vel_models:
            g = vm.get("proposed") or vm.get("current")
            if isinstance(g, Mapping) and "Kp" in g:
                vel_kp, vel_ki, vel_kd = float(g["Kp"]), float(g.get("Ki", vel_ki)), float(g.get("Kd", vel_kd))
                break
    if pos_models:
        for pm in pos_models:
            g = pm.get("proposed") or pm.get("current")
            if isinstance(g, Mapping) and "Kp" in g:
                pos_kp, pos_ki, pos_kd = float(g["Kp"]), float(g.get("Ki", pos_ki)), float(g.get("Kd", pos_kd))
                break

    w = np.geomspace(0.05, 25.0, 600)
    plant = Plant(
        k=float(m["k"]),
        tau_s=float(m.get("tau_s", m.get("tau", 0.0))),
        delay_s=float(m.get("d", 18)) * DT,
        drag=float(m.get("drag", 0.0)),
    )
    c_vel = pid_frf(PidRow(vel_kp, vel_ki, vel_kd), w, dt=DT)
    p_vel = plant.frf(w)
    l_vel = c_vel * p_vel
    t_vel = l_vel / (1.0 + l_vel)
    c_pos = pid_frf(PidRow(pos_kp, pos_ki, pos_kd), w, dt=DT)
    l_pos = c_pos * t_vel / (1j * w)
    mag = np.abs(l_pos)
    phase_deg = np.degrees(np.unwrap(np.angle(l_pos)))
    m_pos = margins(mag, phase_deg, w=w)
    if math.isfinite(m_pos.wc_rps) and m_pos.wc_rps > 0.0:
        return float(np.clip(m_pos.wc_rps, 0.4, 3.0))
    return CANDIDATE_WC_REF_RPS


def q_for_bandwidth(target_wc_rps: float) -> tuple[float, float, float, float]:
    """Scale baseline candidate Q weights `(38, 7.5, 0, 0.3)` from `wc_ref = 1.25 rad/s` to `target_wc_rps` (PROPOSED)."""
    rho = float(np.clip(target_wc_rps / CANDIDATE_WC_REF_RPS, 0.4, 2.0))
    return (
        round(CANDIDATE_LQR_Q[0] * (rho ** 4), 6),
        round(CANDIDATE_LQR_Q[1] * (rho ** 2), 6),
        0.0,
        round(CANDIDATE_LQR_Q[3] * (rho ** 6), 6),
    )


def _lqr_closed_loop_spectral_radius(
    m_true: Mapping[str, Any],
    m_pred: Mapping[str, Any],
    k_gain: Sequence[float],
) -> float:
    """Exact spectral radius of the delayed closed-loop system under Smith-predictor LQR."""
    d_true = max(1, int(m_true.get("d", 1)))
    d_pred = max(1, int(m_pred.get("d", 1)))
    a_t, b_t = model_ad_exact(m_true)
    a_p, b_p = model_ad_exact(m_pred)
    al_p = float(m_pred.get("al", 0.0))

    # Build Smith predictor matrices phi_p (3x3) and gam_p (3 x d_pred)
    phi_p = np.eye(3)
    gam_p = np.zeros((3, d_pred))
    for j in range(d_pred):
        gam_p = a_p @ gam_p
        gam_p[:, j] += b_p
        phi_p = a_p @ phi_p

    # State vector: [p, v, a, z, a_est, u_hist[0..d_max-1]] where u_hist[0] is newest (u_{k-1}), u_hist[d-1] is oldest (u_{k-d})
    d_max = max(d_true, d_pred)
    n_dim = 5 + d_max
    f_mat = np.zeros((n_dim, n_dim), dtype=float)

    # xp = phi_p[:, :2] @ [p, v] + phi_p[:, 2] * a_est + sum_{j=0}^{d_pred-1} gam_p[:, j] * u_{k - d_pred + j}
    # In our u_hist indexing: pipe[j] (j=0 oldest u_{k-d_pred}, j=d_pred-1 newest u_{k-1}) corresponds to u_hist[d_pred - 1 - j]
    k_arr = np.asarray(k_gain, dtype=float)
    k_xp = k_arr[:3]
    u_row = np.zeros(n_dim, dtype=float)
    u_row[:2] = -(k_xp @ phi_p[:, :2])
    u_row[3] = -k_arr[3]
    u_row[4] = -(k_xp @ phi_p[:, 2])
    for j in range(d_pred):
        hist_idx = 5 + (d_pred - 1 - j)
        u_row[hist_idx] -= float(k_xp @ gam_p[:, j])

    # Plant state [p, v, a] update uses u_{k - d_true} = u_hist[d_true - 1]
    f_mat[:3, :3] = a_t
    f_mat[:3, 5 + d_true - 1] += b_t
    # Integral z_{k+1} = z_k + DT * p_k
    f_mat[3, 0] = DT
    f_mat[3, 3] = 1.0
    # Predictor lag state a_est_{k+1} = al_p * a_est_k + (1 - al_p) * u_{k - d_pred}
    f_mat[4, 4] = al_p
    f_mat[4, 5 + d_pred - 1] += 1.0 - al_p
    # History shift: u_hist[0] <- u_k, u_hist[i] <- u_hist[i-1]
    f_mat[5, :] = u_row
    for i in range(1, d_max):
        f_mat[5 + i, 5 + i - 1] = 1.0

    eigs = np.linalg.eigvals(f_mat)
    return float(np.max(np.abs(eigs)))


def _adrc_closed_loop_spectral_radius(
    m_true: Mapping[str, Any],
    d_pred: int,
    wc: float,
    b0: float,
    wo: float = CANDIDATE_ADRC_WO_RPS,
) -> float:
    """Exact spectral radius of the delayed closed-loop system under linear ADRC + 3rd-order ESO."""
    d_true = max(1, int(m_true.get("d", 1)))
    d_eso = max(1, int(d_pred))
    a_t, b_t = model_ad_exact(m_true)
    b1, b2, b3 = 3.0 * wo, 3.0 * wo * wo, wo ** 3

    # State: [p, v, a, z1, z2, z3, u_hist[0..d_max-1]]
    d_max = max(d_true, d_eso)
    n_dim = 6 + d_max
    f_mat = np.zeros((n_dim, n_dim), dtype=float)

    # Plant update
    f_mat[:3, :3] = a_t
    f_mat[:3, 6 + d_true - 1] += b_t

    # ESO update at step k using e = z1_k - p_k and u_del = u_hist[d_eso - 1]:
    # z1+ = z1 + DT*(z2 - b1*(z1 - p))
    # z2+ = z2 + DT*(z3 - b2*(z1 - p) + b0*u_del)
    # z3+ = z3 + DT*(-b3*(z1 - p))
    z1_row = np.zeros(n_dim)
    z1_row[0] = DT * b1
    z1_row[3] = 1.0 - DT * b1
    z1_row[4] = DT

    z2_row = np.zeros(n_dim)
    z2_row[0] = DT * b2
    z2_row[3] = -DT * b2
    z2_row[4] = 1.0
    z2_row[5] = DT
    z2_row[6 + d_eso - 1] += DT * b0

    z3_row = np.zeros(n_dim)
    z3_row[0] = DT * b3
    z3_row[3] = -DT * b3
    z3_row[5] = 1.0

    f_mat[3, :] = z1_row
    f_mat[4, :] = z2_row
    f_mat[5, :] = z3_row

    # Control u_k = (-wc^2 * z1+ - 2*wc * z2+ - z3+) / b0
    u_row = (- (wc ** 2) * z1_row - 2.0 * wc * z2_row - z3_row) / b0
    f_mat[6, :] = u_row
    for i in range(1, d_max):
        f_mat[6 + i, 6 + i - 1] = 1.0

    eigs = np.linalg.eigvals(f_mat)
    return float(np.max(np.abs(eigs)))


def _lqr_loop_margins(
    m_true: Mapping[str, Any],
    m_pred: Mapping[str, Any],
    k_gain: Sequence[float],
) -> tuple[float, float, float]:
    """Compute `(pm_deg, gm_db, wc_rps)` for Smith-predictor LQR on `m_true`."""
    w = np.geomspace(0.05, 30.0, 700)
    z = np.exp(1j * w * DT)
    a_t, b_t = model_ad_exact(m_true)
    a_p, b_p = model_ad_exact(m_pred)
    k_arr = np.asarray(k_gain, dtype=float)
    d_true = int(m_true.get("d", 18))
    d_pred = int(m_pred.get("d", 18))

    # Resolve (zI - A)^-1 B across frequency grid
    eye3 = np.eye(3)
    g_true = np.zeros((3, len(w)), dtype=complex)
    g_pred = np.zeros((3, len(w)), dtype=complex)
    for idx, zi in enumerate(z):
        g_true[:, idx] = np.linalg.solve(zi * eye3 - a_t, b_t)
        g_pred[:, idx] = np.linalg.solve(zi * eye3 - a_p, b_p)

    # Smith predictor loop transfer function:
    # Target nominal loop is K_aug (zI - A_aug_pred)^-1 B_aug_pred, perturbed by model/delay mismatch
    z_int = DT / (z - 1.0)
    l_nom = (k_arr[0] + k_arr[3] * z_int) * g_pred[0] + k_arr[1] * g_pred[1] + k_arr[2] * g_pred[2]
    mismatch = (
        ((k_arr[0] + k_arr[3] * z_int) * g_true[0] + k_arr[1] * g_true[1]) * (z ** (-d_true))
        - ((k_arr[0] + k_arr[3] * z_int) * g_pred[0] + k_arr[1] * g_pred[1]) * (z ** (-d_pred))
    )
    l_eff = l_nom + mismatch
    mag = np.abs(l_eff)
    phase_deg = np.degrees(np.unwrap(np.angle(l_eff)))
    m_res = margins(mag, phase_deg, w=w)
    return m_res.pm_deg, m_res.gm_db, m_res.wc_rps


def _adrc_loop_margins(
    m_true: Mapping[str, Any],
    d_pred: int,
    wc: float,
    b0: float,
    wo: float = CANDIDATE_ADRC_WO_RPS,
) -> tuple[float, float, float]:
    """Compute `(pm_deg, gm_db, wc_rps)` for linear ADRC with delayed ESO input on `m_true`."""
    w = np.geomspace(0.05, 30.0, 700)
    s = 1j * w
    k_true = float(m_true["k"])
    tau_s = float(m_true.get("tau_s", m_true.get("tau", 0.0)))
    drag = float(m_true.get("drag", 0.0))
    d_mismatch_s = (int(m_true.get("d", d_pred)) - int(d_pred)) * DT

    # Plant transfer function P(s) from u to pos
    denom = s * (s + drag) * (tau_s * s + 1.0)
    p_s = k_true * np.exp(-s * max(0.0, d_mismatch_s)) / denom
    # Equivalent ADRC feedback transfer function around nominal double integrator with ESO bandwidth wo
    c_s = (wc ** 2 + 2.0 * wc * s) / b0 * (wo / (s + wo))
    l_s = c_s * p_s
    mag = np.abs(l_s)
    phase_deg = np.degrees(np.unwrap(np.angle(l_s)))
    m_res = margins(mag, phase_deg, w=w)
    return m_res.pm_deg, m_res.gm_db, m_res.wc_rps


def simulate_closed_loop_step(
    kind: str,
    m_true: Mapping[str, Any],
    m_ctrl: Mapping[str, Any],
    *,
    lqr_k: Sequence[float] | None = None,
    adrc_wc: float | None = None,
    adrc_b0: float | None = None,
    adrc_wo: float = CANDIDATE_ADRC_WO_RPS,
    mpc_q: Sequence[float] = CANDIDATE_MPC_Q,
    mpc_rd: float = CANDIDATE_MPC_RD,
    step_cm: float = STEP_CM_PROPOSED,
    duration_s: float = SIM_DURATION_S_PROPOSED,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Simulate a `step_cm` (default 30 cm = 0.3 m) step on delayed `m_true` using firmware controller equations.

    Returns `(t_s, pos_cm, u_cms2, peak_u_raw_cms2)`.
    """
    n_ticks = int(round(duration_s / DT))
    a_t, b_t = model_ad_exact(m_true)
    d_true = max(1, int(m_true.get("d", 18)))
    plant_pipe = np.zeros(d_true, dtype=float)
    x_plant = np.zeros(3, dtype=float)  # [pos, vel, a]

    # Controller predictor state (mirrors API/xy_ctrl.c)
    d_ctrl = max(1, int(m_ctrl.get("d", 18)))
    a_c, b_c = model_ad_exact(m_ctrl)
    al_c = float(m_ctrl.get("al", 0.0))
    k_c = float(m_ctrl["k"])
    drag_c = float(m_ctrl.get("drag", 0.0))
    phi_c = np.eye(3, dtype=float)
    gam_c = np.zeros((3, d_ctrl), dtype=float)
    for j in range(d_ctrl):
        gam_c = a_c @ gam_c
        gam_c[:, j] += b_c
        phi_c = a_c @ phi_c

    ctrl_pipe = np.zeros(d_ctrl, dtype=float)
    a_est = 0.0
    z_int = 0.0
    z1 = z2 = z3 = 0.0
    eso_init = False

    if kind == "mpc":
        h_mpc, f_mpc, _ = mpc_matrices(dict(m_ctrl), tuple(mpc_q), float(mpc_rd), MPC_N)
        step_mpc = 1.0 / float(np.linalg.eigvalsh(h_mpc).max())
        plan_mpc = np.zeros(MPC_N, dtype=float)
        u_prev_mpc = 0.0

    k_vec = np.asarray(lqr_k if lqr_k is not None else CANDIDATE_XY_GAINS_LQR, dtype=float)
    wc = float(adrc_wc if adrc_wc is not None else CANDIDATE_XY_GAINS["g_xyc_adrc_wc"])
    b0 = float(adrc_b0 if adrc_b0 is not None else CANDIDATE_XY_GAINS["g_xyc_adrc_b0"])
    b1, b2, b3 = 3.0 * adrc_wo, 3.0 * adrc_wo * adrc_wo, adrc_wo ** 3

    t_out = np.arange(n_ticks, dtype=float) * DT
    pos_out = np.zeros(n_ticks, dtype=float)
    u_out = np.zeros(n_ticks, dtype=float)
    peak_u_raw = 0.0

    for tick in range(n_ticks):
        pos, vel = float(x_plant[0]), float(x_plant[1])
        pos_out[tick] = pos
        des = float(step_cm)

        if kind == "lqr":
            e = pos - des
            if abs(e) < INT_BAND:
                z_int = float(np.clip(z_int + DT * e, -INT_MAX, INT_MAX))
            x0 = np.array([pos, vel, a_est], dtype=float)
            xp = phi_c @ x0 + gam_c @ ctrl_pipe
            err_x = np.array([xp[0] - des, xp[1], xp[2], z_int], dtype=float)
            u_raw = -float(k_vec @ err_x)
        elif kind == "adrc":
            if not eso_init:
                z1 = pos
                eso_init = True
            e_eso = z1 - pos
            u_oldest = float(ctrl_pipe[0])
            z1 = z1 + DT * (z2 - b1 * e_eso)
            z2 = z2 + DT * (z3 - b2 * e_eso + b0 * u_oldest)
            z3 = z3 + DT * (-b3 * e_eso)
            u0 = (wc ** 2) * (des - z1) + 2.0 * wc * (0.0 - z2)
            u_raw = (u0 - z3) / b0
        elif kind == "mpc":
            e = pos - des
            if abs(e) < INT_BAND:
                z_int = float(np.clip(z_int + DT * e, -INT_MAX, INT_MAX))
            x0 = np.array([pos, vel, a_est], dtype=float)
            xp = phi_c @ x0 + gam_c @ ctrl_pipe
            err_x = np.array([xp[0] - des, xp[1], xp[2], z_int], dtype=float)
            lin = f_mpc @ err_x
            lin[0] -= float(mpc_rd) * u_prev_mpc
            u_vec = plan_mpc.copy()
            y_vec = plan_mpc.copy()
            t_fista = 1.0
            for _ in range(MPC_ITERS):
                t_new = 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * t_fista * t_fista))
                mom = (t_fista - 1.0) / t_new
                un = np.clip(y_vec - step_mpc * (h_mpc @ y_vec + lin), -AMAX, AMAX)
                y_vec = un + mom * (un - u_vec)
                u_vec = un
                t_fista = t_new
            plan_mpc = u_vec
            u_raw = float(u_vec[0])
            u_prev_mpc = float(np.clip(u_raw, -AMAX, AMAX))
        else:
            raise ValueError(f"Unknown controller kind: {kind!r}")

        peak_u_raw = max(peak_u_raw, abs(u_raw))
        u_applied = float(np.clip(u_raw, -AMAX, AMAX))
        u_out[tick] = u_applied

        # Commit to controller pipe (XyCtrl_Commit)
        oldest_ctrl = float(ctrl_pipe[0])
        a_est = al_c * a_est + (1.0 - al_c) * oldest_ctrl
        ctrl_pipe = np.roll(ctrl_pipe, -1)
        ctrl_pipe[-1] = u_applied

        # Advance delayed plant by one tick
        oldest_plant = float(plant_pipe[0])
        plant_pipe = np.roll(plant_pipe, -1)
        plant_pipe[-1] = u_applied
        x_plant = a_t @ x_plant + b_t * oldest_plant

    return t_out, pos_out, u_out, peak_u_raw


CANDIDATE_XY_GAINS_LQR = (
    CANDIDATE_XY_GAINS["g_xyc_lqr_k0"],
    CANDIDATE_XY_GAINS["g_xyc_lqr_k1"],
    CANDIDATE_XY_GAINS["g_xyc_lqr_k2"],
    CANDIDATE_XY_GAINS["g_xyc_lqr_k3"],
)


def evaluate_controller_checks(
    kind: str,
    label: str,
    m_true: Mapping[str, Any],
    m_ctrl: Mapping[str, Any],
    *,
    lqr_k: Sequence[float] | None = None,
    adrc_wc: float | None = None,
    adrc_b0: float | None = None,
    adrc_wo: float = CANDIDATE_ADRC_WO_RPS,
) -> ControllerCheck:
    """Run all 4 PROPOSED checks (stable, PM >= 30 deg, overshoot <= 20%, no saturation on 0.3 m step)."""
    _, pos_cm, _, peak_u_raw = simulate_closed_loop_step(
        kind,
        m_true,
        m_ctrl,
        lqr_k=lqr_k,
        adrc_wc=adrc_wc,
        adrc_b0=adrc_b0,
        adrc_wo=adrc_wo,
        step_cm=STEP_CM_PROPOSED,
        duration_s=SIM_DURATION_S_PROPOSED,
    )
    overshoot_pct = max(0.0, float((np.max(pos_cm) - STEP_CM_PROPOSED) / STEP_CM_PROPOSED * 100.0))
    settled = abs(float(pos_cm[-1]) - STEP_CM_PROPOSED) <= 0.10 * STEP_CM_PROPOSED

    if kind == "lqr":
        k_vec = lqr_k if lqr_k is not None else CANDIDATE_XY_GAINS_LQR
        rho = _lqr_closed_loop_spectral_radius(m_true, m_ctrl, k_vec)
        pm_deg, gm_db, wc_rps = _lqr_loop_margins(m_true, m_ctrl, k_vec)
    elif kind == "adrc":
        wc_val = float(adrc_wc if adrc_wc is not None else CANDIDATE_XY_GAINS["g_xyc_adrc_wc"])
        b0_val = float(adrc_b0 if adrc_b0 is not None else CANDIDATE_XY_GAINS["g_xyc_adrc_b0"])
        rho = _adrc_closed_loop_spectral_radius(m_true, int(m_ctrl.get("d", 18)), wc_val, b0_val, wo=adrc_wo)
        pm_deg, gm_db, wc_rps = _adrc_loop_margins(m_true, int(m_ctrl.get("d", 18)), wc_val, b0_val, wo=adrc_wo)
    else:
        raise ValueError(f"Unsupported check kind: {kind!r}")

    is_stable = bool(rho < 1.0 and settled and np.all(np.isfinite(pos_cm)))
    no_sat = bool(peak_u_raw < AMAX)
    passed = bool(
        is_stable
        and pm_deg >= PM_MIN_DEG_PROPOSED
        and overshoot_pct <= OVERSHOOT_MAX_PCT_PROPOSED
        and no_sat
    )
    return ControllerCheck(
        controller=kind.upper(),
        label=label,
        stable=is_stable,
        spectral_radius=rho,
        pm_deg=pm_deg,
        gm_db=gm_db,
        wc_rps=wc_rps,
        overshoot_pct=overshoot_pct,
        peak_u_cms2=peak_u_raw,
        no_saturation=no_sat,
        passed=passed,
    )


def format_comparison_report(
    var_gains: Mapping[str, float],
    candidate_gains: Mapping[str, float],
    checks_new: Mapping[str, ControllerCheck],
    checks_candidate: Mapping[str, ControllerCheck],
    delay_ticks: int,
    target_wc_rps: float,
    lqr_q: Sequence[float],
    lqr_r: float,
) -> str:
    """Format VAR 75-83 table and side-by-side checks (new sysid vs `xy_ctrl_gains_candidate.h`)."""
    var_ids = {
        "g_xyc_lqr_k0": 75,
        "g_xyc_lqr_k1": 76,
        "g_xyc_lqr_k2": 77,
        "g_xyc_lqr_k3": 78,
        "g_xyc_adrc_wc": 79,
        "g_xyc_adrc_b0": 80,
        "g_xyc_model_k": 81,
        "g_xyc_model_al": 82,
        "g_xyc_model_drag": 83,
    }
    lines = [
        f"=== XY Model-Based Controller Design (delay={delay_ticks} ticks, target_wc={target_wc_rps:.3f} rad/s) ===",
        f"LQR Q=({lqr_q[0]:.4g}, {lqr_q[1]:.4g}, {lqr_q[2]:.4g}, {lqr_q[3]:.4g}), R={lqr_r:.4g} (PROPOSED bandwidth-matched)",
        "MPC note: runtime takes VAR 47 (delay) + VAR 81-83 (k, al, drag); QP matrices XYC_MPC_H/F/STEP/RD are compile-time only.",
        "",
        f"| {'VARName':<16} | {'ID':<4} | {'candidate.h':<12} | {'sysid_new':<12} |",
        f"|{'-' * 18}|{'-' * 6}|{'-' * 14}|{'-' * 14}|",
    ]
    for name, vid in var_ids.items():
        c_val = float(candidate_gains[name])
        n_val = float(var_gains[name])
        lines.append(f"| {name:<16} | {vid:<4} | {c_val:<12.6g} | {n_val:<12.6g} |")

    lines.extend([
        "",
        "--- Closed-Loop Checks on Identified Delayed Model (PROPOSED gates: stable, PM>=30 deg, OS<=20%, |u|<262.9 cm/s^2 on 0.3 m step) ---",
        f"| {'Ctrl':<6} | {'Set':<11} | {'Stable':<6} | {'rho':<6} | {'PM (deg)':<8} | {'OS (%)':<7} | {'Peak |u|':<9} | {'NoSat':<5} | {'Gate':<4} |",
        f"|{'-' * 8}|{'-' * 13}|{'-' * 8}|{'-' * 8}|{'-' * 10}|{'-' * 9}|{'-' * 11}|{'-' * 7}|{'-' * 6}|",
    ])
    for kind in ("lqr", "adrc"):
        for chk in (checks_candidate[kind], checks_new[kind]):
            lines.append(
                f"| {chk.controller:<6} | {chk.label:<11} | {str(chk.stable):<6} | {chk.spectral_radius:<6.4f} | "
                f"{chk.pm_deg:<8.1f} | {chk.overshoot_pct:<7.1f} | {chk.peak_u_cms2:<9.1f} | "
                f"{str(chk.no_saturation):<5} | {'PASS' if chk.passed else 'FAIL':<4} |"
            )
    return "\n".join(lines)


def design_xy(
    vel_models: str | Path | Mapping[str, Any] | Sequence[str | Path | Mapping[str, Any]],
    pos_models: str | Path | Mapping[str, Any] | Sequence[str | Path | Mapping[str, Any]] | None = None,
    delay_ticks: int = 18,
    *,
    q: Sequence[float] | None = None,
    r: float = CANDIDATE_LQR_R,
    target_wc_rps: float | None = None,
    adrc_wo: float = CANDIDATE_ADRC_WO_RPS,
    print_report: bool = True,
) -> XyDesignResult:
    """Design LQR (k0-k3), ADRC (wc, b0), runtime model (k, al, drag), and MPC params from sysid vel/pos models."""
    v_list = _as_list(vel_models)
    p_list = _as_list(pos_models)
    m = extract_xy_model(v_list, p_list, delay_ticks=delay_ticks)

    wc_target = float(target_wc_rps) if target_wc_rps is not None else estimate_pid_bandwidth_rps(m, v_list, p_list)

    # 1. LQR gain calculation via discrete ARE
    if q is not None:
        q_chosen = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
        k_vec = solve_lqr_gain(m, q_chosen, r=r)
    else:
        # Search down from wc_target if needed so that all PROPOSED checks pass without saturating on 0.3 m step
        q_chosen = q_for_bandwidth(wc_target)
        k_vec = solve_lqr_gain(m, q_chosen, r=r)
        for scale in np.linspace(1.0, 0.45, 12):
            q_try = q_for_bandwidth(wc_target * float(scale))
            k_try = solve_lqr_gain(m, q_try, r=r)
            chk_try = evaluate_controller_checks("lqr", "sysid_new", m, m, lqr_k=k_try, adrc_wo=adrc_wo)
            if chk_try.passed:
                q_chosen = q_try
                k_vec = k_try
                break

    # 2. ADRC gains: b0 equals identified input gain k; wc targets closed-loop bandwidth within checks
    adrc_b0 = float(m["k"])
    adrc_wc = wc_target
    if target_wc_rps is None:
        for scale in np.linspace(1.0, 0.35, 14):
            wc_try = float(wc_target * scale)
            chk_try = evaluate_controller_checks(
                "adrc", "sysid_new", m, m, adrc_wc=wc_try, adrc_b0=adrc_b0, adrc_wo=adrc_wo
            )
            if chk_try.passed:
                adrc_wc = wc_try
                break

    # 3. Runtime model parameters & MPC parameters
    model_k = float(m["k"])
    model_al = float(m["al"])
    model_drag = float(m["drag"])
    d_ticks = int(m["d"])

    h_mpc, f_mpc, _ = mpc_matrices(dict(m), CANDIDATE_MPC_Q, CANDIDATE_MPC_RD, MPC_N)
    mpc_step = 1.0 / float(np.linalg.eigvalsh(h_mpc).max())

    mpc_runtime = {
        "g_xyc_delay_ticks": d_ticks,
        "g_xyc_model_k": model_k,
        "g_xyc_model_al": model_al,
        "g_xyc_model_drag": model_drag,
    }
    mpc_compile_only = {
        "runtime_writable": False,
        "reason": (
            "XYC_MPC_H[10][10], XYC_MPC_F[10][4], XYC_MPC_STEP, and XYC_MPC_RD are compile-time const arrays "
            "in API/xy_ctrl_gains.h (API/xy_ctrl.c:280-297). Only Smith predictor model params (VAR 47, 81-83) "
            "update at runtime."
        ),
        "q_block": list(CANDIDATE_MPC_Q),
        "rd": CANDIDATE_MPC_RD,
        "mpc_step": round(mpc_step, 8),
        "mpc_h_shape": list(h_mpc.shape),
        "mpc_f_shape": list(f_mpc.shape),
    }

    var_gains = {
        "g_xyc_lqr_k0": float(k_vec[0]),
        "g_xyc_lqr_k1": float(k_vec[1]),
        "g_xyc_lqr_k2": float(k_vec[2]),
        "g_xyc_lqr_k3": float(k_vec[3]),
        "g_xyc_adrc_wc": float(adrc_wc),
        "g_xyc_adrc_b0": float(adrc_b0),
        "g_xyc_model_k": model_k,
        "g_xyc_model_al": model_al,
        "g_xyc_model_drag": model_drag,
    }

    # 4. Evaluate checks on the identified delayed model for both new and candidate.h gains
    m_cand = {
        "k": CANDIDATE_XY_GAINS["g_xyc_model_k"],
        "al": CANDIDATE_XY_GAINS["g_xyc_model_al"],
        "tau_s": -DT / math.log(CANDIDATE_XY_GAINS["g_xyc_model_al"]),
        "tau": -DT / math.log(CANDIDATE_XY_GAINS["g_xyc_model_al"]),
        "drag": CANDIDATE_XY_GAINS["g_xyc_model_drag"],
        "d": d_ticks,
    }
    checks_new = {
        "lqr": evaluate_controller_checks("lqr", "sysid_new", m, m, lqr_k=k_vec, adrc_wo=adrc_wo),
        "adrc": evaluate_controller_checks(
            "adrc", "sysid_new", m, m, adrc_wc=adrc_wc, adrc_b0=adrc_b0, adrc_wo=adrc_wo
        ),
    }
    checks_candidate = {
        "lqr": evaluate_controller_checks(
            "lqr", "candidate.h", m, m_cand, lqr_k=CANDIDATE_XY_GAINS_LQR, adrc_wo=adrc_wo
        ),
        "adrc": evaluate_controller_checks(
            "adrc",
            "candidate.h",
            m,
            m_cand,
            adrc_wc=CANDIDATE_XY_GAINS["g_xyc_adrc_wc"],
            adrc_b0=CANDIDATE_XY_GAINS["g_xyc_adrc_b0"],
            adrc_wo=adrc_wo,
        ),
    }

    table_text = format_comparison_report(
        var_gains=var_gains,
        candidate_gains=CANDIDATE_XY_GAINS,
        checks_new=checks_new,
        checks_candidate=checks_candidate,
        delay_ticks=d_ticks,
        target_wc_rps=wc_target,
        lqr_q=q_chosen,
        lqr_r=r,
    )
    if print_report:
        print(table_text)

    return XyDesignResult(
        model=m,
        target_wc_rps=wc_target,
        lqr_q=q_chosen,
        lqr_r=float(r),
        lqr_k=(float(k_vec[0]), float(k_vec[1]), float(k_vec[2]), float(k_vec[3])),
        adrc_wc=float(adrc_wc),
        adrc_b0=float(adrc_b0),
        adrc_wo_compile_time=float(adrc_wo),
        model_k=model_k,
        model_al=model_al,
        model_drag=model_drag,
        delay_ticks=d_ticks,
        mpc_runtime_params=mpc_runtime,
        mpc_compile_time_only=mpc_compile_only,
        var_gains=var_gains,
        candidate_var_gains=dict(CANDIDATE_XY_GAINS),
        checks_new=checks_new,
        checks_candidate=checks_candidate,
        table_text=table_text,
    )
