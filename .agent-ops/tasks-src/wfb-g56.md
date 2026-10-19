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
Repository: STM32F407 flight-controller firmware plus a Python ground station (package `ground_station`).
This is "tasks G5 + G6" of workflow B (autonomous tuning flights), done together because G6 reads what G5
writes. G5: the locked telemetry capture set for campaign flights, a loss-free rate probe, and a session
manifest. G6: turn one recorded flight session into bench-style rows and score them with the bench objective J.
Python 3.10+, stdlib + numpy + yaml only. Type hints on every public function. No global mutable state.
No network, no serial port, no clock reads in the tested logic: anything that talks to the drone is an
injected callable, so tests run offline.

Read first (read-only): `docs/workflow-b/interfaces.md` section 4 rows `livewatch/campaign_capture.py` and
`analysis/workflow_b_adapter.py`; `docs/workflow-b/build-plan.md` lines 152-178 and the amendments table rows
G5/G6 (~line 305); `docs/workflow-b/facts-gs.md` sections 1, 5, 6; spec
`.agent-ops/grill-autonomous-flight-loop.md` A3 (~line 295) and Q7/Q11; `ground_station/livewatch/`
(manifests.yaml, multi_slot_presets.yaml, manifest.py MultiSlotPresetManager, stream.py `loss_pct`,
stream_log.py CSV format, capture_preset.py); `sim/bench/bench.py` lines 15-80 (metrics row keys,
SAT_HI/SAT_LO/SAT_BUDGET, objective).
</context>

<task>
ALLOW-LIST (create only these files; do NOT edit any existing file):
- ground_station/livewatch/campaign_capture.py
- ground_station/livewatch/tests/test_campaign_capture.py
- ground_station/analysis/workflow_b_adapter.py
- ground_station/analysis/tests/test_workflow_b_adapter.py
- ground_station/analysis/tests/fixtures/wfb_session/   (small synthetic session: manifest.json + CSVs, < 200 KB total)

G5 campaign_capture.py:
- `CAMPAIGN_SET: dict[str, tuple[str, ...]]` with keys "needed" and "optional": dotted firmware symbols as
  used in manifests.yaml. needed = what G6 and the abort monitor need (position, position reference,
  attitude, rate error or rate + rate setpoint, motor PWM outputs, battery voltage, arm/airborne status,
  safety trip if streamed). optional = MRAC shadow outputs and adaptive weights/features (amendment G5:
  `mrac_state.<axis>.u_ad`, `u_nom`, Theta/Whatf etc. per facts-gs section 1). Every symbol must exist in
  `ground_station/livewatch/symbol_catalog.json` (test it); list any wanted symbol that does not exist in the
  digest instead of inventing it.
- `slots_for(rate_hz: float) -> list[dict]`: split CAMPAIGN_SET into at most 4 slots within the firmware
  limits (62 ranges per slot, 508 floats per frame) and the link budget in facts-gs section 1 (frame cost
  `(12 + payload_bytes) * send_hz / divider` B/s; use 87552 B/s x 0.8 as the planning budget, one named
  constant citing log_frames.md). needed vars first; drop optional vars (never needed ones) when over budget,
  and return which were dropped.
- `probe_max_rate(stream: Callable[[float, float], float], rates=(200, 100, 50, 25), secs: float = 3.0) -> float`:
  `stream(rate_hz, secs)` records and returns loss percent; return the highest rate with loss == 0.0; raise
  `CaptureError` when none is loss-free. The live implementation of `stream` is NOT part of this task.
- `write_manifest(session_dir, *, pack_id: str, rate_hz: float, slots: list[dict], dropped: list[str],
  segments: list[dict] = ()) -> Path`: writes `manifest.json` = {"schema": "wfb_session_v1", "pack_id",
  "rate_hz", "slots": [{"slot", "hz", "csv", "vars"}], "dropped", "segments": [{"name", "t0_host_s",
  "t1_host_s"}]}; CSV files are stream_log format (`t_src_ms,t_host_s,seq` + one column per symbol, arrays
  expanded `name[i]`). Also `read_manifest(session_dir) -> dict` validating the schema.

G6 workflow_b_adapter.py (interfaces.md row is binding):
- `flight_rows(session_dir) -> list[dict]`: one row per manifest segment (a flown trajectory). Keys: `traj`
  (segment name), `rmse` (3-D position tracking RMS error in m over the segment), `rmse_xy`, `rmse_z`, `sat`
  (fraction of motor samples >= SAT_HI or <= SAT_LO, the bench definition; import SAT_HI/SAT_LO from bench, do
  not copy), `n` (samples), `diverged` (bool; True when the segment has < 10 samples or non-finite position,
  then `rmse` = inf as in bench). Use flightlab loaders if one reads stream_log CSVs; else read with csv/numpy.
  Align slots on `t_host_s` (nearest, no interpolation across gaps > 0.1 s).
- `score(rows) -> float` = `bench.objective(rows)` by import (`sys.path` insert of the repo's `sim/bench`,
  resolved from this file's location, not the cwd). Never copy the formula. Empty rows -> ValueError.
- Controller-agnostic: no controller names in this module.

Tests: G5: every CAMPAIGN_SET symbol is in symbol_catalog.json; needed and optional disjoint; slots_for
respects 4 slots / 62 ranges / budget and drops optional before needed (use a tiny budget); probe_max_rate
with a fake stream (loss at 200 and 100 -> 50; all lossy -> CaptureError; rates tried high to low and stops
at the first loss-free); write_manifest + read_manifest round-trip; bad schema rejected. G6 (fixture session
made by a small script inside the test or committed under fixtures/): known offsets give the exact rmse and
sat by hand calculation; a 2-segment session gives 2 rows; diverged segment -> rmse inf; score(rows) equals
`bench.objective(rows)` and a hand-computed J for a 3-row example; empty -> ValueError; running from another
cwd still imports bench.

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/livewatch/tests/test_campaign_capture.py ground_station/analysis/tests/test_workflow_b_adapter.py -q

Digest: write .agent-ops/out/wfb-g56.md with: the files created, CAMPAIGN_SET with the source of each symbol,
the slot plan at 50 Hz and 100 Hz (bytes/s each), the acceptance command and its output verbatim, every
ambiguity you resolved, "SUBSTITUTIONS: none" (or what), and "NOT RUN:" lines (the live stream probe is
NOT RUN by design). Foreground commands only. Do not commit. Leave no caches or other files in the repo.
</task>
