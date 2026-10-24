# Spec: an adaptive (MRAC) layer on every inner and outer loop

Status: SPEC, built TODAY (2026-10-24) and flashed before the first flight of the 10-24 lab. Rewritten 2026-10-24
after the operator's rule: **no flight is dropped because firmware or software still needs work.** The all-loop
flights are at the end of the 10-24 deck, so the build has until then. Anything not reviewed in time still ships,
flag-off (default = today's behaviour), and its flight waits in the deck until the reviewed build is flashed.
Thesis link: configurable multi-time-scale MRAC, controller-agnostic (memory thesis-core-claim). Each loop
gets its own adaptive layer, and the operator picks per loop group which layers run.
Host side (verbs, scripts, deck, metrics): docs/design/2026-10-24-lab-prep-mrac-flights.md, another agent.

## Loop map today

| Loop group | Inner loop | MRAC on inner | Outer loop | MRAC on outer |
|---|---|---|---|---|
| Attitude roll / pitch | body rate | yes | angle | **no, build** |
| Heading | yaw rate | yes | yaw angle | **no, build** |
| Height | z velocity (rate) | yes | z position | **no, build** |
| Horizontal | xy velocity | yes | xy position | yes (D4, `MRAC_PosStep`, d0f29927) |

The xy select is CMD 0x1D field 31 `xy_layers` = inner / outer / both, latched on the ground. Copy that design.

## Work order (one commit per item, in this order)

The order is chosen so the lab gets the most from a partial build: item 0 unblocks the matched-reference flights,
which fly first.

0. **Zeta over WiFi + write order (Gate W). Own commit, first.**
   - Add `MRAC_VF_REF_ZETA` (the next free `MRAC_VF_` index after API/mrac.h:303-322) with bounds in
     `variant_bounds`. Then a second-order "matched" reference (bandwidth + damping) can be sent.
     Bounds: PROPOSED 0.3 to 1.5 (a reference below 0.3 rings, above 1.5 is a slow first-order lag in practice).
   - Fix the write order: `MRAC_XyRefSelect` (runs on the ground latch) must not overwrite variant writes
     (REF_TYPE, REF_MODEL_BW, REF_DELAY_S, REF_ZETA) made after the latch or before it in the same ground window.
     Rule: an explicit variant write wins over the row default. Unit test: write bw and zeta, latch, read back,
     both unchanged.
   - Add the field to ground_station/platform/firmware_contract.py and the fake drone.
1. **Equivalence test, then the layer-select fields.**
   - Write the equivalence test first: with every new layer off, the controller output is bit-identical to today's
     firmware (copy the item-5 EQUIV test of the 10-22 stack: plain and sigma-prior cases).
   - Three new CMD 0x1D fields, `att_layers`, `yaw_layers`, `z_layers`, each inner / outer / both / off, latched on
     the ground like field 31. The default is today's behaviour (inner only). Add them to firmware_contract.py:
     the host `layers` verb reads field numbers from there, so no host change is needed.
2. **z position outer layer.** Copy `MRAC_PosStep`: reference model on the z position error, adaptive law that adds
   a bounded correction to the z-velocity setpoint, projection bounds and sigma leakage as the existing layers.
   Ship it with its clamp row (item 6 rules), the K_SEP check (item 6), no windup (item 6) and its log fields
   (item 7). A layer without those is not done.
3. **Yaw angle outer layer.** Same pattern, correction on the yaw-rate setpoint. Wrap the yaw error to (-180, 180].
4. **Angle (roll, pitch) outer layer.** Same pattern, correction on the rate setpoint. This one touches attitude
   directly, so: off by default, the tightest clamp, the tilt trip and abort limits unchanged.
5. **Prior / warm start for the new axes.** Extend the CMD 0x2F..0x34 path (`Cmd_MracPrior`, 477d6663) to the new
   axes, same rules (disarmed re-seed only).
6. **Rules every outer layer obeys** (built inside items 2-4, listed once here):
   - Correction clamp per axis in a `*_ROW` table (docs/firmware-table-pattern.md). The limit comes from the logged
     setpoint ranges of the latest lab logs (10-21; 10-23 if flown); write the log, the variable and the value next
     to each row. If no log gives a number, the row stays 0 (the layer cannot act), marked PROPOSED, and the report
     says so in its first lines: the CEO sets it before the flash.
   - Time-scale separation: an outer reference bandwidth must be at most the inner closed-loop bandwidth / K_SEP.
     K_SEP = 3 is PROPOSED. The firmware clamps a config that breaks this and logs the clamp.
   - No windup: outer adaptation freezes while the inner loop is saturated.
7. **Logging and host contract.** For each new layer, log Theta, the reference error and the correction (extend the
   mrac_xt block or add one). Update firmware_contract.py, ground_station/service/fake_drone.py and
   ground_station/analysis/mrac_active.py (new axes). Do NOT edit campaign_fly.py, campaigns/*.fly, decks or
   docs/lab/COMMANDS.md: the host agent owns them.
   Margins need the loop broken at the roll injection point: check that the var table logs both the injected
   signal U and the controller output Uc there (lab_d_excite). If one is missing, add it to the var table.

## File ownership (three agents work at once)

| Agent | Where | Owns |
|---|---|---|
| This one (firmware MRAC) | worktree ../fc-mrac-all-loops, branch `mrac-all-loops` | API/mrac*.c/.h and the outer-loop call sites, firmware_contract.py, fake_drone.py, mrac_active.py |
| Host | main checkout, branch main | campaign_fly.py, analysis tools, campaigns/*.fly, decks, COMMANDS.md |
| Estimator | worktree ../fc-estimator, branch `est-upgrades` | API/ekf*, gyro_filter, imu_update, calib, Accel_Calibartion, thrust_estimators, time_estimate, Filter |

Numbers: this agent takes the next free CMD / VAR / field numbers. The estimator agent starts 8 above the next free
number, so the two never collide. Write every number taken in the report.

## Safety rules

- With every new layer off (the default), the output is **bit-identical** to today's firmware (item 1 test).
- The angle layer is off by default, has the tightest clamp, and the tilt trip and abort limits do not change.
- No flash by the build agent. The CEO reviews, merges, builds and flashes; the first flight after the flash is a
  smoke hover.

## Tests

- Per new layer: a step with a known plant mismatch converges, the bounds hold, no windup under inner saturation,
  the K_SEP clamp fires.
- The equivalence test (all new layers off = today's output).
- Item 0: write-order test (variant write survives the latch).
- Host-side tests for the contract fields, the log block, mrac_active and the fake drone.
- A Keil build with 0 errors and no new warnings. If Keil cannot build from the worktree path (absolute paths in
  the .uvprojx), say so in the report; the CEO builds after the merge.

## Flights

Defined in the host spec, section 3 (scripts lab_l_att_*, lab_l_z_*, lab_l_yaw_*, lab_l_all_off / lab_l_all_both)
and in docs/lab/days/2026-10-24.yaml. Order A B C C B A per group; pass marks PROPOSED against the same-day arm.

## Done means

All tests green, Keil clean (or the worktree limit reported), one commit per item on branch `mrac-all-loops`
(not merged, not pushed), and a report `docs/design/2026-10-24-all-loop-mrac-layers-report.md`:
- what is built, per item, with the commit hash
- the equivalence result
- every CMD / VAR / field number taken
- every number marked PROPOSED, with the log it should come from (clamp rows first)
- what is left

---

## Prompt for the build agent (paste as is)

> Before you start, the operator has run, in D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex:
> `git switch main` then `git worktree add ../fc-mrac-all-loops mrac-all-loops`.
> You work ONLY in D:\Projects\UAV_lab\fc-mrac-all-loops (branch `mrac-all-loops`). If
> `git branch --show-current` there is not `mrac-all-loops`, stop and tell me.
> Read AGENTS.md and docs/firmware-table-pattern.md, then build docs/design/2026-10-24-all-loop-mrac-layers.md,
> items 0 to 7, in that order, one commit per item. Start by reading `MRAC_PosStep` and commit d0f29927 (xy outer
> layer + field 31) and copy that structure; do not invent a new one.
> Flight logs are not in git (about 43,000 files): read them from D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs,
> read-only. Never write in that checkout; another agent works there.
> Rules:
> - Never flash, never touch the 8081 service or a real drone. Do not merge, do not push.
> - Only the files the spec's ownership table gives you; two other agents work at the same time.
> - Stage files by path, never `git add -A`.
> - Do not invent numbers: every limit or bandwidth comes from a log or the sim, or stays PROPOSED with a default
>   that leaves the layer unable to act.
> - With all new layers off, the output must be bit-identical to today's; write that test before the layers.
> - Run only the tests of the files you change, plus a Keil build at the end.
> If blocked for more than 20 minutes on one item, write it in the report and go to the next.
> Finish with the report the spec names and a 10-line summary.
