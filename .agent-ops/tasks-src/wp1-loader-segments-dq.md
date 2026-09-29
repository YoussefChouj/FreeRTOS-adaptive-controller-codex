# Task wp1: flightlab VOFA loader, segments, data_quality plugin

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
- `docs/analysis/flightlab-spec.md` sections 3, 4, 5, 6, 6.2 (data_quality row).
- PKG/model.py (Signal, SlotInfo, FlightLog), PKG/registry.py, PKG/loaders/__init__.py (LoadError, load()),
  PKG/pipeline.py (docstring, run_plugins, jsonify), PKG/tests/conftest.py (make_log, hover_log, cfg).
- PKG/schema/metrics.schema.json `$defs.slot` and `$defs.dataQuality`; PKG/config/loops.yaml `phase`,
  PKG/config/rules.yaml `segments` and `params.data_quality`.

VOFA log on disk (real logs are NOT in git; build synthetic ones in `tmp_path` for tests):
- `<stem>.meta.json`: JSON dict. `meta["preset"]["slots"]` is a list; entry i has `rate` (Hz) and `vars`.
  Slot i data file: `<stem>.slot<i>.csv` in the same directory.
- CSV header: `t_src_ms,t_host_s,seq,<var1>,<var2>,...`. `t_src_ms` = firmware ms clock (the only time base),
  `t_host_s` = host receive time in s, `seq` = uint8 counter that wraps 255 -> 0. Cells may be empty.
- Real example row: `1313430,0.0780,0,-0.1231,...`. A real meta with an empty `preset.slots[i].vars` list
  and no CSV files exists (a failed capture); it must raise LoadError.

Contract A: `PKG/loaders/vofa.py`
`load_vofa(meta_path: Path, gap_factor: float = 1.5) -> FlightLog`
- stem = meta_path.name minus ".meta.json". For each i in range(len(meta["preset"]["slots"])):
  csv = meta_path.parent / f"{stem}.slot{i}.csv". Missing csv -> `LoadError(f"{csv.name}: missing")`.
  A csv with zero data rows -> `LoadError(f"{csv.name}: no data rows")`. Missing t_src_ms/t_host_s/seq
  column -> LoadError. Import LoadError from `ground_station.analysis.flightlab.loaders`.
- Parse with pandas: read all cells as str (`dtype=str, keep_default_na=False`), then
  `pd.to_numeric(col, errors="coerce")` -> float64; empty/unparseable -> NaN. Drop rows whose t_src_ms is NaN.
- rate_hz = float(meta["preset"]["slots"][i]["rate"]). t0_src_ms = min t_src_ms over ALL slots.
- Per var column: Signal(name, t=(t_src_ms - t0_src_ms)/1000, v, rate_hz, slot=i), rows stable-sorted by
  t_src_ms (`np.argsort(..., kind="stable")`) so t is non-decreasing.
- A var present in several slots: keep the copy from the slot with the highest rate_hz (lowest index on tie).
- Also add one signal per slot named `f"__t_host_s.slot{i}"` with v = t_host_s (same t). Names starting
  with "__" are internal; data_quality excludes them from stuck/nan scans.
- SlotInfo per slot, computed in FILE ROW ORDER (before sorting), dt = np.diff(t_src_ms):
  index=i; rate_hz; n_rows; duration_s=(max-min t_src_ms)/1000; rate_measured_hz=(n_rows-1)/duration_s
  (0.0 if duration_s == 0); seq_drops = sum over consecutive rows of (d-1) where d=(seq[k]-seq[k-1]) % 256
  and d >= 1 (d == 0 adds 0); tsrc_gaps = count(dt > gap_factor*1000/rate_hz); drop_pct =
  100*seq_drops/(n_rows+seq_drops) (0.0 if both 0); dt_median_ms, dt_p99_ms (np.percentile 99), dt_max_ms
  over dt (0.0 when n_rows < 2); tsrc_backsteps = count(dt < 0); host_latency_std_ms =
  np.std(t_host_s*1000 - t_src_ms, ddof=0) over rows where both are finite; vars = var columns in file order.
- FlightLog(name=stem, source_format="vofa", source_paths=[str(meta_path)] + [str(csv) ...], meta=meta,
  t0_src_ms, duration_s=(max t_src_ms over all slots - t0_src_ms)/1000, signals, slots).

Contract B: `PKG/segments.py`
`segment(log: FlightLog, cfg: dict) -> dict` returning keys "armed", "airborne", "landing", "steady"
(each a list of (t0, t1) python-float tuples, sorted, non-overlapping) and "warnings" (list of str).
- Mask -> intervals on one signal's own samples: an interval starts at the t of the first True sample of a
  run and ends at the t of the first False sample after it; a run lasting to the last sample ends at
  t_last + 1/rate_hz. Write this as a module function `mask_intervals(t, mask, rate_hz)`.
- armed: `cfg["phase"]["arm_var"] == 1`. airborne: `cfg["phase"]["var"] == values["FLYING"]`.
  landing: phase == values["LANDING"]. Compare with np.isclose (values arrive as float).
- Phase var absent: airborne = armed, landing = [], warning "flight_phase absent: airborne = armed".
  Arm var absent: armed = [] plus warning "<arm_var> absent". Both absent: all four [] (two warnings).
- steady: each airborne interval trimmed to [t0 + takeoff_settle_s, t1 - pre_land_s) (cfg["segments"]).
  Then, for every loop in cfg["loops"] that has a `des_hold_tol` key and whose `<prefix>.Des` signal exists:
  a Des sample k is held when max-min of Des over the trailing window [t_k - hold_window_s, t_k] is
  < des_hold_tol; steady keeps only times where every such Des is held (held runs -> intervals with
  mask_intervals on that Des signal, then interval intersection). Drop intervals shorter than min_steady_s.
- hover_log ground truth: armed [(1.0, 25.0)], airborne [(2.0, 22.0)], landing [(22.0, 24.0)],
  steady [(5.0, 21.0)] (hover_log has no Des-hold loops). Compare ends with pytest.approx(abs=1e-6).

Contract C: `PKG/plugins/data_quality.py`, decorated `@register_plugin`, class attributes name =
"data_quality", order = 10. requires() returns []. run(log, segs, cfg) returns exactly the schema
`dataQuality` shape (plus optional "warnings"):
- slots: one dict per SlotInfo with every schema `slot` key; n_vars = len(slot.vars) (the list itself is
  not emitted).
- stuck_vars: sorted names (no "__" prefix) with >= params.data_quality.stuck_min_samples finite samples
  inside segs["airborne"] and np.ptp == 0 over them. Empty airborne -> [] and a warning.
- nan_vars: sorted names (no "__" prefix) whose NaN fraction over the whole log > nan_frac_max.
- worst_drop_pct: max slot drop_pct (None if no slots).
- clock_drift_ppm: slot 0's `__t_host_s.slot0` signal: slope of v vs t (np.polyfit deg 1, finite pairs),
  (slope - 1) * 1e6; None if the signal is absent or has < 2 finite samples.
- figures(log, segs, cfg, out_dir): matplotlib with `matplotlib.use("Agg")`; one file
  `out_dir/"dq_dt.png"` (dpi 120): per slot, np.diff(t)*1000 of that slot's first non-"__" signal vs t.
  Return [path]. Close the figure.
</context>

<task>
Based on the contracts above, implement contracts A, B and C and their tests.

ALLOW-LIST (create these; edit nothing else):
- ground_station/analysis/flightlab/loaders/vofa.py
- ground_station/analysis/flightlab/segments.py
- ground_station/analysis/flightlab/plugins/data_quality.py
- ground_station/analysis/flightlab/tests/test_vofa.py
- ground_station/analysis/flightlab/tests/test_segments.py
- ground_station/analysis/flightlab/tests/test_data_quality.py
- .agent-ops/out/wp1.md (digest)

Required tests (synthetic data only; use tmp_path, make_log, hover_log, cfg fixtures):
- test_vofa: 2-slot log written to tmp_path (100 Hz, 25 Hz): exact seq_drops across a 255->0 wrap
  (e.g. seq 250..255,0,3 -> 2 drops), tsrc_gaps, tsrc_backsteps, empty cell -> NaN, duplicate var keeps the
  faster slot, t0_src_ms = min over slots, `load()` dispatch works on the meta path; LoadError for a
  missing csv, a header-only csv, and a meta whose slots have no CSVs.
- test_segments: hover_log ground truth above; phase-absent fallback + warning; a Des step inside
  airborne splits steady; an interval shorter than min_steady_s is dropped.
- test_data_quality: run() on hover_log validates against `$defs.dataQuality` (jsonschema, after
  pipeline.jsonify); stuck var detected; nan var detected; clock drift of a crafted 100 ppm
  `__t_host_s.slot0` signal -> approx 100 (abs=1); figures() writes dq_dt.png.

Acceptance (run it; paste output + exit code in the digest):
`python -m pytest ground_station/analysis/flightlab/tests -q`
Expected: 0 failed; the 24 existing tests still pass.
Digest `.agent-ops/out/wp1.md` (<= 30 lines): STATUS, files, acceptance output tail, SUBSTITUTIONS, risks.
Constraints: no firmware/OBJ edits, no flash/probe, no network, foreground only, do not commit.
</task>
