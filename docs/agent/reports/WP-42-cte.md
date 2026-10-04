# WP-42 report (CEO inline, 2026-10-04): fault-injection SIL, self-describing sessions, flight review

Status: DONE. Commits: 555b1a7 (P2), 5ede4af (P3), 15fd8a3 (P5), plus the test fix below. The CEO did the work inline; no worker ran.

## P2 SIL fault injection (`sim/sil/faults.py`, `sim/sil/test_faults.py`, wired into the check.sh sil-smoke)
The real API/wfb_glue.c runs in the SIL loop with the firmware controller and the plant, one fault per run, 3 s into hover.
Measured with `python -m sim.sil.faults` (5 runs, 17.7 s); times are seconds from the fault:

| fault | injected | trip | action |
|---|---|---|---|
| link_loss | GS heartbeats stop | HEARTBEAT +0.990 | land_req +2.165 |
| low_v | vbat 16.0 -> 13.5 V | LOW_V +2.995 | land_req +4.115 |
| tilt | motor 1 thrust x0 | TILT +0.630 | motor_stop +0.630 (true tilt passed 60 deg at +0.410) |
| fence_push | x estimate +1.75 m | none (push +0.000) | pushed back inside, push cleared |
| fence_over | x estimate +2.00 m | FENCE +0.000 | land_req +0.000 |

Finding (no firmware change made): TILT KILL fires 0.22 s after the true attitude passes 60 deg, so it stops a vehicle
that is already lost. PROPOSED for operator review: tilt_deg 45 or a shorter tilt_hold_s. Both are *_ROW edits that need flight evidence first.
The SIL does not port LANDING or DANGEROUS_STOP (StabilizerTask.c, flight_fsm.c). A run ends at the request, and nothing after it is simulated.

## P3 self-describing session (`ground_station/service/session_schema.py`, storage.py, core.py)
manifest.json gains a `session_schema` block (gs-session v2) holding:
- the git commit and dirty flag, plus the firmware ELF (path, size, sha256);
- the firmware_contract versions;
- every *_ROW tunable cell;
- one entry per subscribed variable (slot, type, unit, rate, divider, address).

Parameter commands were already in events.jsonl; `session_schema.read()` returns the schema, the params and the events.
Format: ULog-like on the existing CSV + manifest layout rather than MCAP. MCAP would bring an uninstalled dependency and a
binary container, while every current loader reads the CSV. The block is additive, so an MCAP exporter can come later.

## P5 flight review (`ground_station/analysis/flight_review.py`)
`python -m ground_station.analysis.flight_review <session> [--out x.html] [--no-sat]` writes one self-contained HTML page with:
- a P3 header and a timeline;
- setpoint vs actual for every Des/FB pair (position in m first);
- spectra with per-stream peaks;
- motor saturation over the flight span (ground idle excluded);
- a per-slot sample-interval histogram with gap count.

The numbers are embedded as JSON (`page_data`), and the tests use a session written by the real CsvRecorder. A hand check
on logs/sessions/20260927-005647-flight_test_12 (6.3 MB) took 14 s, gave 4 plots and showed a 98 s stall on slots 2 and 3.
WP-34 pairing: the tracking errors are the per-loop Des/FB inputs that mrac_log_replay and refmodel_replay replay; no code is shared yet.

## Acceptance
- pytest sim/sil ground_station/analysis/tests ground_station/service/tests: `2 failed, 1186 passed, 11 skipped` (18 min).
  Both failures are in test_controller_descriptor and predate WP-42. WP-38 C raised the cmd 0x01 bound to 800, but the
  test still used hi=250. The test now derives the value from the contract (42 passed).
- bash tools/check.sh: CHECK PASS (the sil-smoke includes test_faults.py).
