# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry last).
History: `docs/agent/ledger/` (grep, never read whole); the previous HANDOFF is `ledger/handoff-2026-10-07-archive.md`.
Working mode: the desktop session is the CEO and does every item inline. No subagents, no workers.

## Now: load test + variant sweep, manual mode only (2026-10-08 ~04:05)
Branch `overnight-2026-10-08`. Flown: exp8 `logs/exp8_vpA_noload` (vp 0-6, no load), analysis
`docs/flights/2026-10-08-exp8-analysis.md`: vp 6 best attitude, vp 3 landed on the pad (operator trim). Drift
with of1 off = unobserved EKF bias, +x/-y 2.4-6.5 cm/s.
Firmware NOT built since exp8: in-flight preset `vp_user` + `vp_user_go` (vp_active 100), FW-B rows 7-11
(S10, RBF12, RBF6, RBF24, S6+RBF12 via runtime `basis`; need Keil Define `MRAC_VARIANT=2`). Operator builds
and flashes in Keil.
Runbook: `docs/flights/2026-10-08-load-test-runbook.md` (exp9, kp_id 5, vp_id 6 then 3). Sweep plan:
`docs/flights/2026-10-08-variant-sweep-plan.md`. Flights: `/workflow-a`, logs `exp<N>_<vp>-<load>`.

## Next actions
1. Operator: default build -> exp9 load test (vp 6, vp 3). Agent: flight_review + debrief.
2. MULTI build -> exp10 no-load sweep vp 7-11; check CPU/CCM, vp_active 7-11 on the ground first.
3. Open: of1 on with R_of1 0.01 for drift; gyro-compensated of1; of2_h gate; RPM ch2/ch0 (`BSP/rpm.c`).

## Recording rules (operator, verbatim spirit)
- Start only when the operator asks, after one AskUserQuestion confirming the var list and the rate.
- No timer: record until the operator says stop. WiFi telemetry only, never the SWD probe.
- Confirmations are QA in chat; no dashboard approvals (operator 2026-10-07).

## Do not
- Arm or spin motors; flash with anything but `rebuild_and_flash --yes`; flash the ram-savings, o2-build,
  float-math, static-etag or h0g-port branches before the demo; touch protected files.
- Stage OBJ/ or USER/; `git stash`; commit the operator's landing hunks in StabilizerTask.c without asking.
- Run a bare `git status` (use `-- . ':!OBJ' ':!USER'`).

<!-- AUTO:BEGIN -->
<!-- AUTO:END -->
