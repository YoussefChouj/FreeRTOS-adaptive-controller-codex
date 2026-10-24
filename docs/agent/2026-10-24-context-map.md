# Context map for the 2026-10-24 build agents

Read this before anything else. It says what to read, which lines, and what NOT to read. Line numbers are from
main at f29459cd (2026-10-24). If a line moved, grep the symbol, then read about 40 lines around it.

## Rules for every agent

- Never read a file over 20 KB whole. Use grep (`git grep -n "<symbol>" -- <path>`), then read the range
  with offset/limit.
- Big files: API/mrac.c 121 KB, TASK/StabilizerTask.c 148 KB, ground_station/service/campaign_fly.py 85 KB,
  ground_station/service/fake_drone.py 45 KB, ground_station/platform/firmware_contract.py 38 KB.
- Never search the whole tree. `logs/` holds about 43,000 files and `.cache/` holds copies of the C sources.
  Use `git grep` (tracked files only) or `ls` on one folder.
- Skip always: `.cache/`, `OBJ/`, `logs/` (except the folders your spec names), `docs/lab/days/*.json`,
  `docs/research/` (except the files your spec names), `node_modules/`, `.agent-ops/`, `.agent_state/`.
- AGENTS.md: read it once. `docs/agent/HANDOFF.md`: skip it; your spec replaces it.
- Stop gathering context once you can name the function you will change and the test you will copy.
  Then start the item.

## Firmware MRAC agent (../fc-mrac-all-loops)

| Read | Lines | Why |
|---|---|---|
| API/mrac.h | 300-340 | `MRAC_VF_` enum (REF_TYPE 303, REF_DELAY_S 304, REF_MODEL_BW 315, COUNT 335): add REF_ZETA here |
| API/mrac.h | 520-600 | prior dispatch 526, `MRAC_XyRefSelect` 582, `mrac_xy_layers` 591, `MRAC_PosStep` 596 |
| API/mrac.c | 85-225 | `mrac_xy_ref_rows` 90, `MRAC_XyRefSelect` 99, `MRAC_XyLayersLatch` 146, `MRAC_PosStep` 155: the template for every new outer layer |
| API/mrac.c | 1855-1910 | EQUIV comment, boot latch call 1906 |
| API/mrac.c | 2105-2200 | `MRAC_VariantParamSet` (switch 2125-2191): item 0 write order |
| API/mrac.c | 2265-2280, 2415-2440 | GateStep call, `MRAC_PriorWarmStart` (item 5) |
| TASK/StabilizerTask.c | 1985-2000, 2255-2360 | xy_ctrl comment, `MRAC_PosStep` call 2263-2269, setpoint adds 2334, excite injection 2351-2356 |
| API/tests/ | test_mrac_pos_layer.c, test_mrac_equiv.c, run_mrac_equiv.py | copy these; do not read the other tests |
| ground_station/platform/firmware_contract.py | 175-205, 550-570 | MRAC_PRIOR 0x2F block; CMD 0x1D field enum 0..31 |
| ground_station/service/fake_drone.py | 25-40, 190-210, 260-270 | PARAM_CMDS, mrac_flags, the CMD handler |
| ground_station/analysis/mrac_active.py | whole (11 KB) | add the new axes |
| `git show d0f29927 --stat`, then the diff of API/mrac.c only | | the xy outer layer commit: copy its structure |

Skip: the rest of mrac.c (adaptive laws you do not change), the rest of StabilizerTask.c, campaign_fly.py,
campaigns/, decks, all estimator files (API/ekf*, calib*, gyro_filter*), logs except 2026-10-21 setpoint ranges
for the clamp rows (one campaign folder is enough; read its log_plan.json first to find the column).

## Host agent (main checkout)

| Read | Lines | Why |
|---|---|---|
| docs/design/2026-10-24-lab-prep-mrac-flights.md | whole | your spec |
| ground_station/service/campaign_fly.py | 1095-1150 | `mrac_prior` 1102, `mrac_variant` 1112, `mrac_xy_layers` 1133: copy for the new verbs |
| ground_station/platform/firmware_contract.py | 550-570 | read field numbers from here; never hard-code them |
| ground_station/scenario/scenario_schema.py | 75-130 | EXCITE_AMP_MAX_DPS 81, `_compile_excite` 118 |
| campaigns/lab_d_excite.fly and one lab_e_*.fly | whole (small) | script format |
| docs/lab/days/2026-10-21.yaml | whole | deck format |

Skip: API/ and TASK/ (firmware, other agent), fake_drone.py, mrac_active.py, the rest of campaign_fly.py, logs/.

## Estimator agent (../fc-estimator)

Step 0 of its spec is the map, so it reads only by grep: `git grep -n -i -E "still|bias|temp|notch|derot|delay"
-- API/calib.c API/ekf_of.c API/gyro_filter.c API/imu_update.c API/ekf.c`, then about 40 lines around each hit.
Skip: API/mrac*, TASK/StabilizerTask.c beyond the estimator call sites, ground_station/ except its own section
of firmware_contract.py.

## Drift NN agent (../fc-driftnn)

| Read | Why |
|---|---|
| docs/research/2026-10-24-drift-nn-v2-spec.md | your spec |
| docs/research/2026-10-13-sota-extpos-nn-plot.md, lines 140-180 | the 10-13 result (15.7 cm train vs 260 cm held-out) |
| ground_station/analysis/nn_io.py | existing loader; reuse it |
| tools/kaggle_nnio/ (run.py, stage.sh, sweep.py, kernel-metadata.json) | the Kaggle lane |

Skip: all firmware, campaign code, the old D:/Projects/UAV_lab/FreeRTOS-adaptive-controller-codex-driftnn
worktree unless the 10-13 doc points to a file in it.

## Sysid agent (../fc-sysid)

| Read | Why |
|---|---|
| docs/design/2026-10-24-sysid-excitation.md | your spec |
| API/sysid.c | whole (small): signals, ramps, amplitude cap, z band |
| ground_station/livetune/loop.py, lines 1-120 | the livetune excitation defaults |
| ground_station/scenario/scenario_schema.py, lines 75-130 | the deck excite cap |
| TASK/StabilizerTask.c, lines 2340-2360 | where the signal is injected |
| campaigns/lab_d_excite.fly | the flight that was never flown |
| logs/campaigns/lab-d-livetune-*_20261021-*/log_plan.json, then the CSVs it names | what was logged and at what rate |

Skip: API/mrac*, estimator files, the rest of StabilizerTask.c, every other logs/ folder.
