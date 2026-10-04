# Overnight run 2026-10-04/05: summary (CEO inline, no workers)

## Bottom line

Ready for the 2026-10-06 payload and waypoint demo, with one hard first step: flash HEAD. The drone still runs the
2026-10-03 image. HEAD has never been built by Keil after the WP-43 layout commits and has never flown.

| Item | State |
|---|---|
| First action at the lab | probe in, stop the 8081 service, `python -m ground_station.flashtool.rebuild_and_flash --yes`, restart 8081, check identity |
| Lab plan | `docs/agent/lab-2026-10-06.md`: shakedown, weigh, 9 flights (no load, sym, asym x hover/lawnmower/zigzag) |
| Campaign | `ground_station/service/campaigns/payload_waypoints.yaml` (PID, workflow B launch) |
| Debrief | workflow C after every flight (`.claude/skills/workflow-c/SKILL.md`) |
| Ground tests | 162 scoped tests pass |
| Payload limit | PROPOSED from the bench curve: up to ~12 % no sag, 40 % saturates altitude (do not fly) |
| RAM | SRAM 95 % used, ~5.7 KB free. Savings found (below), all PROPOSED for after the demo |

## Commits

| Commit | What |
|---|---|
| 041a970 | workflow C: per-flight debrief, plots, recommendations, next flight |
| 0ac7c6f..a78b403 | WP-43: GPS, sys, tf_mini_plus, fw_identity, delay, Ano_OF to the pid.c standard (FW-EQUIV OK / token compare) |
| 794cdfc | firmware coding standard (PX4-mapped) + `fw_lint.py` ratchet in `tools/check.sh` |
| 33c430b, 01de847 | lab 2026-10-06 readiness doc + HANDOFF pointer |
| c313a1a | flashtool preflight: pyOCD probe check no longer hangs with no probe (`blocking=False`); lab Step 0 stops 8081 |
| c3b4780 | `docs/firmware-ram-budget.md` (map-measured) |
| 20ce10e | WP-43 report: 5 PROPOSED behaviour findings (tf_mini_plus temp_sum, cm/mm, UART4 RX, SDK state bound, dead AnoOF_Check_State) |

## RAM savings (PROPOSED, after the demo)

| # | Change | Saves | Risk |
|---|---|---|---|
| R1 | FreeRTOS heap (`ucHeap`) to CCM | 20,480 B SRAM | low: DMA audit found all DMA buffers static, no stack-resident DMA |
| R2 | running-sum least squares in the calibration file | ~79,900 B | protected file, operator sign-off |
| R3 | drop `Num_offset` only | 20,000 B | protected file, operator sign-off |

## Not done tonight

- Keil build of HEAD: no probe was connected (preflight refuses) and 8081 held the axf. First thing at the lab.
- 19 firmware files still without an `@module` header (`tools/fw_lint_allow.txt`); 3 protected.
