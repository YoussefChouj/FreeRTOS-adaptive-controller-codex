# flightlab — deterministic post-flight analysis (Workflow A)

Status: design agreed 2026-09-29 (grilling Q1–Q3 confirmed by the operator, Q4–Q8 = supervisor recommendations
accepted with "go ahead, I agree with the recommendations"). This file is the contract every worker brief cites.
If a brief and this spec disagree, the spec wins; if the spec is silent, STOP and ask the supervisor.

## 1. Goal

`python -m ground_station.analysis.flightlab analyze logs/vofa/flight16` reads one manually logged flight and writes
everything needed to judge the controller, plus deterministic tuning recommendations. It is the long-term basis
for experiments, and Workflow B (autonomous flight loop) will call the same command and read `metrics.json`.

Non-goals (v1): no LLM inside the pipeline, no auto-applying gains, no live streaming, no firmware changes,
no edits to `ground_station/analysis/flight_report.py` (import from it only).

## 2. Facts the design rests on (measured 2026-09-29 on flight16)

- VOFA log = `<stem>.meta.json` + `<stem>.slotN.csv`, N = 0..3. CSV header: `t_src_ms,t_host_s,seq,<var>...`.
- `meta.json` keys: name, mode, notes, seconds, window_s, started_at, ended_at, git, elf_mtime,
  preset{name, notes, slots[{rate, vars}]}, vofa_channels, state, error, slots, result.
- `t_src_ms` = firmware clock, strictly increasing within a slot (0 back-steps in flight16), 10 ms steps at 100 Hz.
  It is the ONLY time base. `t_host_s` is host arrival time and repeats values (batching, WiFi jitter).
- `seq` = uint8 per ROW, wraps 255 -> 0 (flight16 slot0: 12706 rows, 49 wraps, 0 drops).
  Drop count per step = `((seq[i] - seq[i-1]) mod 256) - 1`. Gaps >= 256 rows are invisible to seq, so ALSO
  flag `t_src` steps > 1.5 / rate_hz.
- Slot -> var assignment changes per preset. Resolve every signal by firmware var NAME, never by slot index.
- flight16 slots: 0 = 100 Hz / 62 vars, 1 = 100 Hz / 10, 2 = 25 Hz / 52, 3 = 10 Hz / 31.
- PID struct `PIDTypeDef` (Global_file/robot_types.h) fields: Des FB Kp Ki Kd Up Ui Ud E PreE SumE U UMax UpMax
  UiMax UdMax SumEMax EMin aw_mode Kt. Instances under `Ctrler.`: rollPID pitchPID yawPID gyroxPID gyroyPID
  gyrozPID Z_posPID Z_ratePID locxPID locyPID locxsPID locysPID. gyrox = roll rate, gyroy = pitch rate.
- `flight_phase` enum (API/flight_fsm.h): 0 GROUND_IDLE, 1 FLYING, 2 LANDING, 3 LANDED. `DroneStatus.ARM_Status`.
- MRAC mode flags (streamed in slot2 of flight16): `mrac_flags.adaptation_on`, `mrac_flags.output_injection_on`
  (send_data.c:1713: injection 0 = motors see pure PID). Mode: off = adaptation 0; shadow = adaptation 1 and
  injection 0; active = both 1. `g_ctrl_select` also streamed.
- MRAC per axis (roll, pitch, yaw, z_rate): `mrac_state.<ax>.{e,u_nom,u_ad,x,r,e_dot,u_def,Theta[0..2],Whatf[0..2]}`.
- Gains/limits (Kp, Ki, Kd, UMax, UiMax, SumEMax, EMin) are RAM fields and were NOT streamed in flight16, so
  saturation and integrator-limit metrics need either streamed limits or `config/loops.yaml` values.
- Motors `mymotor.motor1..4`; yaw pairs from earlier flights: M1/M2 = CW props, M3/M4 = CCW props.

## 3. Package layout (exact; do not add top-level files)

```
ground_station/analysis/flightlab/
  __init__.py            # exports load, FlightLog, Signal, analyze
  __main__.py            # CLI (section 9)
  model.py               # Signal, SlotInfo, FlightLog                      [WP0 supervisor]
  registry.py            # plugin + rule registries                          [WP0 supervisor]
  pipeline.py            # analyze(): load -> segments -> plugins -> rules -> render -> ledger   [WP0]
  loaders/__init__.py    # load(path) -> FlightLog, format dispatch          [WP0]
  loaders/vofa.py        # VOFA adapter                                      [WP1]
  loaders/session.py     # dashboard session adapter: raise NotImplementedError("v2")   [WP0]
  segments.py            # phase segmentation                                [WP1]
  metrics.py             # pure numpy helpers (section 6.1)                  [WP2]
  plugins/data_quality.py                                                    [WP1]
  plugins/pid_loops.py   plugins/motors.py  plugins/battery.py  plugins/position.py      [WP2]
  plugins/spectrum.py    plugins/mrac.py                                     [WP3]
  rules/health.py  rules/pid.py  rules/mrac.py                               [WP4]
  report/figures.py (shared style helpers only) report/render_md.py report/render_html.py  [WP4]
  ledger.py  compare.py                                                      [WP4]
  config/loops.yaml  config/rules.yaml  config/battery.yaml                  [WP0]
  schema/metrics.schema.json                                                 [WP0]
  tests/conftest.py (synthetic FlightLog factory)                            [WP0]
  tests/test_<module>.py  (one per module, written by the module's owner)
```

Plugin figures live in the plugin module (`def figures(...)`), using the style helpers in `report/figures.py`.

## 4. Data model (`model.py`, written by the supervisor — workers must not change it)

```python
@dataclass(frozen=True)
class Signal:
    name: str          # firmware var name, e.g. "Ctrler.gyroxPID.FB"
    t: np.ndarray      # float64 seconds, (t_src_ms - log.t0_src_ms) / 1000
    v: np.ndarray      # float64 values, NaN where the CSV cell is empty/unparseable
    rate_hz: float     # nominal slot rate from meta.preset.slots[i].rate
    slot: int

@dataclass
class SlotInfo:
    index: int; rate_hz: float; n_rows: int; duration_s: float; rate_measured_hz: float
    seq_drops: int; tsrc_gaps: int; drop_pct: float; dt_median_ms: float; dt_p99_ms: float; dt_max_ms: float
    tsrc_backsteps: int; host_latency_std_ms: float; vars: list[str]

@dataclass
class FlightLog:
    name: str; source_format: str; source_paths: list[str]; meta: dict
    t0_src_ms: float            # min t_src_ms over all slots
    duration_s: float
    signals: dict[str, Signal]
    slots: list[SlotInfo]
    def has(self, name: str) -> bool
    def get(self, name: str) -> Signal                    # KeyError if absent
    def find(self, pattern: str) -> list[str]             # fnmatch over names, sorted
    def window(self, name: str, t0: float, t1: float) -> Signal   # inclusive t0, exclusive t1
    def aligned(self, names: list[str], rate_hz: float | None = None,
                t0: float | None = None, t1: float | None = None) -> tuple[np.ndarray, dict[str, np.ndarray]]
        # linear interpolation onto a common grid; default rate = MIN rate of the inputs (never upsample
        # a slow signal by default); NaN outside each signal's own time span (no extrapolation)
```

## 5. Segments (`segments.py`)

`segment(log, cfg) -> dict[str, list[tuple[float, float]]]` with keys:
- `armed`: `DroneStatus.ARM_Status == 1`.
- `airborne`: `flight_phase == 1` (FLYING).
- `landing`: `flight_phase == 2`.
- `steady`: airborne minus the first `takeoff_settle_s` (cfg, default 3.0) after each airborne start, minus the
  last `pre_land_s` (default 1.0) before each airborne end, and only where `Ctrler.Z_posPID.Des`,
  `Ctrler.locxPID.Des`, `Ctrler.locyPID.Des` (those present) each change by less than their `des_hold_tol`
  (cfg per loop) within a rolling `hold_window_s` (default 2.0). Drop intervals shorter than `min_steady_s` (5.0).
- Every interval list is sorted, non-overlapping, and each interval uses the phase signal's own sample times.
- If `flight_phase` is absent, `airborne` falls back to `armed` and a warning is added.

Every plugin metric that depends on a segment is reported for `airborne` AND `steady` (keys `airborne`, `steady`),
`null` when the segment is empty.

## 6. Plugins

Plugin interface (`registry.py`):
```python
class Plugin(Protocol):
    name: str                        # key in metrics.json
    def requires(self, log: FlightLog, cfg: dict) -> list[str]   # missing var names; [] = can run
    def run(self, log: FlightLog, segs: dict, cfg: dict) -> dict  # JSON-serialisable, floats or None only
    def figures(self, log: FlightLog, segs: dict, cfg: dict, out_dir: Path) -> list[Path]
```
Registered with `@register_plugin`. A plugin whose `requires` is non-empty is skipped and listed under
`plugins_skipped: {name: [missing vars]}`. A plugin that raises is caught by the pipeline, the traceback text
goes to `plugins_failed`, and the other plugins still run. No plugin may print; use `warnings` in its dict.

### 6.1 `metrics.py` helpers (pure functions, numpy only, NaN-aware, each unit-tested on synthetic data)
`rms, mean, std, p95_abs, max_abs, iae(t,e), itae(t,e), frac_true(mask), welch_psd(x, fs, nperseg)`
(own numpy implementation, Hann window, 50 % overlap, density scaling), `psd_peaks(f, p, fmin, fmax, k)`,
`band_power(f, p, lo, hi)`, `xcorr_lag_s(a, b, fs, max_lag_s)`, `gain_phase_at(des, fb, fs, f0)` (cross-spectrum
estimate at frequency f0), `linear_slope(t, y)`, `settle_time(t, y, frac=0.1)`, `wrap_deg(x)` (to [-180, 180)),
`lowpass_1pole(x, fs, fc)`. No scipy (not guaranteed installed); no pandas inside helpers.

### 6.2 Plugin catalogue (metric key names are fixed by `schema/metrics.schema.json`)

| Plugin | Output section | Metrics |
| --- | --- | --- |
| data_quality | `data_quality` | per slot: every SlotInfo field; `stuck_vars` (constant over airborne), `nan_vars` (>50 % NaN), `worst_drop_pct`, `clock_drift_ppm` (slope of t_host vs t_src) |
| pid_loops | `loops.<loop>` per loops.yaml entry | per segment: n, e_mean, e_std, e_rms, e_p95_abs, e_max_abs, iae, itae, fb_std, des_std, u_mean, u_std, u_p95_abs, u_sat_frac, sume_sat_frac, ui_share, osc_peak_hz, osc_peak_ratio, lag_ms, track_gain, track_phase_deg; `gains` (streamed Kp/Ki/Kd/limits or null) |
| motors | `motors` | per motor mean/std/min/max/p95; clamp_hi_frac; yaw_pair_diff (mean(M3+M4-M1-M2)/2) and its % of mean; spread_p95; throttle_mean (`Throttle_out`) |
| battery | `battery` | v_rest_start (median over armed-but-not-airborne before first takeoff), v_end, v_min_airborne, sag_v, per-cell versions (cells from battery.yaml), soc_est_start/end from battery.yaml OCV table (flagged approximate) |
| position | `position` | OF: of_quality min/p5/frac below `quality_min`, drift radius rms/max of (locx.FB-Des, locy.FB-Des), of_alt_cm stats; altitude: Z_pos e_mean/e_rms (sign convention: FB - Des < 0 = sag) |
| spectrum | `spectrum` | for each rate loop FB and U, airborne: top 5 peaks (hz, psd), band power 0-2, 2-8, 8-20, 20-Nyquist; motor RPM peaks if `rpm_dbg_period_cyc[*]` present (reuse `ground_station/analysis/rpm_signals.py` conversion) |
| mrac | `mrac.<axis>` | mode_frac {off, shadow, active} over airborne; e_rms; u_nom_rms; u_ad_rms; authority_ratio = u_ad_rms / u_nom_rms; u_ad_max_abs; u_ad_hf_frac (power > `hf_cutoff_hz` / total); corr_uad_unom and corr_uad_e, raw and after 1 Hz lowpass; per weight (Theta[i], Whatf[i]): final, max_abs, slope_last30 (per s, least-squares over the last `conv_window_s` s of airborne), converged (bool: abs(slope_last30) * conv_window_s < `conv_tol` * max(abs(final), eps)), t90_s |

`loops.yaml` (WP0) defines each loop: `prefix` (e.g. `Ctrler.gyroxPID`), `level` (rate/attitude/velocity/position/
altitude_pos/altitude_rate), `axis`, `unit` (null when unverified), `wrap_deg` (att_yaw only), `limits` (UMax,
UiMax, SumEMax: null unless known), `des_hold_tol`. Error is always computed as `Des - FB` (wrapped for yaw),
never read from the `E` field. Adding a controller = new plugin file + yaml entries + rules; no core change.

## 7. Recommendations (`rules/*.py`, deterministic, no LLM)

```python
@dataclass
class Recommendation:
    id: str                 # e.g. "PID-OSC-rate_roll"
    severity: str           # "info" | "warn" | "critical"
    category: str           # "data" | "pid" | "mrac" | "hardware" | "battery" | "logging"
    target: str | None      # firmware field, e.g. "Ctrler.gyroxPID.Kd"
    action: str             # "increase" | "decrease" | "investigate" | "add_to_preset" | "enable" | "disable"
    factor: float | None    # suggested multiplier, e.g. 0.85
    evidence: dict          # {metric json-path: value} — every number the rule read
    rationale: str          # fixed template string filled with evidence values
    confidence: str         # "low" | "medium" | "high"
```
Rules register with `@register_rule(requires=[metric paths])`; a rule whose inputs are missing/null is skipped,
never errors. Every threshold lives in `config/rules.yaml` (initial values are HEURISTIC DEFAULTS, not measured;
recalibrate on flights 12–16). v1 rule set (each gets a positive and a negative unit test):

| id | fires when | says |
| --- | --- | --- |
| DQ-DROP | any slot drop_pct > drop_warn | conclusions weakened; check WiFi |
| DQ-MISSING | a loop/axis has partial vars | add named vars to preset |
| DQ-STUCK | a stuck var matches no `expected_const` pattern | variable not updating; check logging/sensor |
| LOG-GAINS | gains not streamed | add a 1 Hz params slot with the PID structs (limits known next flight) |
| PID-OSC | rate/att loop osc_peak_ratio > osc_ratio and osc_peak_hz > osc_fmin | f > d_band_hz: decrease Kd (x0.85); else decrease Kp (x0.85) |
| PID-BIAS | steady abs(e_mean) > bias_k * e_std and abs(e_mean) > bias_abs[level] | increase Ki or check integrator gating (EMin, SumEMax, aw_mode) |
| PID-IWINDUP | sume_sat_frac > sat_warn | raise SumEMax/UiMax (integrator cannot hold the offset) |
| PID-SAT | u_sat_frac > sat_warn | saturated: gain changes will not help; check trim/hardware/limits |
| PID-LAG | des_std > excite_min and (track_phase_deg < -lag_deg or track_gain < gain_min) | increase Kp of this loop (x1.15) |
| MOT-CLAMP | clamp_hi_frac > clamp_warn | no thrust headroom; hardware or battery |
| MOT-YAWPAIR | abs(yaw_pair_pct) > yawpair_warn | CW/CCW thrust imbalance; props/motors |
| BAT-LOW | v_min per cell < cell_crit | critical: land earlier |
| BAT-SAG | sag per cell > sag_warn | battery health / internal resistance |
| ALT-SAG | altitude steady e_mean indicates FB below Des beyond alt_sag_m | raise hover throttle / Z_rate integrator (EMin, SumEMax, UiMax) |
| MRAC-READY | mode shadow, authority_ratio in [ready_lo, ready_hi], all weights converged, u_ad_hf_frac < hf_max | axis is a candidate for active mode |
| MRAC-DRIFT | any weight not converged | reduce gamma or enable projection before going active |
| MRAC-CHATTER | u_ad_hf_frac > hf_max | filter u_ad / reduce gamma |
| MRAC-WORSE | mode active and ledger baseline (same preset name, mode != active) exists and steady e_rms worse by > worse_pct | return axis to shadow / reduce gamma |

## 8. Outputs

`logs/vofa/reports/<flight>/` (untracked): `metrics.json` (schema_version 1, validated against the schema before
writing), `recommendations.json` (list of Recommendation dicts sorted critical > warn > info), `report.md`
(summary table, data-quality table, per-loop tables, recommendations, embedded `figs/*.png` by relative path),
`report.html` (single file, images base64-inlined, no external URLs), `figs/*.png` (dpi 120; `--pdf` also writes
PDFs). `metrics.json` top level: `schema_version, flight{name, source_format, started_at, ended_at, git,
elf_mtime, notes, preset, duration_s}, segments, controller{mrac_mode, ctrl_select}, data_quality, loops, motors,
battery, position, spectrum, mrac, plugins_run, plugins_skipped, plugins_failed, warnings`.

Ledger `docs/flights/ledger.csv` (tracked): one row per flight, upsert keyed on (flight, started_at). Columns:
flight, started_at, analyzed_at, git, notes, preset, mrac_mode, duration_s, airborne_s, worst_drop_pct,
v_rest_start, v_min_airborne, then `e_rms_steady_<loop>` for every loops.yaml loop, motor clamp_hi_frac,
yaw_pair_pct, n_warn, n_critical.

Compare: `compare A B` flattens both metrics.json, writes `logs/vofa/reports/compare_<A>_vs_<B>/compare.md` with
per-metric A, B, delta, delta %, plus preset var diff and `git log --oneline A_git..B_git` when both hashes exist.

## 9. CLI

```
python -m ground_station.analysis.flightlab analyze <stem|meta.json> [--out DIR] [--pdf] [--no-html] [--no-ledger]
python -m ground_station.analysis.flightlab compare <stemA> <stemB>
python -m ground_station.analysis.flightlab ledger --rebuild        # re-analyse every logs/vofa/*.meta.json
```
Exit code 0 on success even when plugins are skipped; 2 when the log cannot be loaded; 1 on internal error.
Prints only the output folder path and a one-line summary (n recs by severity).

## 10. Acceptance (supervisor verifies; worker "DONE" is a claim, not evidence)

1. `analyze logs/vofa/flight16` exits 0 and writes all section-8 files; metrics.json validates.
2. Every `logs/vofa/*.meta.json` flight analyses without an exception (missing vars -> skipped plugins).
3. Supervisor cross-checks at least 5 numbers against an independent pandas computation (e.g. rate_roll
   airborne e_rms, slot0 drop_pct, v_min_airborne, mrac.roll.u_ad_rms, motor1 mean).
4. `pytest ground_station/analysis/flightlab` passes; every helper and rule has synthetic-data tests
   (sine -> PSD peak within one bin; known RMS; injected seq gaps with wrap -> exact drop count; shifted sine ->
   known lag; crafted metrics -> rule fires / does not fire).
5. Existing `ground_station/analysis/tests` still pass; `flight_report.py` unchanged.
