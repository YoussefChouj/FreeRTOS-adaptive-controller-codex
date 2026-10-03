"""Live-tune session: one CMA-ES candidate per window, ticked by the campaign runner while the drone hovers.

One window per candidate: write gains -> settle -> excite -> score.
  write   CMD 0x01 PID_GAIN Kp, Ki, Kd of each tuned rate loop (gyroxPID roll, gyroyPID pitch): the pid descriptor's
          knobs (controller_descriptor.Knob, idx = axis * 3 + gain, checked with wire_target), the commands
          campaign_api.apply_params sends, sent straight through the runner's client.
  settle  settle_s at the hover point. It also covers the SysID RECOVERY after the previous window (2.0 s,
          API/sysid.c:17): SysID_Start refuses unless IDLE, and CMD 0x14 does not report the refusal.
  excite  CMD 0x14 multisine on the window's rate axis: ramp 1.5 s + dur_s + ramp 1.5 s (API/sysid.c:16).
  score   cost.window_cost over the excitation samples.
The excited axis rotates per CMA-ES generation, so each ranking compares candidates on one axis. Each axis gets one
baseline window first; best-so-far uses J_rel = J / baseline J of the same axis.

Excitation path: CMD 0x14 idx 6 start also resets the optical-flow origin and the loc PID setpoints
(TASK/send_data.c:1850-1864), which moves the WFB hover point and fence with the drone. So every start waits until the
drone sits within start_tol_m of the hover point, adds the offset at the reset to an origin-walk sum, and the supervisor
checks the real-frame position (walk + pos) against the fence box; the run ends before the walk passes walk_budget_m.

Search space: x = log(gain / baseline) per gain, box [log(1 - trust), log(1 + trust)], CMA-ES started at x = 0.
Every end (budget, evals, failures, stop) writes the baseline back; the best gains are reported, never applied.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

import numpy as np

from ground_station.livetune.cmaes import CMAES
from ground_station.livetune.cost import CostWeights, window_cost
from ground_station.livetune.supervisor import SafetyLimits, Supervisor

GAINS = ("Kp", "Ki", "Kd")
FIRMWARE_RATE: Mapping[str, float] = {"Kp": 5.0, "Ki": 0.01, "Kd": 10.0}  # gyroxPID = gyroyPID, API/pid.c:23-24
RATE_PID = {"roll": "gyroxPID", "pitch": "gyroyPID"}
ERR_IDX = {"roll": 0, "pitch": 1}           # AbortSample.rate_err_dps is (roll, pitch, yaw)
CMD_MRAC_FLAGS, MRAC_INJECTION_IDX = 0x0F, 10  # output_injection_on (firmware_contract CMD 0x0F)
CMD_SYSID = 0x14
SYSID_AXIS = {"pitch": 0, "roll": 1}        # CMD 0x14 idx 0 (TASK/send_data.c:1834)
SYSID_RAMP_S = 1.5                          # API/sysid.c:16
SYSID_RECOVERY_S = 2.0                      # API/sysid.c:17
SYSID_ALT_M = (0.30, 1.50)                  # API/sysid.c:22-23: SysID refuses to start and aborts outside
SYSID_AMP_MAX_DPS = 90.0                    # API/sysid.c:42, roll and pitch
GAIN_MAX = 200.0                            # CMD 0x01 accepts 0..200 (TASK/send_data.c:1527)
ALT_MARGIN_M = 0.15                         # PROPOSED: hover z this far inside SYSID_ALT_M
OK_STATUSES = ("budget", "max_evals", "walk_budget")  # the flight goes on to its next step; any other end lands

STEP_REQUIRED = ("loop", "axes", "budget_s")
STEP_OPTIONAL = ("max_evals", "trust", "sigma0", "baseline", "seed", "amp_dps", "mrac_off", "hold_radius_m")


@dataclass(frozen=True)
class ExciteConfig:
    amp_dps: float = 30.0  # PROPOSED: peak rate setpoint added to the excited axis
    f0_hz: float = 1.0     # PROPOSED
    f1_hz: float = 8.0     # PROPOSED: below cost f_c 10 Hz
    dur_s: float = 1.0     # firmware minimum (API/sysid.c:184)
    signal: int = 0        # log chirp: peak = amp_dps by construction (API/sysid.c:126-133). Not the multisine: its
                           # peak normalisation scans min(dur, 8) s (sysid.c:209) of a dur + 3 s signal, so a 1 s run
                           # overshoots amp_dps (~3x in the WP-28 sim)


@dataclass(frozen=True)
class LiveTuneConfig:
    loop: str = "rate"
    axes: tuple[str, ...] = ("roll", "pitch")
    budget_s: float = 60.0
    max_evals: int = 40
    trust: float = 0.3                # +-30 % of the baseline
    sigma0: float = 0.1               # in log-gain units, ~10 %
    baseline: Mapping[str, float] = field(default_factory=lambda: dict(FIRMWARE_RATE))
    seed: int = 0
    mrac_off: bool = True             # CMD 0x0F idx 10 = 0 before the first window; left off
    excite: ExciteConfig = ExciteConfig()
    settle_s: float = 2.5             # PROPOSED: >= SYSID_RECOVERY_S
    start_tol_m: float = 0.08         # PROPOSED: offset allowed at each origin reset
    start_wait_s: float = 3.0         # PROPOSED: extra settle before a candidate that cannot hold is tripped
    walk_budget_m: float = 0.4        # PROPOSED: summed origin shift
    sigma_shrink: float = 0.7         # PROPOSED: sigma factor after a trip
    min_gain: float = 0.05            # PROPOSED: recommend only a best J_rel <= 1 - min_gain
    dt_s: float = 0.02                # runner tick in the step: 50 Hz, the log plan rate
    hover_m: tuple[float, float, float] = (0.0, 0.0, 0.8)
    fence_xy_m: tuple[float, float] = (1.3, 1.7)  # TrajLimits x/y_abs_m: 0.3 m inside the fence
    limits: SafetyLimits = SafetyLimits()
    weights: CostWeights = CostWeights()

    @property
    def excite_s(self) -> float:
        return 2.0 * SYSID_RAMP_S + self.excite.dur_s

    @property
    def window_s(self) -> float:
        return self.settle_s + self.excite_s


def _num(v: Any) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def _int(v: Any) -> bool:
    return not isinstance(v, bool) and isinstance(v, int)


def parse_step(body: Mapping[str, Any], hover_m: tuple[float, float, float],
               fence_xy_m: tuple[float, float]) -> tuple[LiveTuneConfig | None, list[str]]:
    """Check a scenario `livetune` step body; (config, []) or (None, problems). Keys are checked by the caller."""
    d = LiveTuneConfig()
    p: list[str] = []
    if body.get("loop") != "rate":
        p.append(f"loop: only 'rate' is implemented, angle and yaw come later (got {body.get('loop')!r})")
    axes = body.get("axes")
    if not (isinstance(axes, list) and axes and all(a in RATE_PID for a in axes) and len(set(axes)) == len(axes)):
        p.append(f"axes: must be a non-empty list of distinct {list(RATE_PID)} (got {axes!r})")
    budget = body.get("budget_s")
    if not (_num(budget) and budget >= 2 * d.window_s):
        p.append(f"budget_s: must be a number >= {2 * d.window_s:.1f} s, two {d.window_s:.1f} s windows "
                 f"(got {budget!r})")
    max_evals = body.get("max_evals", d.max_evals)
    if not (_int(max_evals) and 1 <= max_evals <= 500):
        p.append(f"max_evals: must be an integer in [1, 500] (got {max_evals!r})")
    trust = body.get("trust", d.trust)
    if not (_num(trust) and 0.0 < trust <= 0.5):
        p.append(f"trust: must be in (0, 0.5] (got {trust!r})")
        trust = d.trust
    sigma0 = body.get("sigma0", d.sigma0)
    if not (_num(sigma0) and 0.0 < sigma0 <= 0.5):
        p.append(f"sigma0: must be in (0, 0.5] log-gain units (got {sigma0!r})")
    seed = body.get("seed", d.seed)
    if not (_int(seed) and seed >= 0):
        p.append(f"seed: must be an integer >= 0 (got {seed!r})")
    amp = body.get("amp_dps", d.excite.amp_dps)
    if not (_num(amp) and 0.0 < amp <= SYSID_AMP_MAX_DPS):
        p.append(f"amp_dps: must be in (0, {SYSID_AMP_MAX_DPS:g}] (got {amp!r})")
    mrac_off = body.get("mrac_off", d.mrac_off)
    if not isinstance(mrac_off, bool):
        p.append(f"mrac_off: must be true or false (got {mrac_off!r})")
    hold = body.get("hold_radius_m", d.limits.hold_radius_m)
    if not (_num(hold) and hold > 0.0):
        p.append(f"hold_radius_m: must be a number > 0 (got {hold!r})")
    elif hold + d.walk_budget_m > min(fence_xy_m):
        p.append(f"hold_radius_m: {hold} m + origin walk budget {d.walk_budget_m} m reaches outside the fence box "
                 f"(|x| <= {fence_xy_m[0]}, |y| <= {fence_xy_m[1]} m)")
    baseline = body.get("baseline", dict(d.baseline))
    if not (isinstance(baseline, dict) and sorted(baseline) == sorted(GAINS)
            and all(_num(v) and 0.0 < v and v * (1.0 + trust) <= GAIN_MAX for v in baseline.values())):
        p.append(f"baseline: must map exactly {list(GAINS)} to numbers > 0 with value * (1 + trust) <= {GAIN_MAX:g} "
                 f"(got {baseline!r})")
    lo, hi = SYSID_ALT_M[0] + ALT_MARGIN_M, SYSID_ALT_M[1] - ALT_MARGIN_M
    if not lo <= hover_m[2] <= hi:
        p.append(f"hover z {hover_m[2]} m is outside the excitation band [{lo:.2f}, {hi:.2f}] m (API/sysid.c:22-23)")
    if p:
        return None, p
    return LiveTuneConfig(
        loop="rate", axes=tuple(axes), budget_s=float(budget), max_evals=int(max_evals), trust=float(trust),
        sigma0=float(sigma0), baseline={g: float(baseline[g]) for g in GAINS}, seed=int(seed), mrac_off=mrac_off,
        excite=ExciteConfig(amp_dps=float(amp)), hover_m=tuple(float(v) for v in hover_m),
        fence_xy_m=tuple(float(v) for v in fence_xy_m),
        limits=SafetyLimits(hold_radius_m=float(hold)),
    ), []


def rate_knobs(axes: tuple[str, ...], baseline: Mapping[str, float], trust: float) -> dict[str, dict[str, Any]]:
    """The CMD 0x01 knob per (axis, gain), checked against the firmware idx decode."""
    from ground_station.analysis.controller_descriptor import PID_AXES, PID_GAIN_CMD, PID_GAINS, Knob, wire_target

    out: dict[str, dict[str, Any]] = {}
    for axis in axes:
        out[axis] = {}
        for g in GAINS:
            sym = f"{RATE_PID[axis]}.{g}"
            idx = PID_AXES.index(RATE_PID[axis]) * len(PID_GAINS) + PID_GAINS.index(g)
            if wire_target(PID_GAIN_CMD, idx) != sym:
                raise RuntimeError(f"CMD 0x01 idx {idx} does not write {sym}")
            b = float(baseline[g])
            out[axis][g] = Knob(sym, PID_GAIN_CMD, idx, b, b * (1.0 - trust), b * (1.0 + trust), "log")
    return out


@dataclass
class Eval:
    n: int
    gen: int
    axis: str
    kind: str                 # "baseline" or "candidate"
    gains: dict[str, float]
    status: str               # "ok" or "trip"
    J: float | None = None
    J_rel: float | None = None
    parts: dict[str, float] | None = None
    reason: str = ""
    t_s: float = 0.0          # session time at the end of the window


@dataclass
class LiveTuneResult:
    status: str = "running"
    reason: str = ""
    baseline: dict[str, float] = field(default_factory=dict)
    baseline_J: dict[str, float] = field(default_factory=dict)
    best: dict[str, float] | None = None
    best_rel: float | None = None
    recommended: dict[str, float] | None = None  # best, if it beat the baseline by min_gain; a verify flight applies it
    mean: dict[str, float] = field(default_factory=dict)
    sigma: float = 0.0
    generations: int = 0
    candidates: int = 0
    evals: list[Eval] = field(default_factory=list)
    walk_m: float = 0.0
    origin_resets: int = 0
    elapsed_s: float = 0.0
    reverted: bool = False
    es_state: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return asdict(self)


def _fmt(g: Mapping[str, float]) -> str:
    return " ".join(f"{k} {g[k]:.4g}" for k in GAINS)


class LiveTuneSession:
    """tick(sample, prim_state) once per runner tick until it returns True; close() always (reverts if needed)."""

    def __init__(self, cfg: LiveTuneConfig, send: Callable[[int, int, float], bool], clock: Callable[[], float],
                 say: Callable[[str], None] = lambda text: None) -> None:
        self.cfg = cfg
        self._send, self._clock, self._say = send, clock, say
        self.knobs = rate_knobs(cfg.axes, cfg.baseline, cfg.trust)
        self.es = CMAES(np.zeros(len(GAINS)), cfg.sigma0, math.log(1.0 - cfg.trust), math.log(1.0 + cfg.trust),
                        seed=cfg.seed)
        self.sup = Supervisor(cfg.limits, cfg.hover_m, cfg.fence_xy_m)
        self.result = LiveTuneResult(baseline=dict(cfg.baseline))
        self.done = False
        self._t0: float | None = None
        self._phase = ""
        self._t_phase = 0.0
        self._queue: list[tuple[str, int]] = []
        self._X: np.ndarray | None = None
        self._f: np.ndarray | None = None
        self._axis = cfg.axes[0]
        self._cur: tuple[str, int, dict[str, float]] = ("baseline", -1, dict(cfg.baseline))
        self._buf: list[tuple[float, float, float]] = []
        self._fails = 0
        self._walk = (0.0, 0.0)
        self._exciting = False

    def gains_of(self, x) -> dict[str, float]:
        return {g: self.cfg.baseline[g] * math.exp(float(v)) for g, v in zip(GAINS, x)}

    # ------------------------------------------------------------------ runner interface

    def tick(self, sample: Any, prim_state: int) -> bool:
        if self.done:
            return True
        now = self._clock()
        if self._t0 is None:
            self._t0 = now
            if self._begin():
                self._next(now)
            return self.done
        v = self.sup.check(sample, prim_state, self._walk)
        if v.kind == "stop":
            self._finish(v.code, v.reason)
        elif self._phase == "recover":  # baseline back after a trip: it gets settle_s to bring the drone back
            if now - self._t_phase >= self.cfg.settle_s:
                if v.kind == "trip":
                    self._finish("baseline_unsafe", f"still tripped {self.cfg.settle_s} s after the revert: {v.reason}",
                                 revert=FIRMWARE_RATE)
                else:
                    self._next(now)
        elif v.kind == "trip":
            self._trip(now, v.reason)
        elif self._phase == "settle":
            dt = now - self._t_phase
            off = math.hypot(sample.pos_m[0] - self.cfg.hover_m[0], sample.pos_m[1] - self.cfg.hover_m[1])
            if dt >= self.cfg.settle_s and off <= self.cfg.start_tol_m:
                self._start_excite(now, sample)
            elif dt >= self.cfg.settle_s + self.cfg.start_wait_s:
                self._trip(now, f"could not settle within {self.cfg.start_tol_m} m of the hover point ({off:.2f} m)")
        elif self._phase == "excite":
            self._buf.append((sample.t_s, sample.rate_err_dps[ERR_IDX[self._axis]], sample.sat_frac))
            if now - self._t_phase >= self.cfg.excite_s:
                self._exciting = False
                self._score(now)
        return self.done

    def close(self, reason: str = "") -> None:
        """End the run if it is still going (runner abort, exception): baseline back, excitation off."""
        if not self.done:
            self._finish("stopped", reason or "closed by the runner")

    # ------------------------------------------------------------------ window steps

    def _begin(self) -> bool:
        ex = self.cfg.excite
        cmds = [(CMD_MRAC_FLAGS, MRAC_INJECTION_IDX, 0.0)] if self.cfg.mrac_off else []
        cmds += [(CMD_SYSID, 1, float(ex.signal)), (CMD_SYSID, 2, ex.f0_hz), (CMD_SYSID, 3, ex.f1_hz),
                 (CMD_SYSID, 4, ex.amp_dps), (CMD_SYSID, 5, ex.dur_s)]
        for cmd, idx, val in cmds:
            if not self._send(cmd, idx, val):
                self._finish("write_failed", f"cmd 0x{cmd:02X} idx {idx} not applied", revert=None)
                return False
        return True

    def _write(self, gains: Mapping[str, float]) -> bool:
        for axis in self.cfg.axes:
            for g in GAINS:
                k = self.knobs[axis][g]
                if not self._send(k.cmd_id, k.idx, float(gains[g])):
                    return False
        return True

    def _next(self, now: float) -> None:
        cfg, res = self.cfg, self.result
        elapsed = now - self._t0
        if elapsed + cfg.window_s > cfg.budget_s:
            return self._finish("budget", f"{elapsed:.0f} s of the {cfg.budget_s:.0f} s budget used")
        if res.candidates >= cfg.max_evals:
            return self._finish("max_evals", f"{res.candidates} candidates evaluated")
        if not self._queue:
            self._axis = cfg.axes[self.es.gen % len(cfg.axes)]
            self._X, self._f = self.es.ask(), np.full(self.es.lam, np.nan)
            self._queue = [("baseline", -1)] if self._axis not in res.baseline_J else []
            self._queue += [("candidate", i) for i in range(self.es.lam)]
        kind, i = self._queue.pop(0)
        gains = dict(cfg.baseline) if kind == "baseline" else self.gains_of(self._X[i])
        self._cur = (kind, i, gains)
        if not self._write(gains):
            return self._finish("write_failed", f"gain write {_fmt(gains)} not applied")
        self._phase, self._t_phase = "settle", now
        self.sup.reset()

    def _start_excite(self, now: float, sample: Any) -> None:
        cfg = self.cfg
        walk = (self._walk[0] + sample.pos_m[0] - cfg.hover_m[0], self._walk[1] + sample.pos_m[1] - cfg.hover_m[1])
        if math.hypot(*walk) > cfg.walk_budget_m:
            return self._finish("walk_budget", f"the next origin reset would walk {math.hypot(*walk):.2f} m "
                                               f"> {cfg.walk_budget_m} m")
        if not (self._send(CMD_SYSID, 0, float(SYSID_AXIS[self._axis])) and self._send(CMD_SYSID, 6, 1.0)):
            return self._finish("write_failed", "excitation start not applied")
        self._walk = walk
        self.result.walk_m = math.hypot(*walk)
        self.result.origin_resets += 1
        self._exciting = True
        self._buf = []
        self._phase, self._t_phase = "excite", now

    def _score(self, now: float) -> None:
        cols = list(zip(*self._buf)) if self._buf else [(), (), ()]
        wc = window_cost(cols[0], cols[1], cols[2], self.cfg.excite.amp_dps, self.cfg.weights)
        if wc is None:
            return self._finish("link_lost", "no telemetry in the excitation window")
        kind, i, gains = self._cur
        res = self.result
        ev = Eval(len(res.evals) + 1, self.es.gen, self._axis, kind, gains, "ok", J=wc.J,
                  parts={"track": wc.track, "sat": wc.sat, "osc": wc.osc, "osc_hz": wc.osc_hz, "n": wc.n},
                  t_s=now - self._t0)
        if kind == "baseline":
            res.baseline_J[self._axis] = wc.J
            ev.J_rel = 1.0
        else:
            self._fails = 0
            self._f[i] = wc.J
            res.candidates += 1
            ev.J_rel = wc.J / max(res.baseline_J[self._axis], 1e-9)
            if res.best_rel is None or ev.J_rel < res.best_rel:
                res.best, res.best_rel = dict(gains), ev.J_rel
        res.evals.append(ev)
        self._maybe_tell()
        self._next(now)

    def _trip(self, now: float, reason: str) -> None:
        if self._exciting:
            self._send(CMD_SYSID, 6, 0.0)
            self._exciting = False
        kind, i, gains = self._cur
        if kind == "baseline":
            return self._finish("baseline_unsafe", f"baseline tripped: {reason}", revert=FIRMWARE_RATE)
        if not self._write(self.cfg.baseline):
            return self._finish("write_failed", f"revert after a trip ({reason}) not applied")
        res = self.result
        self._f[i] = math.inf
        res.candidates += 1
        res.evals.append(Eval(len(res.evals) + 1, self.es.gen, self._axis, kind, gains, "trip", reason=reason,
                              t_s=now - self._t0))
        self.es.shrink(self.cfg.sigma_shrink)
        self._fails += 1
        self._say(f"livetune: candidate {_fmt(gains)} tripped ({reason}); baseline restored")
        if self._fails >= self.cfg.limits.max_consecutive_fail:
            return self._finish("too_many_failures", f"{self._fails} consecutive trips, last: {reason}")
        self._maybe_tell()
        self._phase, self._t_phase = "recover", now
        self.sup.reset()

    def _maybe_tell(self) -> None:
        if self._queue or self._X is None or np.any(np.isnan(self._f)):
            return
        self.es.tell(self._X, self._f)
        self._X = None
        res = self.result
        res.generations = self.es.gen
        best = f"best J_rel {res.best_rel:.2f} at {_fmt(res.best)}" if res.best else "no feasible candidate yet"
        self._say(f"livetune gen {self.es.gen} ({self._axis}): {best}; sigma {self.es.sigma:.3f}, "
                  f"{res.candidates} candidates")

    def _finish(self, status: str, reason: str, revert: Mapping[str, float] | None | str = "baseline") -> None:
        if self.done:
            return
        if self._exciting:
            self._send(CMD_SYSID, 6, 0.0)
            self._exciting = False
        target = self.cfg.baseline if revert == "baseline" else revert
        res = self.result
        res.reverted = target is not None and self._write(target)
        if target is not None and not res.reverted:
            reason += "; revert NOT applied (link down: the landing or the firmware gain lease must cover it)"
        res.status, res.reason = status, reason
        res.mean = self.gains_of(self.es.mean)
        res.sigma = self.es.sigma
        res.generations = self.es.gen
        res.elapsed_s = (self._clock() - self._t0) if self._t0 is not None else 0.0
        res.es_state = self.es.state_dict()
        if res.best_rel is not None and res.best_rel <= 1.0 - self.cfg.min_gain:
            res.recommended = dict(res.best)
        self.done = True
        rec = (f"recommended for a verify flight: {_fmt(res.recommended)} (J_rel {res.best_rel:.2f})"
               if res.recommended else "no candidate beat the baseline by "
                                       f"{self.cfg.min_gain:.0%}: keep the baseline")
        self._say(f"livetune {status}: {reason}. {res.candidates} candidates, {res.generations} generations, "
                  f"{res.elapsed_s:.0f} s; baseline {'restored' if res.reverted else 'NOT restored'}. {rec}")
