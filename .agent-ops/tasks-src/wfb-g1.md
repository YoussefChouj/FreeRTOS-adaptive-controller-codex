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
This is "task G1" of workflow B (autonomous tuning flights): the battery model that gates each flight.
Read first: docs/workflow-b/interfaces.md section 4 (the `analysis/battery_model.py` row);
ground_station/analysis/tests/test_runs.py (test style reference: plain pytest functions, tmp_path).
The drone flies 4S LiPo packs. Between flights the operator reads the resting pack voltage; in flight the
ground station sees a filtered loaded voltage. Every number below marked PROPOSED is a placeholder the
operator recalibrates from flights: copy it exactly and keep the PROPOSED marker as a YAML comment.
Python 3.10+, standard library plus PyYAML only. Type hints on every public function. No I/O outside
`PackRegistry.load`. No global mutable state.
</context>

<task>
Implement the battery model module and its pack registry file.

ALLOW-LIST (create only these three files, nothing else anywhere):
- ground_station/analysis/battery_model.py
- ground_station/analysis/packs.yaml
- ground_station/analysis/tests/test_battery_model.py

packs.yaml content (exact keys and values):
- gate_soc_pct: 30.0            (spec value, not PROPOSED)
- min_rest_s: 60.0              # PROPOSED
- sag_v_per_cell: 3.55          # PROPOSED
- default_flight_drop_pct: 10.0 # PROPOSED
- ocv_table: list of [volts_per_cell, soc_pct] pairs, descending voltage  # PROPOSED generic LiPo curve
  [4.20,100] [4.15,95] [4.11,90] [4.08,85] [4.02,80] [3.98,75] [3.95,70] [3.91,65] [3.87,60] [3.85,55]
  [3.84,50] [3.82,45] [3.80,40] [3.79,35] [3.77,30] [3.75,25] [3.73,20] [3.71,15] [3.69,10] [3.61,5] [3.27,0]
- packs: three entries {id, label, cells, capacity_mah}:
  {P5300-1, "5300 mAh #1", 4, 5300}, {P4000-1, "4000 mAh #1", 4, 4000}, {P4000-2, "4000 mAh #2", 4, 4000}
  A pack entry may carry its own optional `ocv_table`, which replaces the shared one for that pack.

Interface (names and signatures exact):
- class PackError(ValueError)
- class PackRegistry:
  - classmethod load(path: str | Path | None = None) -> PackRegistry
      None means the packs.yaml next to the module. Validation collects every problem and raises one
      PackError whose message lists them all, one per line. Problems to detect: missing or non-numeric
      top-level key; gate_soc_pct outside 0..100; a table with fewer than 2 rows; table voltages not
      strictly descending; table soc values not non-increasing or outside 0..100; duplicate pack id;
      pack with cells < 1 or capacity_mah <= 0; empty pack list; unreadable file or invalid YAML.
  - pack_ids() -> list[str]   (file order)
  - predict_soc(pack_id: str, resting_v: float) -> float
      Pack voltage / cells -> volts per cell -> linear interpolation in that pack's table, clamped to
      0..100. Unknown pack_id raises PackError. Non-finite resting_v raises PackError.
  - record_flight(pack_id: str, soc_before: float, soc_after: float) -> None
      Stores one observed SoC drop (soc_before - soc_after) for the pack, in memory. A drop that is
      non-finite or <= 0 raises PackError and stores nothing.
  - expected_drop(pack_id: str) -> float
      Mean of the recorded drops for the pack; default_flight_drop_pct when none are recorded.
  - next_flight_allowed(pack_id: str, resting_v: float, cooldown_s: float) -> tuple[bool, str]
      Allowed when cooldown_s >= min_rest_s AND predict_soc(...) - expected_drop(...) >= gate_soc_pct
      (exactly at the gate is allowed). The reason string is "ok" when allowed; otherwise it starts with
      "REST:" or "SOC:" and states the measured and the required value. When both fail, report REST.
      Unknown pack or non-finite input returns (False, "INPUT: ...") instead of raising.
  - sag_critical(pack_id: str, loaded_v_filtered: float) -> bool
      True when loaded_v_filtered / cells < sag_v_per_cell. True for a non-finite voltage and for an
      unknown pack (fail safe).

Required tests (plain pytest functions; build temporary YAML files with tmp_path for the bad cases):
1. The shipped packs.yaml loads; pack_ids() == ["P5300-1", "P4000-1", "P4000-2"].
2. predict_soc: 16.8 V -> 100; 13.08 V -> 0; 15.36 V (3.84 V/cell) -> 50; a voltage halfway between two
   table rows gives the halfway SoC (pytest.approx, abs=1e-6); above 16.8 V clamps to 100; below clamps to 0.
3. predict_soc raises PackError for an unknown pack, NaN and infinity.
4. A per-pack ocv_table replaces the shared one for that pack only.
5. load: one temporary file with three different problems raises one PackError naming all three; each
   single problem listed above also raises.
6. expected_drop: default before any record; mean after two records; record_flight rejects a zero, a
   negative and a NaN drop and leaves the mean unchanged.
7. next_flight_allowed: allowed case returns (True, "ok"); short rest -> reason starts "REST:"; low SoC ->
   "SOC:"; both -> "REST:"; exactly at the gate -> allowed; a learned larger drop turns an allowed case
   into "SOC:"; unknown pack and NaN voltage -> (False, reason starting "INPUT:").
8. sag_critical: False just above 4 x 3.55 V, True just below, True for NaN, True for an unknown pack.

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/analysis/tests/test_battery_model.py -q

Digest: write .agent-ops/out/wfb-g1.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
