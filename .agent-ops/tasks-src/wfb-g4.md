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
This is "task G4" of workflow B (autonomous tuning flights): the trajectory uploader. It sends a list of
trajectory points to the drone over CMD 0x1B (BEGIN, APPEND one float at a time, CRC_HI + COMMIT) through the
existing typed client `WfbClient`, and retries from BEGIN when a step is rejected. Pure logic: no sockets, no
threads, no clock, no sleeps. Python 3.10+, standard library only (dataclasses, math, typing). Type hints on
every public function. No global mutable state.

Read first (read-only): `docs/workflow-b/interfaces.md` section 1 (CMD 0x1B table, COMMIT checks, "Upload
integrity") and section 4; `ground_station/platform/wfb_commands.py` (WfbClient, encode, index enums);
`ground_station/service/trajectory_pipeline.py` (TrajPoint, crc32, any MAX_POINTS constant);
`ground_station/service/fake_drone.py` (FakeDrone: send, status, default hover_z, COMMIT check order).
</context>

<task>
ALLOW-LIST (create only these two files):
- ground_station/platform/trajectory_upload.py
- ground_station/platform/tests/test_trajectory_upload.py

Interface (exact):
```python
@dataclass(frozen=True)
class UploadResult:
    ok: bool
    attempts: int          # attempts made (0 when a pre-check refused before sending anything)
    error: str | None      # None when ok, else the last failure text (see below)

def upload(points: Sequence[TrajPoint], client: WfbClient, attempts: int = 3) -> UploadResult: ...
```

Behaviour:
1. attempts < 1 raises ValueError (programming error).
2. Pre-checks, nothing sent, return ok False, attempts 0: fewer than 2 or more than the firmware maximum
   (600; import the constant from trajectory_pipeline if it defines one, else define
   `TRAJ_MAX_POINTS = 600` with a comment citing interfaces.md `WFB_TRAJ_MAX_POINTS`) -> error "count";
   any non-finite x, y, z, yaw_deg or t -> error "nonfinite".
3. The CRC is computed once with `trajectory_pipeline.crc32(points)`. Never reimplement CRC or float packing.
4. One attempt = `client.traj_begin(N)`, then `client.traj_append(v)` for each point in the order
   x, y, z, yaw_deg, t (5N calls), then `client.traj_commit(crc)` (it sends CRC_HI then COMMIT itself).
5. The first False ends the attempt at once (no further frames in that attempt). The next attempt starts
   again with traj_begin (BEGIN clears the buffer). Error texts: "begin rejected",
   "append rejected at float <k>" (k = 0-based float index), "commit rejected".
6. After the last failed attempt return ok False, attempts = attempts made, error = the last error.
7. Do not call `validate` (ruling: the runner validates before upload; the firmware COMMIT checks again).
8. Exceptions from the client or transport propagate; do not catch them.

Tests (pytest; transport = FakeDrone, `client = WfbClient(drone.send)`, or a small wrapper around
`drone.send` that counts, decodes, rejects or corrupts frames). Decode frames with a decoder from
wfb_commands if one exists, else by the frame layout in interfaces.md section 1
(`CC DF 01 flags txid_LE(2) cmd idx 04 00 float32_LE xor`: cmd at byte 6, idx at byte 7, value bytes
10..13). To corrupt a value re-encode with `wfb_commands.encode(cmd, idx, value, txid)`. Build points the
FakeDrone accepts (start and end at its hover point, t[0] = 0 and increasing, speeds under the limit);
`trajectory_pipeline.generate` may be used.
1. Happy path: ok True, attempts 1, error None; FakeDrone status shows trajectory state READY (key names
   from fake_drone.py); APPEND frames sent == 5N.
2. First attempt corrupted (one APPEND value changed on attempt 1 only): COMMIT rejected, attempt 2 ok;
   result ok True, attempts 2.
3. Always corrupted: ok False, attempts 3, error "commit rejected", exactly 3 BEGIN frames sent.
4. attempts=1 with corruption: ok False, attempts 1.
5. Wrapper returns REJECTED for APPEND float 10 on attempt 1: no APPEND after it in attempt 1; attempt 2 ok.
6. BEGIN rejected on every attempt: error "begin rejected", no APPEND frame sent at all.
7. Pre-checks: 1 point and 601 points -> "count"; NaN in each of the five fields -> "nonfinite"
   (parametrize); zero frames sent. attempts=0 raises ValueError.
8. A point with x = 0.1 (not exact in float32) still commits (CRC over float32 values).
9. UploadResult is frozen (assigning a field raises dataclasses.FrozenInstanceError).

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/platform/tests/test_trajectory_upload.py ground_station/platform/tests/test_wfb_commands.py ground_station/service/tests/test_fake_drone.py -q

Digest: write .agent-ops/out/wfb-g4.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
