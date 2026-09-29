# Task wp2: flightlab metrics helpers + pid_loops, motors, battery, position plugins

<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including all existing tests,
   conftest.py, model.py, registry.py, pipeline.py, config/*.yaml and schema/*.json.
2. Make every assertion pass by fixing implementation code. Keep existing tests and assertions exactly
   as they are. [ImpossibleBench, arXiv 2510.20270]
3. Write complete implementations. Every function body does real work; no "...", TODO, `pass` stubs or
   "rest of code" comments.
4. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
6. Catch only specific exceptions (ValueError, KeyError, ...) and re-raise or record them in "warnings".
   Bare `except:` and `except Exception: pass` are forbidden. [general, arXiv 2603.28592]
7. Compare floats with `np.isclose`/`pytest.approx`; test NaN with `np.isnan`/`math.isnan`.
8. State array axes explicitly (`axis=0`); write interval ends as half-open [t0, t1) unless told otherwise;
   check lengths before slicing. [general, arXiv 2511.18782]
9. Keep every function signature, name and return shape exactly as the brief specifies.
10. Import only: python stdlib, numpy, scipy, pandas (loaders only), matplotlib, jsonschema, yaml.
    Use only functions you have seen documented; when unsure, check with `python -c "help(...)"`.
11. If a command or edit fails twice the same way, change approach; never repeat an identical call.
12. Write the digest `.agent-ops/out/<id>.md` BEFORE printing DONE; the supervisor rejects a run whose
    digest file is missing, regardless of exit code.
13. Research tasks: cite canonical URLs only (no vertexaisearch / google.com/url redirects); quote <= 25 words.
14. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
Package: `ground_station/analysis/flightlab/` (below: PKG). Read these in full before writing code:
- `docs/analysis/flightlab-spec.md` sections 3, 4, 6, 6.1, 6.2 (pid_loops, motors, battery, position rows).
- PKG/model.py (FlightLog.has/get/find/window/aligned), PKG/registry.py (register_plugin, plugin protocol),
  PKG/pipeline.py (docstring, run_plugins, jsonify), PKG/tests/conftest.py (make_log, hover_log, cfg).
- PKG/schema/metrics.schema.json `$defs` segStats, loop, motorSeg, motorStats, battery, posSeg, and top-level
  `properties.loops/motors/battery/position`. PKG/config/loops.yaml, rules.yaml `params`, battery.yaml.
`cfg` = merged dict of the three yaml files (keys: loops, pid_fields, motors, mrac, phase, ctrl_limits_source,
segments, params, thresholds, battery). segs = {"armed","airborne","landing","steady": [(t0, t1), ...]}.
Missing input -> null (None) field, never an exception, never 0.0 in place of "unknown".
All thresholds/definitions below are HEURISTIC DEFAULTS; say so in each module docstring.

Contract A: `PKG/metrics.py` (numpy + stdlib only; no scipy/pandas). NaN-aware: drop non-finite samples first;
a stat with no finite samples returns float("nan").
- rms(x), mean(x), std(x) (ddof=0), p95_abs(x) = 95th percentile of |x|, max_abs(x), frac_true(mask)
  (fraction of True; nan for empty).
- iae(t, e) = np.trapezoid(|e|, t); itae(t, e) = np.trapezoid((t - t[0]) * |e|, t); pairs with finite e only;
  nan if < 2 pairs.
- welch_psd(x, fs, nperseg) -> (f, p): drop non-finite x; nperseg = min(nperseg, len(x)); len < 2 -> two empty
  arrays. Periodic Hann window (`np.hanning(nperseg + 1)[:-1]`), step = nperseg - nperseg // 2, segments
  start at 0, step, ... while start + nperseg <= len; subtract each segment's mean; P = |rfft(w*seg)|^2 /
  (fs * sum(w^2)); average over segments; multiply bins 1..end by 2, except the last bin when nperseg is even
  (Nyquist); f = np.fft.rfftfreq(nperseg, 1/fs). Must equal scipy.signal.welch(x, fs, window="hann",
  nperseg=nperseg, noverlap=nperseg // 2, detrend="constant", scaling="density") within rtol 1e-9.
- psd_peaks(f, p, fmin, fmax, k) -> list of {"hz": float, "psd": float}: interior i with p[i] > p[i-1] and
  p[i] >= p[i+1] and fmin <= f[i] (and f[i] <= fmax unless fmax is None); sort by psd descending; first k.
- band_power(f, p, lo, hi) = np.trapezoid over bins with lo <= f < hi (hi None = no upper bound); nan if < 2 bins.
- xcorr_lag_s(a, b, fs, max_lag_s): keep indices where both finite; subtract means; nan if < 2 samples or
  either std == 0. For integer L in [-M, M], M = min(round(max_lag_s*fs), n-1): c(L) = mean(a[k-L]*b[k]) over
  valid k. Return argmax_L c(L) / fs. Positive = b lags a (b[k] ~ a[k-L]).
- gain_phase_at(des, fb, fs, f0) -> (gain, phase_deg): pairs where both finite; nperseg = min(n,
  round(fs*4/f0)); Hann segments as in welch_psd (mean removed); bin j = round(f0*nperseg/fs); X, Y = rfft of
  windowed des, fb segments at bin j; H = sum(conj(X)*Y) / sum(|X|^2); gain = |H|, phase_deg =
  degrees(angle(H)); negative phase = fb lags des. (nan, nan) if n < 2 or sum(|X|^2) == 0 or j > nperseg//2.
- linear_slope(t, y) = np.polyfit(t, y, 1)[0] on finite pairs; nan if < 2 pairs.
- settle_time(t, y, frac=0.1): finite pairs; y_final = mean of the last 10% of samples (at least 1);
  band = frac*|y_final - y[0]|; return 0.0 if band == 0; else t[k] - t[0] for the smallest k such that
  |y[j] - y_final| <= band for all j >= k.
- wrap_deg(x) = ((x + 180) % 360) - 180 (elementwise, result in [-180, 180)).
- lowpass_1pole(x, fs, fc): a = 1 - exp(-2*pi*fc/fs); y[0] = x[0]; y[k] = y[k-1] + a*(x[k] - y[k-1]);
  a non-finite x[k] holds y[k] = y[k-1].
- segment_mask(t, intervals) -> bool array, True where t0 <= t < t1 for any interval.
- longest_interval(intervals) -> (t0, t1) with max t1 - t0 (first on tie), or None if empty.

Common plugin rules: `@register_plugin` class with attributes `name`, `order`; methods requires(log, cfg) ->
list[str] (missing var names; empty = run), run(log, segs, cfg) -> dict, figures(log, segs, cfg, out_dir) ->
list[Path] (matplotlib, `matplotlib.use("Agg")`, dpi 120, close each figure; return [] when there is nothing
to draw). Per-segment outputs use keys "airborne" and "steady"; a segment whose interval list is empty, or
which masks zero samples, is None.

Contract B: `PKG/plugins/pid_loops.py`: name = "loops", order = 20.
- For each loop L in cfg["loops"]: names prefix + "." + cfg["pid_fields"][f] for f in des, fb, u, sume, kp,
  ki, kd. The loop is emitted only if Des, FB or U exists. requires() returns sorted FB names of all loops
  when no loop would be emitted, else [].
- Grid: `log.aligned(present_names)` (default rate = min nominal rate of those signals); fs = 1/median(diff
  grid). e = Des - FB (for loops with wrap_deg true: metrics.wrap_deg(Des - FB)); e is None when Des or FB
  is missing, and every e-based stat is then None.
- Loop dict (schema `$defs.loop`): prefix, level, gains, limits, missing, airborne, steady.
  missing = sorted full names of Des/FB/U/SumE that are absent. limits = cfg loop limits + "source" =
  cfg["ctrl_limits_source"]; a streamed `<prefix>.UMax` / `.UiMax` / `.SumEMax` overrides that limit with its
  median and sets source "streamed". gains = {"Kp","Ki","Kd": median over airborne samples or None} if any
  of the three is present, else None.
- Per segment (segStats, all 21 keys): m = metrics.segment_mask(grid, segs[key]).
  All-sample stats over m: n (count of m & finite e, or m & finite U when e is None), e_mean, e_std, e_rms,
  e_p95_abs, e_max_abs, fb_std, des_std, u_mean, u_std, u_p95_abs; u_sat_frac = frac_true(|U| >= sat_margin
  * UMax) over finite U; sume_sat_frac likewise with SumE and SumEMax; ui_share = rms(clip(Ki*SumE, -UiMax,
  UiMax)) / rms(U) when Ki, SumE, U present, UiMax not None and rms(U) > 0, else None.
  Longest-interval stats (samples inside metrics.longest_interval(segs[key])): iae, itae; osc: f, p =
  welch_psd(e, fs, int(osc_nperseg_s*fs)), keep f >= osc_fmin_hz, need >= 3 bins else None;
  osc_peak_hz = f at max p; osc_peak_ratio = max p / median p. lag_ms = 1000*xcorr_lag_s(Des, FB, fs,
  lag_max_s); track_gain, track_phase_deg = gain_phase_at(Des, FB, fs, track_f0_hz); these three are None when
  des_std < 1e-9 or Des/FB missing. Params from cfg["params"]["pid"].
- figures: `out_dir/"loops_error.png"`, e vs t per emitted loop over airborne.
- hover_log ground truth (airborne): rate_roll e_rms ~ 2.13 (rel 0.05), osc_peak_hz == 6.0 +/- 0.5,
  u_sat_frac == 0.0; rate_pitch e_mean ~ -1.5 (abs 0.1); lag_ms/track_* None for both (Des constant).

Contract C: `PKG/plugins/motors.py`: name = "motors", order = 30. requires() = missing names of
cfg["motors"]["vars"]. run -> {"airborne": motorSeg|None, "steady": motorSeg|None} on log.aligned(motor vars
[+ throttle_var if present]) masked per segment. motorSeg:
- per_motor: {"m1".."m4" (position in vars list, 1-based): {mean, std, min, max, p95}} (np.percentile 95).
- clamp_hi_frac = fraction of samples where ANY motor >= pwm_max - clamp_margin; clamp_lo_frac: ANY motor <=
  pwm_zero + clamp_margin (cfg["params"]["motors"]["clamp_margin"]).
- yaw_pair_diff = mean((sum CCW motors - sum CW motors) / 2) using cfg["motors"]["spin"].
- yaw_pair_pct = 100 * yaw_pair_diff / (mean of all motor samples - pwm_zero); None if denominator <= 0.
- spread_p95 = 95th percentile of (max motor - min motor) per sample; throttle_mean = mean(throttle_var) or None.
- figures: `out_dir/"motors.png"`, 4 motors vs t.
- hover_log ground truth (airborne): clamp_hi_frac ~ 0.025 (abs 0.002), clamp_lo_frac == 0.0,
  yaw_pair_diff ~ 111.25 (abs 0.5).

Contract D: `PKG/plugins/battery.py`: name = "battery", order = 40. b = cfg["battery"]; requires() = [b["var"]]
if absent else []. run -> schema `$defs.battery` dict: var = b["var"]; v_rest_start = median V over samples in
armed and not in airborne with t < first airborne t0 (no airborne: median over armed); v_end = median V with
t >= last airborne t1 (no airborne: None); v_min_airborne = min V over airborne; sag_v = v_rest_start -
v_min_airborne. cells = b["cells"] if it is an int, else int(round(v_rest_start / cell_nominal_v)) clipped to
cells_range (None if v_rest_start is None). *_cell = value / cells. soc_est_start = np.interp(
v_rest_start_cell, ocv volts, ocv soc); soc_est_end likewise with v_end_cell. soc_approx =
bool(b["ocv_approximate"]). Any input None -> dependent outputs None.
- figures: `out_dir/"battery.png"`, V vs t with airborne shaded.
- hover_log ground truth: v_rest_start 16.4, v_end 15.9, v_min_airborne ~ 15.603 (abs 0.01), sag_v ~ 0.797
  (abs 0.01), cells 4, soc_est_start ~ 0.9286, soc_est_end ~ 0.8056 (abs 1e-3).

Contract E: `PKG/plugins/position.py`: name = "position", order = 50. Inputs: "ano_of.of_quality",
"ano_of.of_alt_cm", and Des/FB of loops pos_x, pos_y, alt_pos (prefixes from cfg["loops"], field names from
cfg["pid_fields"]). requires() = all those names if none is present, else []. run -> {"airborne": posSeg|None,
"steady": posSeg|None}; each input group is aligned with log.aligned on its own names and masked per segment:
- of_quality_min, of_quality_p5 (np.percentile 5), of_quality_low_frac = fraction <
  cfg["params"]["position"]["quality_min"].
- drift: ex = locx FB - Des, ey = locy FB - Des; r = hypot(ex, ey) if both loops present, |ex| or |ey| if only
  one; drift_rms = rms(r), drift_max = max(r).
- of_alt_cm_mean, of_alt_cm_std. alt_e = Z_pos FB - Des (negative = sag); alt_e_mean, alt_e_rms.
- A group whose inputs are absent -> its keys None. figures: `out_dir/"position_drift.png"` when drift
  exists, else [].
</context>

<task>
Based on the contracts above, implement contracts A to E and their tests.

ALLOW-LIST (create these; edit nothing else):
- ground_station/analysis/flightlab/metrics.py
- ground_station/analysis/flightlab/plugins/pid_loops.py
- ground_station/analysis/flightlab/plugins/motors.py
- ground_station/analysis/flightlab/plugins/battery.py
- ground_station/analysis/flightlab/plugins/position.py
- ground_station/analysis/flightlab/tests/test_metrics.py
- ground_station/analysis/flightlab/tests/test_pid_loops.py
- ground_station/analysis/flightlab/tests/test_motors.py
- ground_station/analysis/flightlab/tests/test_battery.py
- ground_station/analysis/flightlab/tests/test_position.py
- .agent-ops/out/wp2.md (digest)

Segments in tests: WP1 (segments.py) is built in parallel by another worker and is absent here. Pass segs as
a literal: HOVER_SEGS = {"armed": [(1.0, 25.0)], "airborne": [(2.0, 22.0)], "landing": [(22.0, 24.0)],
"steady": [(5.0, 21.0)]}.

Required tests (synthetic data only; make_log, hover_log, cfg fixtures):
- test_metrics: every helper against a hand-computed value; NaN inputs; welch_psd vs scipy.signal.welch
  (`pytest.importorskip("scipy.signal")`, np.allclose rtol 1e-9); psd_peaks on a 2-tone sine finds both tones
  in order; xcorr_lag_s: b = a delayed 0.05 s -> +0.05 (abs 1/fs); gain_phase_at: fb = 0.5*des delayed 0.05 s
  at f0 = 1 Hz -> gain ~ 0.5 (rel 0.05), phase ~ -18 deg (abs 3); wrap_deg(190) == -170; settle_time on a
  first-order step; lowpass_1pole step response reaches 1 - exp(-1) at t = 1/(2*pi*fc) (rel 0.05).
- test_pid_loops / test_motors / test_battery: hover_log ground truth above; every loop / segment dict
  validates against its schema `$defs` entry after pipeline.jsonify (jsonschema.Draft202012Validator with
  `{"$ref": "#/$defs/<name>", "$defs": schema["$defs"]}`); a missing input gives None, not an exception;
  requires() lists missing names; figures() writes its png into tmp_path.
- pid_loops extra: make_log with U at +UMax or -UMax for 20 % of airborne -> u_sat_frac ~ 0.2; a loop with Ki
  and SumE streamed -> ui_share not None; att_yaw error wraps (Des 179, FB -179 -> |e| == 2).
- test_position: make_log with of_quality, locx/locy Des/FB and Z_pos Des/FB -> hand-computed drift_rms,
  of_quality_low_frac, alt_e_mean; locy absent -> drift uses |ex|; all absent -> requires() non-empty.

Acceptance (run it; paste output + exit code in the digest):
`python -m pytest ground_station/analysis/flightlab/tests -q`
Expected: 0 failed; the 24 existing tests still pass.
Digest `.agent-ops/out/wp2.md` (<= 30 lines): STATUS, files, acceptance output tail, SUBSTITUTIONS, risks.
Constraints: no firmware/OBJ edits, no flash/probe, no network, foreground only, do not commit.
</task>
