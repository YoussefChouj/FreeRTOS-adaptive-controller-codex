# Planning report, 2026-10-24 (lab day)

From the CEO planning session (plan-only, no agents launched from it). Use this file to launch the build agents
and, later, to review their work. Add review results to the "Review log" at the bottom.

## 1. What gets built, by whom

| # | Spec (prompt at its end) | Agent | Folder | Branch | Report it writes |
|---|---|---|---|---|---|
| 1 | docs/design/2026-10-24-lab-prep-mrac-flights.md | Host | main checkout | main | docs/lab/2026-10-24-prep-report.md |
| 2 | docs/design/2026-10-24-all-loop-mrac-layers.md | Firmware MRAC | ../fc-mrac-all-loops | mrac-all-loops | docs/design/2026-10-24-all-loop-mrac-layers-report.md |
| 3 | docs/design/2026-10-24-state-estimation-upgrades.md, Part A | Estimator | ../fc-estimator | est-upgrades | docs/design/2026-10-24-state-estimation-upgrades-report.md |
| 4 | docs/research/2026-10-24-drift-nn-v2-spec.md (replaces Part B) | Drift NN v2 (offline, Kaggle) | ../fc-driftnn | drift-nn-lofo | docs/research/2026-10-24-drift-nn-v2-report.md |
| 5 | docs/design/2026-10-24-sysid-excitation.md | Sysid | ../fc-sysid | sysid-excite | docs/design/2026-10-24-sysid-excitation-report.md |

File ownership: the table in spec 2. Numbers: the firmware MRAC agent takes the next free CMD / VAR / field numbers;
the estimator starts 8 above them. Both list every number in their reports.

## 2. Before launching (done by the CEO)

The CEO created the four worktrees at f29459cd (fc-mrac-all-loops, fc-estimator, fc-driftnn, fc-sysid) and
fast-forwarded them to the docs commit. The operator runs no git command. The prompts, the Cursor setup and the
launch order are in docs/agent/2026-10-24-agent-prompts.md; what each agent reads is in
docs/agent/2026-10-24-context-map.md. Flight logs are not in git, so the worktree agents read them from the main
checkout's logs/ (read-only, written in their prompts). Do not stash the uncommitted host edits in main.

## 3. Launch order and timing

1. Firmware MRAC (2) and Host (1) together, first. Item 0 of spec 2 (zeta + write order) unblocks the matched
   flights, which fly first.
2. Estimator (3) at the same time if the laptop holds it. The laptop's power is fragile: at most 3 agents at once.
3. Drift NN (4) after the lab, or on Kaggle. It changes nothing today (shadow only).
4. Sysid (5) in the third heavy slot: its S0 audit answers "was the 10-21 sysid data good?". The current order is
   in docs/agent/2026-10-24-agent-prompts.md (it wins over this list).

Freeze point: 60 min before leaving (PROPOSED). What passes the merge gate (section 4) is merged, built, soaked and
flashed. A layer whose code is not reviewed yet still ships flag-off. Its flight waits in the deck, and the flag is
turned on by a ground write after review, with no rebuild. After any commit at the lab, restart the 8081 service.

## 4. QA checklist (CEO, per report; re-run the checks, a report's "green" is a claim)

All branches:
- [ ] `git diff --stat main..<branch>`: only owned files, one commit per item
- [ ] Every number in the diff has a log name next to it or says PROPOSED
- [ ] Scoped tests re-run by the CEO

Firmware MRAC (2):
- [ ] The first lines of the report list the clamp rows still at 0 (PROPOSED). Set them from the log before the flash.
- [ ] Equivalence test (all new layers off = today's output) re-run: plain and sigma-prior
- [ ] Item 0 write-order test: zeta and bandwidth survive the ground latch
- [ ] K_SEP clamp and no-windup tests present for z, yaw and angle
- [ ] Angle layer: off by default, the tightest clamp, tilt trip unchanged (read the diff)
- [ ] After the merge: Keil 0 errors in the main checkout, soak of docs/lab/days/2026-10-24.yaml, flash with
      `rebuild_and_flash --yes`, smoke hover first

Host (1):
- [ ] Field numbers read from firmware_contract.py (grep for hard-coded numbers)
- [ ] Every new .fly script parses, the 10-24 deck loads, soak green on the fake drone
- [ ] docs/lab/COMMANDS.md updated for every new command

Estimator (3):
- [ ] Step-0 map present (what already exists in calib.c / ekf_of.c)
- [ ] All flags off = bit-identical, re-run
- [ ] A4 delay read from the deck, not 330 ms hard-coded
- [ ] No MRAC files touched (overlap risk: firmware_contract.py, own marked section only)

Drift NN (4):
- [ ] LOFO table per held-out flight, p50 and p90
- [ ] Each citation checked (title, venue, year, quote)
- [ ] Go / no-go against step 8; no change to the live system

## 5. Decisions taken in the session

- No flight is dropped because software or firmware still needs work. Build it; anything unreviewed ships flag-off.
- The operator launches the agents by hand (Cursor, local). The planning session launches none and shows no cards.
- Every number is measured or marked PROPOSED.
- Matched reference = the nominal closed loop of the baseline controller, so MRAC adapts only to plant mismatch.
  - PID: T = PC / (1 + PC), reduced to second order, or fitted from the alone flight.
  - LQR: A - BK.
  - ADRC: its designed bandwidth.
  - MPC: the linearized state-space prediction model. Unconstrained, it is u = -K_mpc x, so the closed loop is
    A - B K_mpc. Pause adaptation while a constraint is active.
  - Sliding mode, bang-bang and saturated loops: only a local linearization at hover.
- Drift NN: shadow only until LOFO beats optical-flow-only. Denoising is judged apart from drift removal.
- Estimator upgrades: flag-gated, flown off, on, on, off.

## 6. Pre-registration (fill in before the first all-loop flight, then do not change it)

Per loop group (attitude, heading, height, horizontal), inner vs. both:
- Primary metric (one, chosen now): RMS tracking error of the outer variable over the step pattern.
- Secondary metrics:
  - overshoot %
  - rise time 10-90 %
  - 2 % settling time
  - control effort: RMS of u and of du/dt
  - disturbance peak and recovery time
  - gain and phase margin from the multisine at the roll injection point, valid only inside the excited band
- % improvement = (inner - both) / inner x 100, reported per flight pair with its range, not only the mean.
- Decision rule: "both" wins only if:
  - the primary improves by more than the same-arm spread (|A1 - A2|, |C1 - C2|);
  - no secondary metric gets worse by more than its own spread;
  - the margins stay above 6 dB / 45 deg (PROPOSED, textbook values, not measured).
- Order A B C C B A, so slow drift (battery, temperature) affects both arms equally.
- 4 groups x 7 metrics = many tests. The claim is the primary metric only (Holm correction across the 4 groups).
  Secondary metrics are descriptive.
- n = 2 per arm is weak evidence. Write it as "consistent with", not "shown".

## 7. Open questions for the operator

1. Which single comparison is today's main claim?
2. Is the IMU temperature logged? Item A6 depends on it.
3. Who reads the angle-layer diff before its flag is turned on?
4. Drift NN on Kaggle tonight, or after the lab tomorrow?

## 8. Knowledge gaps seen in the session (coaching list)

- How a controller plus plant gives a closed loop, and how to read its bandwidth and damping (the matched model).
- MPC is linear state feedback when no constraint is active.
- Margins from flight data: where the loop is broken, and why they are valid only inside the excited band.
- Small-n statistics: same-arm spread, pre-registration, multiple comparisons.
- Learned odometry: why a held-out flight is the only honest test (15.7 cm on training flights vs. 260 cm held-out).

## Review log

- 2026-10-24: worktrees fc-mrac-all-loops, fc-estimator, fc-driftnn, fc-sysid created at f29459cd by the CEO and
  fast-forwarded to the docs commit. No agent output in main yet. Host prompt corrected (no drop); firmware agent
  relaunched in its worktree with item 0 first. Drift NN Part B replaced by the v2 spec; sysid spec added (row 5).
