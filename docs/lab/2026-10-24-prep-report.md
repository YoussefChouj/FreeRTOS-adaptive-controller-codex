# Lab prep report, 2026-10-24 (P1 host lab prep)

Spec: `docs/design/2026-10-24-lab-prep-mrac-flights.md`. This is report v2 (complete P1): it covers the sysID
placement (spec step 6), the ground-write and missing-input refuse rules (step 5), all 20 section-3 MRAC `.fly`
scripts (step 2), the `mrac_ref_fit` and `layer_ab` analysis CLIs (step 3), the 51-flight `2026-10-24.yaml` deck
(step 4), and the 9-flight MRAC fake-drone soak (`logs/soak/1024-mrac-new`).

## Deck order

| Order | Deck | Camera mode | First flight |
|---|---|---|---|
| 1 | `.\labday 2026-10-23-fast` | 2 | smoke-hover-mode2 (lab_s_smoke) |
| 2 | `.\labday 2026-10-24` | 3 | smoke-hover (lab_s_smoke) |

The operator chose this order on 2026-10-24: the fast deck flies first so the sysID data comes on a fresh pack.
Camera mode is per deck (`labday.py` `self.cam`), and the sysID flights need mode 2. Each excite re-zeroes the flow
origin, and mode 3 would pull the drone back mid-run. So sysID cannot sit after the smoke hover in the mode-3 main deck.
The fast deck now has its own smoke hover as flight 1.

## SysID flights (fast deck, after the smoke hover)

| Flight | Script | Axis | Passes | Band | Amp |
|---|---|---|---|---|---|
| rate-sysid-roll-pitch | lab_d_excite | roll, pitch | 1 each, 30 s | 0.5-15 Hz multisine | 60 deg/s |
| rate-sysid-roll-pitch-2 | lab_d_excite | roll, pitch | repeat | same | same |
| rate-sysid-yaw | lab_d_excite_yaw (new) | yaw | 2, 30 s | same | 60 deg/s (yaw cap) |

- Axes: the firmware sysID takes pitch, roll and yaw only (`autotune/excitation.py:25`). Amp caps: 90 deg/s for roll
  and pitch, 60 for yaw (l.34). The campaign excite cap is 60 (`scenario_schema.py:83`).
- Repeats: two runs per axis give the run-to-run spread of the frequency response.
- Gains: default PID, no livetune, MRAC injection 0.

## Log rate and signals

- Rate 200 Hz, the ceiling. In SUBSCRIBE_ONLY (the lab service's mode) the firmware stream ticks at 200 Hz
  (`Subscribe_StreamTickHz`, registry field 18; MIXED and LEGACY stay at 100 Hz). A log plan above `SEND_TASK_HZ`
  is refused (`campaign_capture.py:353`). 200 Hz is 13x the 15 Hz top line. The live link probe
  (`probe_max_rate`, ladder 200/100/50/25) may still lower it if the link loses frames. A drop below 200 Hz is a
  link fault to find, not a setting to accept. The bridge resends the mode before every subscribe request, so an FC
  reboot (boots MIXED, 100 Hz) is switched back before slots are priced.
- Signals: the core set has the gyro PID `.FB` / `.Des` (rate feedback and reference) and the motors. Both excite
  scripts now also log the `thrust_model` group, which carries `u_gyrox` / `u_gyroy` / `u_gyroz`, the rate PID output
  (Uc for the frequency response). Before this change the sysID flight did not log Uc.

## MRAC during sysID

No verb freezes MRAC learning. With `mrac_injection=0` the MRAC output is never applied, so the excite data is
clean. Learning still runs in shadow (`API/mrac.c` l.250-313: `learn_gate = basic_gate && !freeze_shadow`;
adaptation at l.1293). The shadow estimates after a sysID flight have seen the excite, so do not use them as a prior.
Possible later host/firmware item: a takeoff option that sets `freeze_shadow`.

## Ground writes and missing inputs: no flight dropped

- Missing firmware field: a flight whose firmware field is missing gets a `refuse` ground write
  (`ground_plan.refusing_write`, commit a120308e).
- Missing host input (`InputNotReady`): if `logs/ref_fit/<kind>.json` has not been generated yet (for `mrac_ref`) or
  `latest:<campaign>` has no flown session yet (for `prior`), `fly_file._ground_verb` catches `InputNotReady` and emits
  `refusing_write(...)`. The deck still parses and dry-runs; at flight time `fly_one` re-parses the `.fly` file, so once
  `mrac_ref_fit` or `pattern-learn` has run, the dependent flight proceeds cleanly.
- `latest:<campaign>` lookup (`ground_plan._latest_session`): accepts both `latest:lab-p-pattern-learn` and
  `latest:pattern_learn` (normalizing `lab_<x>_` prefixes and `_`/`-`).
- Disarmed `prior` pre-apply (`campaign_fly.preapply_prior`): firmware `Cmd_MracPrior` (`0x2F..0x34`) accepts packets
  only while disarmed. In the RC-arm workflow `wait_for_arm` blocks until the operator arms by RC before `fly_one`
  calls `fly_scenario`, so `campaign_fly` pre-applies `prior` writes before `wait_for_arm` and `campaign_runner` skips
  resending them once already RC-armed.

## Analysis CLIs (`mrac_ref_fit` and `layer_ab`)

- `python -m ground_station.analysis.mrac_ref_fit <session_dir> --kind lqr|mpc|adrc`: fits first-order `bw` (clamped to
  `[0.5, 10.0]` rad/s) and `rate_cap` (`1.2 * p99(|v_xy|)`, floor `0.5` m/s) from `x_pid.Des/FB` and `y_pid.Des/FB`
  during trajectory segments (`traj_status == 2`), writing `logs/ref_fit/<kind>.json`.
- `python -m ground_station.analysis.layer_ab <session_dir> [...] --out docs/lab/layer-ab-2026-10-24.md`: summarizes
  RMS tracking error (`pos_xy`, `att_rp`, `z`, `yaw`), RMS rate-PID command `Uc` (`thrust_model` `u_gyrox/y/z`), motor
  saturation % (`[2005, 3995]`), and final adaptation norm across layer configurations (`off`, `inner`, `outer`, `both`).

## Gate W (write order of the x/y reference)

The host must write a fitted reference (`mrac_ref`) after the exp row has latched, because both write the same
fields.

| Check | Result |
|---|---|
| Host order | PASS. The runner sends the exp and `set` VAR_WRITEs first and waits for the `exp_active` echo, then the ground writes (`campaign_runner.py` takeoff list). |
| Firmware on main | FAIL, two bugs. (A) `MRAC_XyRefSelect` copies its row over every reference field on the tick after the exp latch, so the `s_xyrm` row of the exp (lqr bw 4.8) is replaced by the nominal row (bw 2.0). (B) A fitted reference stays in the next flight of the same kind. |
| Firmware fix | Done on branch mrac-all-loops (8e1de8ff, 7f4218d8). The firmware records which fields were written, per axis; the row fills only the others; the record ends at the disarm edge. |
| Host mirror | `ground_station/platform/mrac_state.py` models the fixed firmware; 43 tests pass (`platform/tests/test_mrac_state.py`, `service/tests/test_fly_ground_verbs.py`). |

So the matched and `_3l` flights need the all-loop firmware flashed (merge, then `rebuild_and_flash --yes`). On the
main firmware they would fly bw 2.0, and a fit could leak into the next flight. **No flight dropped.**

## Checks

| Check | Result |
|---|---|
| Unit tests (`test_fly_ground_verbs`, `test_fly_file`, `test_mrac_state`, `test_mrac_ref_fit`, `test_layer_ab`, `test_commands_doc`, `test_fake_drone`) | 145 passed |
| Dry run all 20 new section-3 `.fly` scripts (`lab_e_*`, `lab_p_*`, `lab_l_*`) | 20/20 rc 0 |
| Dry run `lab_d_excite` and `lab_d_excite_yaw` | rc 0 |
| Labday dry run, `2026-10-23-fast` and `2026-10-24` (`--dry-run`) | rc 0 each (8/8 and 51/51 flights OK; 22 all-loop flights marked `REFUSED <group>_layers` until `mrac-all-loops` merge) |
| Fake-drone soak, old fast deck (`logs/soak/1024-2026-10-23-fast`) | 5/5 flights OK, 0 failures, labday rc 0; sysID flight 3536 frames, p99 7.5 ms |
| Fake-drone soak, new fast deck (`logs/soak/1024-fast-v2`) | 8/8 flights OK, 0 failures, labday rc 0; sysID flights 5721 / 5750 / 5722 frames, p99 5.0 / 7.5 / 5.0 ms, max 75 / 85 / 73 ms |
| Fake-drone soak, 9 new MRAC flights (`logs/soak/1024-mrac-new`) | 9/9 flights `OK complete`, 0 failures, labday rc 0; 43,724 frames total (4,170-8,415/flight at 200 Hz), p50 2.0 ms, p99 7.5-20.0 ms (15.0 ms overall), max 203.3 ms (1 frame >200 ms during subagent spawn), 0 dropped frames, mid-deck pack swap PASS |

## Next step before flying (P2 merge + flash)

- Merge branch `mrac-all-loops` (P2 firmware), compile Keil (`UV4`), and flash via `rebuild_and_flash --yes` +
  `livewatch verify` once `COM6` is connected.
