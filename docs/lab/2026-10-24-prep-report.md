# Lab prep report, 2026-10-24 (P1 host lab prep)

Spec: `docs/design/2026-10-24-lab-prep-mrac-flights.md`. This is report v1: it covers the sysID placement (spec step
6) and the ground-write refuse rule (step 5). The new MRAC scripts and Gate W are still open (last section).

## Deck order

| Order | Deck | Camera mode | First flight |
|---|---|---|---|
| 1 | `.\labday 2026-10-23-fast` | 2 | smoke-hover-mode2 (lab_s_smoke) |
| 2 | `.\labday 2026-10-23` | 3 | smoke-hover (lab_s_smoke) |

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

- Rate 100 Hz, the ceiling. The firmware telemetry send task runs at 100 Hz (`SEND_TASK_HZ`; a log plan above it is
  refused, `campaign_capture.py:351`). 100 Hz is 6.7x the 15 Hz top line. The live link probe
  (`probe_max_rate`, ladder 200/100/50/25) may still lower it if the link loses frames. A drop below 100 Hz is a
  link fault to find, not a setting to accept.
- Signals: the core set has the gyro PID `.FB` / `.Des` (rate feedback and reference) and the motors. Both excite
  scripts now also log the `thrust_model` group, which carries `u_gyrox` / `u_gyroy` / `u_gyroz`, the rate PID output
  (Uc for the frequency response). Before this change the sysID flight did not log Uc.

## MRAC during sysID

No verb freezes MRAC learning. With `mrac_injection=0` the MRAC output is never applied, so the excite data is
clean. Learning still runs in shadow (`API/mrac.c` l.250-313: `learn_gate = basic_gate && !freeze_shadow`;
adaptation at l.1293). The shadow estimates after a sysID flight have seen the excite, so do not use them as a prior.
Possible later host/firmware item: a takeoff option that sets `freeze_shadow`.

## Ground writes: no flight dropped

A flight whose firmware field is missing gets a `refuse` ground write (`ground_plan.refusing_write`, commit
a120308e). The deck still parses and runs. The runner refuses only that flight, before arming, with the field in
the label. The dry run prints `ground : REFUSED <verb>: <field>`. **No flight dropped.**

## Checks

| Check | Result |
|---|---|
| Dry run lab_d_excite | rc 0, 7 steps, 2 excites |
| Dry run lab_d_excite_yaw | rc 0, 2 excites |
| Fake-drone soak, old fast deck (`logs/soak/1024-2026-10-23-fast`) | 5/5 flights OK, 0 failures, labday rc 0; sysID flight 3536 frames, p99 7.5 ms |
| Fake-drone soak, new fast deck (`logs/soak/1024-fast-v2`) | 8/8 flights OK, 0 failures, labday rc 0; sysID flights 5721 / 5750 / 5722 frames, p99 5.0 / 7.5 / 5.0 ms, max 75 / 85 / 73 ms |
| Labday dry run, both decks (`--dry-run`) | rc 0 each, every flight OK |

## Still open (P1)

- New MRAC scripts from the spec: `lab_e_xyl_inner/outer/both`, `lab_e_exp_*_matched`, `lab_e_exp_*_3l`,
  `lab_p_prior_cold/warm`, `lab_p_pattern_learn`, `lab_l_att_*` / `lab_l_z_*` / `lab_l_yaw_*` / `lab_l_all_*`.
  None exist yet.
- Gate W (write order, spec l.47): must pass before any matched flight goes into a deck.
- The 2026-10-24 deck (spec sec. 4, a copy of 10-23 plus the new flights) and its soak.
