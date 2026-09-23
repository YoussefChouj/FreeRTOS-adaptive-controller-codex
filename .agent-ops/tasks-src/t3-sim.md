# Task T3: the sim/replay harness (ground_station/research/sim/)

Read docs/research-platform/SPEC.md; it is binding. T2's ground_station/research package exists; use its Run/store.
Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081.

## Physical constants: do not invent numbers
The canonical constants live in the ORIGINAL project, read-only:
`/mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS---Six_Degrees_of_Freedom _Adaptive_controller/` (note the space in the name):
- `sim/plant.py`: CANONICAL_AIRFRAME, CANONICAL_MODELS (per-axis K/pole/delay), mixer, motor tau;
- `sim/reference_model.py`;
- `sim/adaptive_law.py`;
- `sim/baseline.py` (PID gains);
- `docs/sysid_results.md`.

Copy the needed values into `ground_station/research/sim/constants.py`, with a comment citing the source file:line for EVERY number. Do not copy the original's code wholesale; port what the harness needs.

## Build
1. `plant.py`: the identified rate-loop plant per axis, G(s)=K/(s(1+s/p))·e^(-sT), with yaw as a pure integrator. Discrete at a configurable rate (default 500 Hz).
2. `reference_model.py`: the per-axis reference models from the original project.
3. `baseline.py`: cascaded attitude→rate PID matching the original's baseline.
4. `replay.py`: replay recorded commands from a Run's captures through plant + PID, producing a predicted response (the PID-only reference, "xm_physics"). Also an adaptive-layer hook: a callable `u_ad(t, state)`, default zero.
5. `dryrun.py`: `dry_run(workflow_or_trajectory, params) -> Run` executes in sim and stores a Run of kind "validation", tagged `sim`, analyzed with T2's pipeline.
6. Tests:
   - the plant step response matches the analytic first-order-plus-integrator shape (known answer);
   - a closed-loop PID sim is stable for a ±20° roll step;
   - replaying a synthetic capture reproduces it within tolerance;
   - dry_run produces a Run with metrics.

## Acceptance
- The full tree is green (paste the last line of `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120`).
- Append a "## T3 as built" section (at most 15 lines) to the SPEC, and list any constant you could NOT find.
- Commit on your branch; the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
