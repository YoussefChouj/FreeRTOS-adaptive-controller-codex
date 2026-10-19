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
This is "task G7" of workflow B (autonomous tuning flights): controller descriptors and the tuner. A
descriptor YAML lists the tunable knobs of one controller (PID or MRAC) and how each is written to the drone
(command id + index). The tuner proposes the next knob values from past (params, J, valid) results. The tuner
must be controller-agnostic: it only sees descriptor knobs, never a controller name (spec Q11).
Python 3.10+, stdlib + numpy + yaml only. Type hints on every public function. No global mutable state.

Read first (read-only): spec `.agent-ops/grill-autonomous-flight-loop.md` lines 180-240 (Q10 gain envelope,
Q11); `docs/workflow-b/interfaces.md` section 4 rows controller_descriptor / tuner; `docs/workflow-b/facts-gs.md`
sections 2 and 5; `sim/bench/bench.py` lines 15-100 (PARAMS format, `to_x`/`from_x` normalisation, POP,
SIGMA0); `ground_station/platform/firmware_contract.py` lines 175-501 (CommandSpec / CommandParam: command
ids, idx meaning, lo/hi); `API/pid.c` and `API/mrac*.h|c` for the firmware default of each knob.
</context>

<task>
ALLOW-LIST (create only these files):
- ground_station/analysis/controller_descriptor.py
- ground_station/analysis/tuner.py
- ground_station/analysis/controllers/pid.yaml
- ground_station/analysis/controllers/mrac.yaml
- ground_station/analysis/tests/test_controller_descriptor.py
- ground_station/analysis/tests/test_tuner.py

controller_descriptor.py (exact names):
```python
@dataclass(frozen=True)
class Knob: symbol: str; cmd_id: int; idx: int; default: float; lo: float; hi: float; scale: str  # 'log'|'lin'
@dataclass(frozen=True)
class Descriptor: name: str; knobs: tuple[Knob, ...]; shadow_outputs: tuple[str, ...]
class DescriptorError(ValueError): problems: list[str]
def load(path: str | os.PathLike) -> Descriptor: ...
```
Checks, all problems listed in one DescriptorError: required keys, unknown keys, types (bool is not int),
finite numbers, lo < hi, lo <= default <= hi, scale in {log, lin}, log needs lo > 0, cmd_id and idx in
0..255, unique symbols, unique (cmd_id, idx) pairs, 1..14 knobs (bench limit), cmd_id/idx must match a
CommandSpec/CommandParam in firmware_contract.py (import it) and lo/hi inside that param's lo/hi.

YAML files: 4-10 knobs each. pid.yaml: the gains a flight campaign would tune first (position, velocity,
attitude, rate loops; include `locxPID.Kp`/`locyPID.Kp`, `locxsPID.Kp`/`locysPID.Kp`, roll/pitch rate Kd).
mrac.yaml: the MRAC adaptation gains / output scale that firmware_contract exposes; `shadow_outputs` lists
the streamable shadow symbols (e.g. `mrac_state.roll.u_ad`, per facts-gs.md section 1). Per knob: default =
the firmware value (one comment per knob citing file:line of the default and of the CommandParam); lo/hi =
default x 0.5 and x 2 clipped to the CommandParam range; scale log for gains > 0. If firmware_contract has
no command for a knob you wanted, leave the knob out and list it in the digest. Never invent a command id.

tuner.py (exact names):
```python
class Tuner:
    def __init__(self, descriptor: Descriptor, seed: int) -> None: ...
    def propose(self, history: Sequence[Mapping]) -> dict[str, float]: ...
    def record(self, params: Mapping[str, float], J: float, valid: bool) -> None: ...
```
- `history`: prior results from earlier campaigns, items `{"params": {...}, "J": float, "valid": bool}`;
  merged with the tuner's own `record`ed results (pass [] when none).
- Search in the normalised space of bench.py (log/lin like `to_x`/`from_x`; reimplement those 2 small
  functions for Knob and cite bench.py lines; do not import bench here).
- No results yet -> propose the defaults. Otherwise a seeded (1+1)-style evolution strategy: mutate the best
  valid result with Gaussian steps (sigma from bench SIGMA0) in normalised space; 1/5 success rule to adapt
  sigma; clip to [lo, hi]. Invalid results or non-finite J never become the incumbent. If no valid result
  exists, restart from the defaults with a smaller sigma.
- Deterministic: same descriptor, seed, history and record calls -> same proposals (numpy
  `default_rng(seed)`). Keys of the returned dict = knob symbols in descriptor order.
- The per-flight change limit (Q10 max_step) belongs to the campaign runner, not here.

Tests (pytest): descriptor: both shipped YAMLs load; one test per check (parametrize), a file with 3
mistakes lists 3 problems; frozen dataclasses. Tuner: first proposal == defaults; proposals always inside
[lo, hi] over 200 steps on a synthetic quadratic J; on that quadratic the best J after 60 recorded steps is
below the J of the defaults; invalid and NaN-J results never become the best; determinism (two tuners, same
seed -> identical 30-step sequences; different seed -> different); tuner source contains no controller name
(e.g. assert "pid" and "mrac" not in its non-comment code is too fragile: instead build a Descriptor with an
arbitrary name like "zz" and check it works).

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/analysis/tests/test_controller_descriptor.py ground_station/analysis/tests/test_tuner.py -q

Digest: write .agent-ops/out/wfb-g7.md with: the files created, a table of every knob (symbol, cmd, idx,
default, lo, hi, citations), the acceptance command and its output verbatim, every ambiguity you resolved,
a line "SUBSTITUTIONS: none" (or what you substituted), and "NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
