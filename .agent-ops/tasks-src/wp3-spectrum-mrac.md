# Task wp3: flightlab spectrum + mrac plugins

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
Read first, in this order: docs/analysis/flightlab-spec.md sections 5, 6.2 (rows `spectrum`, `mrac`) and 8;
ground_station/analysis/flightlab/model.py (FlightLog.has / find / aligned); registry.py; pipeline.py
(module docstring, run_plugins, jsonify); metrics.py (welch_psd, psd_peaks, band_power, lowpass_1pole,
linear_slope, segment_mask, longest_interval, rms, max_abs); plugins/motors.py (plugin shape to mirror);
tests/conftest.py; tests/test_battery.py (schema-validation pattern); schema/metrics.schema.json ($defs loopSpec,
mracAxis, mracStats, weight; top-level properties `spectrum` and `mrac`); config/loops.yaml (loops, pid_fields,
mrac); config/rules.yaml (params.spectrum, params.mrac, params.pid.osc_fmin_hz);
ground_station/analysis/rpm_signals.py (compute_rpm only).

Missing input -> null (None) field, never an exception, never 0.0 in place of "unknown".
All thresholds/definitions below are HEURISTIC DEFAULTS; say so in each module docstring.
nan is allowed inside run() output: pipeline.jsonify turns nan into null. Tests validate AFTER jsonify.
Use numpy, metrics.py and rpm_signals.compute_rpm only (no scipy, no pandas).

Notation: P = cfg["params"]; L = cfg["loops"]; F = cfg["pid_fields"]; M = cfg["mrac"].
log.aligned(names) defaults to the lowest nominal rate of its inputs; keep that default everywhere.
fs of an aligned grid = 1.0 / float(np.median(np.diff(grid))). nperseg(fs) = int(round(P["spectrum"]["nperseg_s"] * fs)).
fill(x): replace non-finite samples by np.interp over the finite ones (index axis); None if fewer than 2 finite.

## Contract A: plugins/spectrum.py
@register_plugin class SpectrumPlugin: name = "spectrum", order = 60.
Rate loops = [k for k, v in L.items() if v["level"] == "rate"] (yaml order); fb = L[k]["prefix"] + "." + F["fb"],
u = L[k]["prefix"] + "." + F["u"]. A loop is used only when BOTH fb and u exist (log.has).
A1 requires(log, cfg): [] if any rate loop has both fb and u; else the sorted absent fb/u names of all rate loops.
A2 private helper _loop_psds(log, segs, cfg) -> (iv, {loop: {"fb": (f, p), "u": (f, p)}}), used by run() AND figures():
   iv = metrics.longest_interval(segs.get("airborne") or []); iv None -> (None, {}).
   Per used loop: grid, data = log.aligned([fb, u], t0=iv[0], t1=iv[1]); skip the loop when len(grid) < 2,
   len(grid) < nperseg(fs), or fill() returns None; (f, p) = metrics.welch_psd(fill(x), fs, nperseg(fs)).
A3 run(log, segs, cfg) -> {"segment": "airborne" if iv else None, "loops": {...}, "rpm": ...}
   loops[k] = {"fb_peaks": psd_peaks(f, p, P["pid"]["osc_fmin_hz"], None, P["spectrum"]["top_k"]) on fb,
               "u_peaks": the same on u,
               "band_power": {"fb": {key: band_power(f, p, lo, hi)}, "u": {key: ...}}}
   Band keys from P["spectrum"]["bands_hz"]: f"{lo:g}-{hi:g}", or f"{lo:g}-nyq" when hi is None
   -> "0-2", "2-8", "8-20", "20-nyq" (hi None = up to Nyquist; band_power already does this).
   rpm: discover periods with log.find("rpm_dbg_period_cyc[[]*]"); for index i with both "rpm_dbg_period_cyc[i]"
   and "rpm_dbg_edges[i]": aligned over iv, rpm = np.asarray(compute_rpm(list(period), list(edges)), float),
   same fill/length/nperseg rules, key f"m{i+1}" -> psd_peaks(f, p, P["pid"]["osc_fmin_hz"], None, top_k).
   rpm = None when no motor qualifies or iv is None.
A4 figures(log, segs, cfg, out_dir) -> [out_dir / "spectrum.png"]: one semilogy subplot per loop from _loop_psds
   (fb and u lines, title = loop name, x label "Hz"), dpi=120, matplotlib Agg as in plugins/motors.py;
   [] when there is nothing to plot.

## Contract B: plugins/mrac.py
@register_plugin class MracPlugin: name = "mrac", order = 70.
Axes = M["axes"] (yaml order); var(a, k) = M["axes"][a]["prefix"] + "." + M["fields"][k].
B1 requires(log, cfg): [] if any axis has var(a, "u_ad"); else the sorted list of all var(a, "u_ad").
B2 run(log, segs, cfg) -> {axis: mracAxis} for EVERY axis in M["axes"]:
   "prefix": the axis prefix;
   "missing": [var(a, k) for k in M["fields"] if not log.has(var(a, k))] (fields yaml order);
   "mode_frac": A = M["flags"]["adaptation"], I = M["flags"]["injection"], aligned together; samples inside
      segs["airborne"] (segment_mask) with both finite; a = A > 0.5, i = I > 0.5;
      off = mean(~a), shadow = mean(a & ~i), active = mean(a & i); all three None when a flag is missing
      or there is no such sample;
   "airborne", "steady": _stats(seg), or None when var(a, "u_ad") is missing or the segment has no sample;
   "weights": {rel_name: weight} ({} when none).
B3 _stats(seg): one aligned grid over the present ones of e, u_nom, u_ad; mask = segment_mask(grid, segs[seg]).
   e_rms, u_nom_rms, u_ad_rms = metrics.rms(x[mask]); u_ad_max_abs = metrics.max_abs(u_ad[mask]).
   authority_ratio = u_ad_rms / u_nom_rms; None when u_nom is missing, nan, or <= P["mrac"]["eps"].
   u_ad_hf_frac: longest interval of segs[seg]; u_ad aligned on it, fill(), welch_psd with nperseg(fs);
      band_power(f, p, P["mrac"]["hf_cutoff_hz"], None) / band_power(f, p, 0, None); None when the interval has
      fewer than nperseg samples or the total is nan or <= eps.
   corr_uad_unom, corr_uad_e: Pearson on masked samples where both are finite; None if < 3 pairs or a std is 0.
   corr_uad_unom_lp, corr_uad_e_lp: the same after metrics.lowpass_1pole(x, fs, P["mrac"]["lp_hz"]) applied to
      the FULL aligned series of both signals, then masked. Any stat whose input var is missing -> None.
B4 weights: for pat in M["weight_patterns"]: names = log.find(prefix + "." + pat); key = name[len(prefix) + 1:]
   (e.g. "Theta[0]"); dedupe, sorted. Per weight: grid, data = log.aligned([name]); am = airborne mask & finite;
   no sample in am -> all five fields None. Otherwise:
   final = last value in am; max_abs = max |w| in am;
   t_end = last time in am; win = am & (grid >= t_end - P["mrac"]["conv_window_s"]);
   slope_last30 = metrics.linear_slope(grid[win], w[win]) (units per s; nan -> None);
   converged = abs(slope_last30) * conv_window_s < conv_tol * max(abs(final), eps); None when slope is None;
   t90_s: w0, t0 = first value and time in am; d = final - w0; None when abs(d) <= eps; else the first t in am with
      (w - w0) * sign(d) >= t90_frac * abs(d); t90_s = t - t0.
B5 figures: mrac_weights.png (one subplot per axis that has weights, all weight lines vs time) and mrac_uad.png
   (one subplot per axis with u_ad: u_ad and u_nom vs time); dpi=120; Agg; return only the files written.
</context>

<task>
Based on the contracts above, implement contracts A and B and their tests.

ALLOW-LIST (create these; edit nothing else):
- ground_station/analysis/flightlab/plugins/spectrum.py
- ground_station/analysis/flightlab/plugins/mrac.py
- ground_station/analysis/flightlab/tests/test_spectrum.py
- ground_station/analysis/flightlab/tests/test_mrac.py
- .agent-ops/out/wp3.md (digest)
If a contract cannot be met without touching another file, stop and write STATUS: BLOCKED with the reason.

Required tests (synthetic data only; make_log and cfg fixtures; copy.deepcopy(cfg) before overriding a value;
fs = 100 Hz, t = 0..25 s; segments literal
HOVER_SEGS = {"armed": [(1.0, 25.0)], "airborne": [(2.0, 22.0)], "landing": [(22.0, 24.0)], "steady": [(5.0, 21.0)]}):
spectrum
 S1 rate_roll FB = sin(2*pi*5*t) + 0.5*sin(2*pi*30*t), U = 2*FB: the two fb_peaks hz are within 0.5 of 5 and 30;
    fb band "2-8" == approx(0.5, rel=0.1), "20-nyq" == approx(0.125, rel=0.1), "0-2" < 0.01, "8-20" < 0.01;
    u band "2-8" == approx(2.0, rel=0.1). (A sine of amplitude A carries power A**2/2.)
 S2 rpm_dbg_period_cyc[0] = 60*168e6 / (6000 + 300*sin(2*pi*10*t)), rpm_dbg_edges[0] = np.arange(len(t)):
    rpm["m1"] has a peak within 0.5 Hz of 10.
 S3 airborne [] -> {"segment": None, "loops": {}, "rpm": None}; no rpm vars -> rpm is None.
 S4 requires(): [] with rate_roll FB and U; the missing names when no rate loop has both.
 S5 jsonify(run(...)) validates against {**schema["properties"]["spectrum"], "$defs": schema["$defs"]}.
 S6 figures() writes spectrum.png into tmp_path.
mrac (axis roll unless stated)
 M1 u_nom = sin(2*pi*0.5*t), u_ad = 0.1*u_nom, e = cos(2*pi*0.5*t): airborne authority_ratio == approx(0.1, rel=1e-6),
    corr_uad_unom == approx(1.0, abs=1e-9), corr_uad_unom_lp == approx(1.0, abs=1e-6).
 M2 u_ad = sin(2*pi*10*t) -> airborne u_ad_hf_frac > 0.95; u_ad = sin(2*pi*0.5*t) -> < 0.05.
 M3 Theta[0] = 0 for t < 2, 1 - exp(-(t - 2)) after; params.mrac.conv_window_s = 5.0: converged is True,
    t90_s == approx(math.log(10), abs=0.02), final == approx(1.0, abs=1e-6).
 M4 Theta[1] = 0.1*t (same override): converged is False.
 M5 adaptation_on = 1 everywhere, output_injection_on = 1 for t >= 17 else 0:
    mode_frac == approx({"off": 0.0, "shadow": 0.75, "active": 0.25}, abs=0.02); flags absent -> all three None.
 M6 yaw with all seven fields except u_nom -> yaw["missing"] == ["mrac_state.yaw.u_nom"] and its airborne
    authority_ratio is None; an axis with no vars -> its airborne and steady are None and its weights == {}.
 M7 requires(): [] when roll u_ad exists; the 4 sorted u_ad names when none exists.
 M8 each axis of jsonify(run(...)) validates against {"$ref": "#/$defs/mracAxis", "$defs": schema["$defs"]}.
 M9 figures() writes mrac_weights.png and mrac_uad.png into tmp_path.
Load the schema the way tests/test_battery.py does.

Acceptance (run it; paste output + exit code in the digest):
python -m pytest ground_station/analysis/flightlab/tests -q
Expected: 0 failed; the 69 existing tests still pass.

Digest .agent-ops/out/wp3.md (<= 30 lines): STATUS, files, acceptance tail, SUBSTITUTIONS, risks.
Constraints: no firmware/OBJ edits, no flash/probe, no network, foreground only, do not commit.
</task>
