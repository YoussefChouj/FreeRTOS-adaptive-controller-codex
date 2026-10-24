Item 0 (zeta over WiFi + Gate W write order): 8e1de8ff, then 7f4218d8 (the Gate W record fix) on branch `mrac-all-loops`.
The matched flights need 7f4218d8: it is the commit the firmware must be built from. Branch only, not merged, not pushed.

# All-loop MRAC layers: what is built (spec docs/design/2026-10-24-all-loop-mrac-layers.md)

Agent: firmware MRAC. Worktree `D:\Projects\UAV_lab\fc-mrac-all-loops`, branch `mrac-all-loops`.
One commit per item, in the order of the spec. Every new layer is off by default.

## 0. Clamp rows (PROPOSED, item 6 rules) - READ FIRST

Every number below is **PROPOSED**: no log in the lab corpus gives the value yet, so each row stays **0**
(the layer cannot act) until the operator sets it. The log each one must come from is named next to it, so the
CEO can fill them from one session instead of a guess.

| Row (firmware table) | Axis | Correction unit | Value | Status | The log it must come from |
|---|---|---|---|---|---|
| `MRAC_ZPOS_ROW` u_max | z position -> z velocity setpoint | m/s | 0 | PROPOSED | 10-21 `lab-d-livetune-pos_*`: the `Ctrler.Z_velPID.Des` range (z velocity setpoint) |
| `MRAC_YAWANG_ROW` u_max | yaw angle -> yaw rate setpoint | deg/s | 0 | PROPOSED | 10-21 rate flights: the `Ctrler.gyrozPID.Des` range (yaw rate setpoint) |
| `MRAC_ATTANG_ROW` u_max | roll/pitch angle -> body rate setpoint | deg/s | 0 | PROPOSED | 10-21 rate flights: the `Ctrler.gyroxPID.Des` / `gyroyPID.Des` range (body rate setpoint) |

The reference-model bandwidths of the new outer layers are PROPOSED for the same reason (item 6, K_SEP = 3):
they must be at most the inner closed-loop bandwidth / 3, and the inner bandwidths are the ones the logs carry.

## 1. What is built, per item, with the commit hash

| Item | What | Commit | State |
|---|---|---|---|
| 0 | `MRAC_VF_REF_ZETA` (CMD 0x1D field 32, bounds 0.3..1.5) + the Gate W write order | `8e1de8ff`, `7f4218d8` | done |
| 1 | equivalence test first, then `att_layers` / `yaw_layers` / `z_layers` (CMD 0x1D fields 33/34/35) | this commit (`git log -- API/tests/test_mrac_group_layers.c`) | done: firmware + host table; the selectors latch but no outer layer reads them yet (items 2-4) |
| 2 | z position outer layer | - | not started |
| 3 | yaw angle outer layer | - | not started |
| 4 | roll/pitch angle outer layer | - | not started |
| 5 | prior / warm start for the new axes | - | not started |
| 6 | the rules every outer layer obeys (clamp rows, K_SEP, no windup) | - | not started |

Item 1 checks (2026-10-24, all measured):

| Check | Result |
|---|---|
| `python API/tests/run_all_loop_equiv.py` vs base `a7891b70` | EQUIV OK: mrac half 21968848 lines identical (plain) and 24280848 (sigma-prior); StabilizerTask hash identical, seeds 1 and 2 |
| `python tools/host_tests.py mrac` | 13/13 pass; new row `mrac_group_layers`: 45 checks, 0 fails |
| `pytest ground_station/analysis/tests ground_station/platform` | pass, except the 2 capability-manifest tests, which need a built `OBJ/JX_FLY.axf` (the worktree has none). Regenerate the manifest in main after the build. |
| 7 | logging (Theta, reference error, correction) + contract / fake drone / mrac_active | - | not started |

### Item 0 in detail

**Zeta over WiFi.** `MRAC_VF_REF_ZETA` is CMD 0x1D field 32, bounds **0.3 .. 1.5** (PROPOSED: a reference below
0.3 rings, above 1.5 is a slow first-order lag in practice). It writes `ref_model_zeta`, the damping of the
second-order reference model, so a fitted "matched" reference (bandwidth + damping) can be sent over WiFi
instead of the first-order fit the lab flies today.

**The wire encoding.** Field 32 has no axes 0-3 encoding: `(field << 2) | axis` would run into the 0x80 range the
firmware reads as the x / y space. The x / y space carries the field in 6 bits (0..63), so **field 32 is
reachable on the x and y axes only** (the axes whose reference model it damps). The host refuses it on axes 0-3
instead of sending a byte the firmware would read as an x / y write.

**Gate W, the write order.** `MRAC_XyRefSelect` (the ground latch) applies the reference row of the selected
nominal. It must not overwrite a reference the operator wrote over WiFi. The rule: **an explicit CMD 0x1D write
wins over the row default**, in both orders (write then latch, latch then write) and for every reference field
(type, bandwidth, zeta, delay).

The record of those writes lives in `mrac_xy_ref_written[2]` (API/mrac.c): one byte per axis (x = 0, y = 1), one
bit per field. Two defects in the first version of this fix (`8e1de8ff`) are fixed in `7f4218d8`:

- the record was **one byte for both axes**, so a write on x stopped the row from filling y as well: a reference
  fitted on one axis pinned the other one to a value the operator never wrote;
- the record was **cleared only at boot**, so a reference fitted for one flight stayed on the next one, with no
  way back to the nominal row short of a reboot. The ground window now ends at the **disarm edge**
  (`MRAC_XyRefWindowEnd`, called from `MRAC_GateStep`), which drops the record and re-arms the row.

The host mirror already models this shape (`ground_station/platform/mrac_state.py`: `xy_ref_written[2]`,
`xy_ref_window_end`), so the host and the firmware agree on the semantics.

## 2. The equivalence result

With every new layer off (the default), the output is bit-identical to today's firmware.

| Proof | What it compares | Result |
|---|---|---|
| `API/tests/run_mrac_equiv.py` | `API/mrac.c` tick by tick vs the base tree, plain and sigma-prior | **21968848 lines identical (plain) + 24280848 (sigma-prior)** |
| `API/tests/run_all_loop_equiv.py` | the same, plus `TASK/StabilizerTask.c` over seeded random control ticks (hash of every external call and of the state the file owns) | **ALL-LOOP EQUIV OK** - mrac as above, stab seed 1 hash `f365f4d485487d80` (940247 calls) and seed 2 hash `cce93817e1f4c3ef` (951690 calls) equal on both trees |

The base is the merge base with `main` (`a7891b70`), so the proof survives a rebase.

## 3. Every CMD / VAR / field number taken

| Number | What | Notes |
|---|---|---|
| CMD 0x1D field 32 | `MRAC_VF_REF_ZETA` | the 2nd-order reference damping; x / y axes only on the wire |
| CMD 0x1D field 33 | `att_layers` | item 1 |
| CMD 0x1D field 34 | `yaw_layers` | item 1 |
| CMD 0x1D field 35 | `z_layers` | item 1 |
| - | no new CMD id | the layer targets ride the existing CMD 0x1D |
| - | no new VAR id yet | item 7 logs the new layers; the ids are listed there when taken |

The estimator agent starts 8 above the next free field number, so it starts at **40**: the two never collide.

## 4. Every number marked PROPOSED

See the clamp table in section 0 (the first thing in this report, as the spec asks) and the per-item notes
below. PROPOSED means: no log gives the number yet. Nothing here is an invented measurement.

## 5. What is left

Items 1 to 7 (see the table in section 1), the Keil build at the end (behind `ah lock keil`), and the numbers in
the clamp table, which only the operator can set from a log.
