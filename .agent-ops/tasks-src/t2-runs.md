# Task T2: the Run record, the index and deterministic analysis (new package ground_station/research/)

Read docs/research-platform/SPEC.md first; it is binding.
Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081. Python only. Match the repo style.

## Find first (report in your result)
Where recorded data already lives and in what format:
- ground_station/livewatch/stream_log (CSV);
- capture_preset outputs;
- the service's session recordings (grep `list_sessions`, `recording`, `session_id` in ground_station/service).

The Run importer must ingest these existing formats.

## Build
1. `ground_station/research/run.py`: a `Run` dataclass per SPEC.
   - Fields: id (ULID or timestamp-based), kind (experiment|debug|validation|flight), intent, hypothesis, phase, firmware_hash, git_commit, params (dict snapshot), variant, trajectory, captures (list of paths), events (list of {t, kind, detail}, including Simplex trips), notes (list of {t, author, text}), outcome, metrics (dict), tags, created_at.
   - JSON round-trip.
2. `ground_station/research/store.py`: the data directory.
   - Location: `UAV_RUNS_DIR` env, default `D:/uav-runs` when D: exists, else `~/uav-runs`.
   - Layout: `<dir>/runs/<id>/run.json`, captures converted to Parquet (pyarrow if available, else CSV fallback, recorded in the run), and `<dir>/index.sqlite` with one row per run plus a metrics table (run_id, name, value).
   - API: `create`, `get`, `update`, `query(sql_where, params)`, `import_capture(path, kind, **meta)`.
3. `ground_station/research/analysis.py`: a deterministic core-metrics pipeline.
   - Per axis: RMSE of tracking error, overshoot, settling time, saturation time, and dominant spectrum peaks.
   - Plus a plugin registry (`@metric("name")`).
   - Thesis plugin stubs: `u_ad_spike_ratio` (max |u_ad| / median |u_ad|), `w_norm_convergence` (slope of ‖Ŵ‖ over the last 30% of the run), and `gate_saturation` (fraction of samples outside [0,1] or with sum ≠ 1).
   - Each plugin skips cleanly (returns None) when its columns are missing.
   - Writes `report.md` into the run directory and metrics into the index.
4. CLI: `python -m ground_station.research {import,list,show,analyze,query}`.
5. Tests in ground_station/research/tests/ using synthetic signals with known answers:
   - a step with known overshoot;
   - a sine with a known RMSE;
   - query filtering;
   - JSON round-trip;
   - importing one small synthetic CSV in each existing format.

   Use tmp_path; never touch D:.

## Acceptance
- The full tree is green: `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120` (paste the last line).
- Update docs/research-platform/SPEC.md only by appending a "## T2 as built" section (at most 15 lines).
- Commit on your branch; the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
