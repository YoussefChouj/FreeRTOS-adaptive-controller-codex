- Use XML-style tags (`<guardrails>`, `<context>`, `<task>`) as delimiters. Phrase rules positively.
- Leave temperature at the default 1.0; lower values can cause looping.

```text
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

# Task wfb-plan: write the workflow-B build plan and wire contract

## Goal
`docs/workflow-b/build-plan.md` has a complete `## Tasks` section (12-18 self-contained tasks) and
`docs/workflow-b/interfaces.md` exists, so implementer agents can build "workflow B" (autonomous
PID/adaptive-controller tuning flight campaign on an STM32F407 + FreeRTOS quadrotor with a Python ground
station) one task at a time, each seeing ONLY its own task text plus the files it names.

## Context pointers (read in this order; open source files only to settle a specific doubt)
- `.agent-ops/grill-autonomous-flight-loop.md` - BINDING spec: Q1-Q12, shared-understanding summary,
  operator confirmation, additions A1-A4.
- `docs/workflow-b/facts-firmware.md`, `docs/workflow-b/facts-gs.md` - code facts with file:line.
- `docs/workflow-b/build-plan.md` - skeleton; keep the header and G1-G9 unchanged (add G10+ only if needed).
- `docs/firmware-table-pattern.md` - tunables style.

## Supervisor rulings to build on (they resolve gaps between spec and facts)
R1 Only CMD ids 0x1A-0x1D are free. Multiplex with a sub-index byte the way 0x0E does. Suggested split: one id
   for flight primitives (takeoff-to-hover, land-via-hover, GS heartbeat, set hover_z), one for trajectory
   upload (begin / chunk / commit+CRC / start / clear), rest reserved. Fix exact bytes, units, endianness,
   ack/telemetry feedback and error codes in interfaces.md.
R2 The RC transmitter must be on for any flight (live SBUS is already an FSM requirement; ch10 kill stays the
   top authority). The GS never replaces RC; it adds commands.
R3 GS "abort" today is a motor kill. The campaign soft stop is the new land CMD (fly to hover point, settle,
   descend, touchdown auto-disarm; timeout fallback = descend in place). Kill is for abort level 3 only.
R4 Takeoff: GS arm (existing 0x0E idx0) -> motor idle (idx1) -> settle -> new takeoff CMD that reuses the RC
   ch7 auto-climb code path with a shared hover_z parameter (firmware default = the existing ch7 value so RC
   behaviour is unchanged; the campaign writes 0.7 m at session start). RC ch5 land and GS land share one
   implementation including the return-to-hover-point leg.
R5 The missing safety net is added in firmware and belongs to the protected set: GS-heartbeat-loss auto-land
   (only while a GS-commanded flight is active), low-voltage backstop land, geofence/ceiling -> land, tilt
   crash-detect -> kill, PROTECTED BEGIN/END markers. All thresholds are PROPOSED constants in one *_ROW
   table. Spec values: fence +-1.1 x +-1.6 m, envelope +-0.8 x +-1.3 m, ceiling 1.5 m, 120 s airborne cap.
R6 Trajectory executor: buffer in CCM RAM (main SRAM headroom is only ~6.3 kB, derived), 20 bytes/point,
   chunked upload with checksum, bounds check against the envelope at commit, upload refused while a
   trajectory is executing, time interpolation, start-point check (PROPOSED 0.10 m from the hover point);
   trajectories start and end at the hover point. Use the position source the existing position-hold path
   uses; do not change estimator modes.
R7 Firmware style stays C89 per AGENTS.md even though the project flag is --C99. New logic in pure-C modules
   (no FreeRTOS/HAL includes) with gcc host tests; RTOS files only call them.
R8 The ground station computes J exactly as sim/bench/bench.py does, by importing/sharing that code, never
   copying it. One descriptor YAML per controller; the tuner is controller-agnostic and modular. flightlab
   (workflow A) recommendations are evidence only.
R9 flightlab compare.py / ledger.py / report are another session's uncommitted work: B calls flightlab through
   a thin adapter over its CLI/JSON contract and tests the adapter with fakes. Plan no edits to flightlab.
R10 Telemetry (A3): a locked "campaign" capture preset with every needed and optional variable (shadow-mode
   controller outputs, adaptive weights/features); the rate is found by a loss-free probe at session start,
   not hard-coded (the rate docs conflict); a per-session self-describing manifest keeps datasets with
   different columns combinable.
R11 Path pipeline: reuse path_library/resample/smooth and the path JSON schema already in the tree;
   /api/paths is GET-only and nothing uploads arbitrary waypoints yet, so B adds the uploader and the
   dashboard Execute for uploaded paths.
R12 Go surface (A4): a /flight-campaign skill (tracked source under docs/skills/, because .claude/skills is
   gitignored; the supervisor installs it) clarifies objectives, writes + validates the campaign YAML and
   opens the campaign; per-battery go (pack ID + checklist) on a dashboard Campaign panel and via MCP; the
   panel always shows Pause / Land / Abort. Agent arming is possible only inside an operator-opened battery
   session with allow_agent_arm on; that gate is tier-0 and must be explicit and tested.
R13 Architecture: a deterministic Python campaign runner (gates -> arm -> idle -> takeoff -> maneuver ->
   return -> land -> analyse -> score -> tune -> repeat; stop at SoC 30 %) with a decision hook; an LLM
   session decides between flights, otherwise a deterministic tuner. The runner is tested end to end against
   a fake-drone test double. Includes SoC model + pack registry, abort monitor (three levels), code-change
   gate (Q10c nine steps: protected check, justification, Keil build + map RAM, SIL of the real C file,
   last-known-good custody, one change per flight, hover-first, auto-revert, hash logged) and the ADB phone
   recorder (camera feeds A only; adb is installed by the operator, so code degrades cleanly without adb).
R14 No worker flies, arms, spins motors, flashes or POSTs to the live service. Every task is verifiable
   without hardware (Keil builds are run by the supervisor; FW tasks verify with gcc host tests).

## Plan format (a script extracts tasks by heading - follow exactly)
- Each task starts with a heading line `### Task N: <title>` (N = 1,2,3...). Everything up to the next such
  heading is ALL the implementer sees, so each task is self-contained: goal, lane, exact files to
  create/modify (paths), interfaces consumed (interfaces.md section numbers) and produced (exact function
  signatures / CLI / JSON keys), behaviour rules, tests to write with concrete cases, verification commands,
  protected regions it may touch (if any), and an explicit "Out of scope" line.
- Size: one agent, one sitting (roughly <= 600 lines of new code + tests, <= 8 files). Split bigger pieces.
  Order so every task depends only on earlier tasks in its lane or on interfaces.md.
- Two lanes with disjoint file sets: FW lane (firmware C under API/ TASK/ BSP/ USER/ plus host tests) and GS
  lane (Python/JS/docs). Number FW tasks first. In `## Lanes` list the task numbers per lane. GS tasks code
  against interfaces.md and fakes, never against unmerged FW code.
- Last task: end-to-end dry run against the fake drone plus `docs/workflow-b/RUNBOOK.md` listing operator
  actions (install adb, bench-check yaw near 0 after boot, clamp phone, mark end wall, label packs, review
  crash-detect thresholds, enable allow_agent_arm).
- Never state a number as measured; mark thresholds PROPOSED. Quote file:line from the facts files where a
  task hooks into existing code. Plain ASCII, lines <= 110 chars, no filler prose.
- End build-plan.md with `## Coverage`: a table mapping every spec decision Q1-Q12 and A1-A4 to task numbers.

## Constraints
- Safety tier: Tier 2 (docs only). Do not touch any file except the two deliverables and the digest.
- Hardware: none. No builds, no flashing, no port 8081. Foreground commands only; do not commit.

## Deliverables
1. `docs/workflow-b/interfaces.md` (new).
2. `docs/workflow-b/build-plan.md` with `## Lanes`, `## Tasks`, `## Coverage` filled in.
3. `.agent-ops/out/wfb-plan.md` (digest <= 30 lines: STATUS, one line per task `N | lane | title | files`,
   then up to 5 lines on anything in the spec/facts you could not resolve and how you ruled).

## Verification (run and paste output in the digest)
- `grep -c "^### Task [0-9]*:" docs/workflow-b/build-plan.md` -> 12..18
- `grep -n "^## " docs/workflow-b/build-plan.md docs/workflow-b/interfaces.md`
- `awk "length > 110" docs/workflow-b/build-plan.md docs/workflow-b/interfaces.md | wc -l` -> 0
- Self-check and state the result: (a) every Q1-Q12/A1-A4 maps to a task; (b) no file appears in tasks of
  both lanes; (c) every consumed interface is produced earlier or in interfaces.md; (d) each task tests
  match its stated behaviour.

## Out of scope
- Writing any implementation code; editing the spec or the facts files.

Based on the contracts and rulings above, write the two deliverables and the digest now.
