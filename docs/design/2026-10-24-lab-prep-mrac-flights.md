# Spec: prepare every flight for the 2026-10-24 lab, including the new MRAC flights

Status: SPEC, not built. Written 2026-10-24 (plan-only deck review). Host part. The firmware it needs (zeta over WiFi, the
write-order fix, the new loop layers) is docs/design/2026-10-24-all-loop-mrac-layers.md, built TODAY in parallel and
flashed before the first flight. Rule (user 2026-10-24): no flight is dropped because software is missing; build it.
Lab: today, 5-6 h, packs not limited. Rule: everything here is built and soaked BEFORE the operator leaves.

## Why

The 2026-10-23 deck (not flown, moves to today) flies the controllers alone and the memory A/B. The MRAC stack
built 10-22 (items 1-7 of docs/design/2026-10-22-mrac-experiment-stack.md) has no flight that uses it:
no `.fly` script sets the x/y layer (CMD 0x1D field 31), the excitation patterns (CMD 0x2E), the prior
(CMD 0x2F..0x34) or a per-controller reference. This spec adds those flights.

## Facts checked in the code (2026-10-24)

| Fact | Where |
|---|---|
| x/y reference rows for ADRC / LQR / MPC are still copies of the PID row (type 1, bw 2.0, zeta 0.8, 0 ticks), marked PROPOSED | API/mrac.c:90-111 |
| WiFi can set per axis: `REF_TYPE` (0), `REF_DELAY_S` (1), `REF_MODEL_BW` (12). There is NO zeta field | API/mrac.h:303-322 |
| `MRAC_XyRefSelect(kind)` copies a row into mrac_config_x/y when the kind changes | API/mrac.c |
| Host client already has `mrac_variant(axis, field, value)`, `mrac_xy_layers(target)`, `mrac_prior(axis, idx, value)` | ground_station/service/campaign_fly.py:1102-1144 |
| Scenario steps already have `pattern {layer, speed, axes, periods}` (CMD 0x2E) | ground_station/service/scenario_schema.py:12,110,147 |
| `refmodel_replay.py` replays the MRAC law on SHADOW logs (injection off) for any reference type | ground_station/analysis/refmodel_replay.py |
| `.fly` verbs seen: campaign exp hold land log preset rate set slow takeoff excite. No verb for layers, variants, prior or pattern | ground_station/service/campaigns/*.fly |

So "matched" means a reference fitted from the controller's own alone-retest flight: first-order plus delay
(type 1) on the current firmware, second-order (bw, zeta, delay) once the firmware has `MRAC_VF_REF_ZETA`
(all-loop spec item 0). The verb sends zeta only if firmware_contract.py lists the field.

## What to build (host only)

### 1. `.fly` verbs (ground_station/service/campaign_fly.py, the .fly parser)

| Verb | Sends | Rule |
|---|---|---|
| `layers <group> inner\|outer\|both\|off` (groups xy, att, yaw, z; keep `xy_layers` as an alias) | CMD 0x1D: field 31 for xy; the att / yaw / z field numbers come from firmware_contract.py | before `takeoff` (latched on the ground). A group whose field is not in firmware_contract.py yet: refuse with "needs the all-loop flash". The firmware agent adds the fields there; nothing to change here later |
| `mrac_ref from=<kind>` | `mrac_variant(x/y, REF_TYPE, REF_MODEL_BW=bw, REF_DELAY_S=d[, REF_ZETA=z])` from `logs/ref_fit/<kind>.json` | refuse the flight if the file is missing (no unmeasured numbers) |
| `step axis=z\|yaw delta=<m or deg> hold=<s>` (only if no verb does this today; check `excite` and `preset` first) | a setpoint step through the existing preset / traj path | delta within the script's existing limits; no new abort limit |
| `pattern layer=outer\|inner speed=slow\|fast axes=x,y periods=N` | CMD 0x2E, same checks as the scenario `pattern` step | must follow a `hold` |
| `mrac_prior from=<session>` | CMD 0x2F..0x34 with Theta from that session's last mrac_xt sample, `warm=1` | disarmed only; refuse if the session has no mrac_xt block |

If a verb already exists under another name, reuse it and say so in the report. Each write is
idempotent and resent on an unknown outcome (memory lossy-link rule): read back, three tries, then abort the
flight on the ground.

**Gate W (write order), must pass before any matched flight is in the deck:** find where `MRAC_XyRefSelect` is
called. If it runs on the exp latch or on the ground latch AFTER the `mrac_ref` writes, the row overwrites them.
Prove the order on the fake drone (ground_station/service/fake_drone.py) by reading `ref_model_bw` back after
take-off. If the order is wrong: write it in the report and KEEP the matched flights; the firmware fix is all-loop
spec item 0. Re-run Gate W once that fix is merged. Never edit firmware yourself (the firmware agent owns API/).

### 2. Reference fit tool: `python -m ground_station.analysis.mrac_ref_fit <session> --kind lqr|mpc|adrc|pid`

- Input: an alone-retest session (MRAC learning on, injection off = shadow log).
- Fit, per axis x and y: the first-order-plus-delay model AND the second-order-plus-delay model (bw, zeta, delay) from the velocity demand to the measured velocity
  (bw in rad/s, delay in s), by least squares over the hold and figure-8 segments. Reuse refmodel_replay's
  loader and the firmware ref-model code path; do not write a second copy of the ref model.
- Also report: the fit residual, and the replayed MRAC tracking error with the fitted reference against the PID
  row (that is the expected benefit, from refmodel_replay).
- Clamp to the firmware bounds (`variant_bounds`); if a value hits a bound, say so and still write it.
- Output `logs/ref_fit/<kind>.json` {kind, session, order, bw_x, bw_y, zeta_x, zeta_y, delay_x, delay_y, residual_1st, residual_2nd, replay_gain} plus one
  line on stdout. Tests: a synthetic first-order log with known bw and delay is recovered within 5 %.

### 2b. A/B metrics tool: `python -m ground_station.analysis.layer_ab <day> --arms <id,id,...> --ref <id>`

Rule (user 2026-10-24): prove "both" beats "inner" with control metrics, not only rms. Per flight, per axis, on
the same segments:

| Metric | How | Segment |
|---|---|---|
| tracking error | rms and p95 of position and velocity error | hold, figure-8 |
| step response | rise time 10-90 %, overshoot %, settling time (5 % band) | `step` segments |
| control effort | rms of the inner setpoint and of the motor command; % of samples saturated | all |
| stability margins | multisine: loop gain L(jw) = -Uc/U at each excited frequency (Uc = controller output, U = Uc + injected signal); gain and phase margin by interpolation, ONLY inside the excited band, else print "crossover outside the band" | excitation segments |
| oscillation | peak-to-peak and dominant frequency in the hold | hold |

- First find where `excite` / `lab_d_excite` injects and which signals are logged. If Uc and U at the injection
  point are not logged, add them to the log plan (host only); if that needs firmware, write the request in the
  report for the firmware agent.
- Output per arm: each metric, its % change against `--ref`, and the same-arm spread (difference between the
  two repeats of one arm) next to it. A change smaller than the same-arm spread prints "within noise".
- Test: a synthetic log of a known loop gives back its gain and phase margin within 10 %.

### 3. New `.fly` scripts (ground_station/service/campaigns/)

Copy the matching `lab_e_exp_*` script (same exp figure-8, hold, abort limits, `set g_xyc_delay_ticks=21`);
change only what the table says. MRAC injection ON unless noted.

| Script | Base | Change |
|---|---|---|
| lab_e_xyl_inner | lab_e_exp_matrix (PID) | `xy_layers inner` |
| lab_e_xyl_outer | lab_e_exp_matrix | `xy_layers outer` |
| lab_e_xyl_both | lab_e_exp_matrix | `xy_layers both` |
| lab_e_exp_lqr_matched | lab_e_exp_lqr_3l | `xy_layers inner`, `mrac_ref from=lqr` |
| lab_e_exp_mpc_matched | lab_e_exp_mpc_3l | `xy_layers inner`, `mrac_ref from=mpc` |
| lab_e_exp_adrc_matched | lab_e_exp_adrc_3l | `xy_layers inner`, `mrac_ref from=adrc` |
| lab_p_pattern_learn | lab_e_exp_pid_u0 | after the hold: `pattern layer=outer speed=slow axes=x,y periods=<from excite_patterns default>`; injection OFF, learning ON |
| lab_p_prior_cold | lab_e_exp_matrix | no prior (reset), same figure-8 |
| lab_p_prior_warm | lab_e_exp_matrix | `mrac_prior from=<pattern-learn session>` |
| lab_l_att_{inner,outer,both} | lab_d_excite (roll multisine) | `layers att <arm>`, injection ON |
| lab_l_z_{inner,outer,both} | lab_e_exp_matrix hold | `layers z <arm>`; three `step axis=z` (amplitude from an existing script, else PROPOSED and small) |
| lab_l_yaw_{inner,outer,both} | lab_e_exp_matrix hold | `layers yaw <arm>`; three `step axis=yaw` (same rule) |
| lab_l_all_off / lab_l_all_both | lab_e_exp_matrix | every group off / every group both; adds the roll multisine and the z / yaw steps |

### 4. Deck `docs/lab/days/2026-10-24.yaml`

Copy the 2026-10-23 deck (flights, camera, before, branches) with `day: 2026-10-24`; keep its order and stops.
Then add the block below after `pid-track`. Pass marks are PROPOSED; compare against the same-day `pid-alone-ref`.

| id | fly | why | stop |
|---|---|---|---|
| ref-fit (ground step, `before`-style note) | `mrac_ref_fit` on lqr/mpc/adrc-alone-retest | writes the three ref files | |
| xy-layer-inner-1 | lab_e_xyl_inner | which loop should adapt: velocity only | |
| xy-layer-outer-1 | lab_e_xyl_outer | position only | |
| xy-layer-both-1 | lab_e_xyl_both | both | |
| xy-layer-both-2 | lab_e_xyl_both | repeat, reversed order (A B C C B A) so pack drift hits every arm | |
| xy-layer-outer-2 | lab_e_xyl_outer | repeat | |
| xy-layer-inner-2 | lab_e_xyl_inner | repeat | yes |
| lqr-plus-mrac-matched | lab_e_exp_lqr_matched | MRAC with LQR's own reference; after lqr-alone-retest (its log makes the ref file) | |
| mpc-plus-mrac-matched | lab_e_exp_mpc_matched | same, MPC | |
| adrc-plus-mrac-matched | lab_e_exp_adrc_matched | same, ADRC; pairs with adrc-plus-mrac-mismatched | yes |
| pattern-learn | lab_p_pattern_learn | learn Theta from a slow outer pattern, injection off | |
| prior-cold | lab_p_prior_cold | baseline: MRAC starts from zero | |
| prior-warm | lab_p_prior_warm | MRAC starts from the pattern-learn Theta | |
| prior-cold-2 / prior-warm-2 | same | n=2, interleaved | yes |

All-loop layer block (after the block above; needs the all-loop flash):

| id | fly | why | stop |
|---|---|---|---|
| att-layer-inner-1, -outer-1, -both-1, -both-2, -outer-2, -inner-2 | lab_l_att_* | roll multisine; MRAC on rate, angle, or both | after -inner-2 |
| z-layer-... (same 6) | lab_l_z_* | altitude steps; MRAC on z velocity, z position, or both | after -inner-2 |
| yaw-layer-... (same 6) | lab_l_yaw_* | heading steps | after -inner-2 |
| all-layers-off-1, -both-1, -both-2, -off-2 | lab_l_all_off / _both | the full multi-time-scale stack against all layers off | after -off-2 |

Branches to add: matched flight with no ref file → run `mrac_ref_fit` on that controller's alone-retest (1 min, on the ground), then fly it. Alone-retest failed → land, tell me, fix, re-fly
the retest, then its matched flight. Any write ends `no result` → re-fly (writes are idempotent).
The 2026-10-23-fast and 2026-10-23-mode1 decks fly after this one by their own names; check that `labday` accepts a
past date id; if not, copy them to 2026-10-24-fast / -mode1 the same way.

### 5. Before-the-lab checks (part of done)

1. Unit tests for the new verbs, the fit tool and the deck schema (scoped: changed files only).
2. Fake-drone soak of all three of today's decks, one at a time:
   `python -m ground_station.lab.soak --days 1 --day-src docs/lab/days/<deck>.yaml --out logs/soak/1024-<deck>`;
   every report.md all-pass. The matched flights need ref files in the soak: generate them from a fake-drone
   alone-retest run, never write numbers by hand.
3. Gate W result in the report.
4. Known bug: the rx_age heartbeat aborted drift-nn-live-1 after a pack swap (10-21). Reproduce on the fake drone
   (swap = link drop + reconnect between flights). Fix it only if the fix is host-side and small; else report.
5. `docs/lab/COMMANDS.md`: add the fit command, layer_ab and the new verbs.
6. The all-loop block soaks only after the firmware branch is merged (the fake drone gets the new groups there).
   Before that, the expected soak result for it is the "needs the all-loop flash" refusal; the CEO re-runs the
   soak after the merge.

## Done means

All of section 5 passes; one commit per piece (verbs, fit tool, layer_ab, scripts, deck), not pushed; a report
`docs/lab/2026-10-24-prep-report.md` with: Gate W result, soak results per deck, what was skipped and why.

## Out of scope today

Firmware: zeta over WiFi, the write-order fix and the new loop layers are docs/design/2026-10-24-all-loop-mrac-layers.md
(today, another agent). Estimator upgrades: docs/design/2026-10-24-state-estimation-upgrades.md.

---

## Prompt for the build agent (paste as is)

> You work in D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex on branch main, in the working tree as it is
> (it has uncommitted host edits; do not stash, reset or revert them). If `git branch --show-current` is not
> main, stop and tell me. Read AGENTS.md, then build the spec
> docs/design/2026-10-24-lab-prep-mrac-flights.md, sections 1, 2, 2b, 3, 4, 5, in that order. Stage files by path
> (never `git add -A`): two other agents work in their own worktrees.
> Rules: host code only (ground_station/, tools/, docs/lab/). Do not edit ground_station/platform/firmware_contract.py,
> ground_station/service/fake_drone.py or ground_station/analysis/mrac_active.py: the firmware agent owns them; read
> field numbers from firmware_contract.py, never hard-code them. Never edit API/, BSP/ or other firmware, never flash,
> never touch the 8081 service or a real drone. Do not invent numbers: every bw, delay or pass mark comes from a
> log or stays marked PROPOSED. Run only the tests of the files you change, plus the soak in section 5.
> Commit after each piece with a clear message; do not push. If Gate W fails, write it in the report and keep the
> matched flights (the firmware agent fixes the order). If anything blocks you for more than 20 minutes, write it in the report and move on.
> Finish with docs/lab/2026-10-24-prep-report.md and a 10-line summary: what is built, soak result per deck,
> Gate W, what was skipped.
