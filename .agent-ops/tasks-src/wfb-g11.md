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
This is "task G11" of workflow B (autonomous tuning flights): the campaign file schema. The operator (through
a Claude skill) writes one YAML file per campaign; the campaign runner loads it with this module before any
flight. The loader must list EVERY problem in one error, so the operator fixes the file in one pass.
Pure logic: file read + validation, no network, no clock. Python 3.10+, stdlib + `yaml` (PyYAML) only.
Type hints on every public function. No global mutable state.

Read first (read-only): spec `.agent-ops/grill-autonomous-flight-loop.md` lines 140-300 (Q9, Q10 gain
envelope, Q12 aborts, summary items 4-6, A1-A4); `docs/workflow-b/interfaces.md` section 1 constants table
(WFB_HOVER_Z_MIN/MAX, WFB_TRAJ_V_MAX) and section 4; `ground_station/service/trajectory_pipeline.py`
(SHAPES dict, Profile fields); `ground_station/service/abort_monitor.py` if present (AbortLimits fields);
`ground_station/analysis/battery_model.py` (pack id format).
</context>

<task>
ALLOW-LIST (create only these files):
- ground_station/service/campaign_schema.py
- ground_station/service/campaigns/example_circle.yaml
- ground_station/service/tests/test_campaign_schema.py

Interface (exact names):
```python
class CampaignError(ValueError):
    problems: list[str]           # every problem, each "<dotted.path>: <what is wrong>"
def load_campaign(path: str | os.PathLike) -> Campaign: ...
def parse_campaign(data: dict) -> Campaign: ...   # same checks, for data already loaded (tests, MCP)
```
`Campaign` and its parts are frozen dataclasses; lists become tuples.

Starting schema (reconcile with the spec; any field you add, drop or rename goes in the digest with the spec
line that justifies it):
```yaml
campaign: circle_pid_01          # [a-z0-9_-]{1,40}
objective: "reduce circle tracking error"   # non-empty string
controller: pid                  # [a-z0-9_]+ ; names ground_station/analysis/controllers/<name>.yaml (existence NOT checked here)
packs: [P1, P2]                  # non-empty, unique pack ids (existence checked by the runner, not here)
max_flights: 12                  # int >= 1
envelope:                        # Q10 per-campaign gain envelope, symbol -> limits; may be empty
  locxPID.Kp: {min: 0.3, max: 1.2, max_step: 0.1}   # finite, min < max, 0 < max_step <= max - min
experiments:                     # ordered queue, >= 1 item, names unique
  - name: circle_r05
    shape: circle                # key of trajectory_pipeline.SHAPES
    params: {r: 0.5}             # passed to the shape; not checked here beyond "mapping"
    profile: {v_cruise_mps: 0.3, a_max_mps2: 0.5, ds_m: 0.05, hover_z_m: 0.8, yaw_deg: 0}
    capture: campaign            # capture preset name, non-empty
    repeats: 2                   # int >= 1
abort: {}                        # optional overrides of AbortLimits fields (unknown field = problem)
```
Checks (each failure adds one problem and validation continues): missing required key; unknown key at any
level; wrong type (bool is not an int; ints accepted where floats are expected); every numeric finite;
v_cruise_mps in (0, WFB_TRAJ_V_MAX]; a_max_mps2 > 0; ds_m > 0; hover_z_m in [WFB_HOVER_Z_MIN,
WFB_HOVER_Z_MAX]; yaw_deg in [-180, 180]; shape in SHAPES; envelope rules above; duplicate experiment names
or pack ids. Take numeric limits from existing Python constants if trajectory_pipeline or fake_drone defines
them (import, do not copy); else define them once at module top with a comment citing interfaces.md.
A YAML syntax error or a top level that is not a mapping raises CampaignError with one problem.

Example file: a valid 2-experiment circle + figure8 campaign for controller pid with 2 packs; comment each
field in one line.

Tests (pytest, tmp_path for files):
1. The example file loads; field values round-trip; lists are tuples; dataclasses frozen.
2. One test per check above (parametrize), asserting the dotted path appears in `problems`.
3. A file with 4 different mistakes lists exactly those 4 problems.
4. Unknown key at top level, inside profile and inside an envelope row: each reported.
5. `True` for repeats and `"1"` for max_flights are type errors; `1` for a float field is accepted.
6. Non-mapping top level and invalid YAML give CampaignError with one problem.

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/service/tests/test_campaign_schema.py -q

Digest: write .agent-ops/out/wfb-g11.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved and every schema deviation with its spec line, a line
"SUBSTITUTIONS: none" (or what you substituted), and "NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
