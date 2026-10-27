# Z CF root-cause progress

Branch: z-cf-rootcause; base: ef56fe57 (committed main).
Worktree: D:/Projects/UAV_lab/FreeRTOS-adaptive-controller-codex/.worktrees/z-cf-rootcause
User authorized GPT-6.1 medium subagents. Parent integrates disjoint owned files and commits each item.
No hardware, probe, 8081, Keil, flash, main merge or main push.
Source working-tree edits are preserved; source PID Z_pos Ki=0.001 differs from committed base 0.005. Simulation must report this distinction.
All physical model inputs without measurements are PROPOSED; host/simulation evidence is not flight evidence.

## Gates
1. Problem/falsifier: spec H1-H6 and measured four-recording oscillation; A1 falsifies stale-sample hypothesis if current correction uses newest frame.
2. Constraints: offline only, C99, runtime configuration disarmed, preserve FD/legacy parity, scoped tests and explicit path staging.
3. Anti-pattern checks: delay consistency, bounded bias/output, no new PID/adaptation architecture; verify estimator timing.
4. Coupling: ToF/accel -> estimator -> altitude position PID -> velocity PID -> motor/plant -> sensors. Bandwidth separation remains unverified on hardware.
5. Fidelity: simulator reports proposed plant/noise parameters, committed-vs-spec PID variants and sampled margin approximation.
6. V-model: host C tests, synthetic log, recorded data, simulation/parity; bench and flight deferred to operator.

## Item records
- A1 DONE | 77d45c14 | gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -ITASK API/tests/test_alt_cf.c -lm -o .agent_state/z-cf-rootcause/test_alt_cf.exe; run exe | raw .agent_state/z-cf-rootcause/A1-host.log
    A1 sine sample=38 newest=0.950 used=0.940 previous=0.940
    A1 sine sample=39 newest=0.980 used=0.950 previous=0.950
    A1 sine sample=40 newest=1.000 used=0.980 previous=0.980
    A1 baseline sine: previous-frame corrections 40/40 (expected defect)
    ALL 13 TESTS PASSED (U1-U13) (24053 checks)

- A2 DONE | 012598a9 | strict C99 host build/run; ALT_TOF_LEGACY=1 python API/tests/run_all_loop_equiv.py --only stab --base 77d45c14 | raw .agent_state/z-cf-rootcause/A2-host.log and A2-all-loop-legacy.log
    A1 sine sample=38 newest=0.950 used=0.950 expected=0.950
    A1 sine sample=39 newest=0.980 used=0.980 expected=0.980
    A1 sine sample=40 newest=1.000 used=1.000 expected=1.000
    A1 fixed sine: newest-frame corrections 40/40
    ALL ALT_CF TESTS PASSED (U1-U13, A1-A2) (24058 checks)
    Legacy whole-loop equivalence OK, two seeds / 20000 ticks. Default DIFFER is intentional A2 behavior, retained in A2-all-loop.log. Median is outlier reference; literal median fusion would violate newest-k acceptance.

- B1 DONE | 8e275ef0 | python -m pytest tools/test_analyze_z_cf_b1.py ground_station/lab/tests/test_commands_doc.py -q | .agent_state/z-cf-rootcause/B1-integration.log
    ......                                                                   [100%]
    6 passed in 7.65s
    231115: selected-bin period=9.347s (right-censored slow peak) mode=[0.0] h lag=0.040s vz lag=0.120s bias/throttle r=0.258
    231338: selected-bin period=3.505s mode=[1.0] h lag=0.160s vz lag=0.160s bias/throttle r=0.413
    231601: selected-bin period=4.673s mode=[1.0] h lag=0.160s vz lag=0.120s bias/throttle r=-0.075
    231738: selected-bin period=9.360s (right-censored slow peak) mode=[0.0] h lag=0.040s vz lag=0.200s bias/throttle r=0.559
    B1: four measured recordings analyzed; no original log writes
    B1 finding: slow variation dominates; scaled peaks 3.505/4.673s, legacy lowest search bin unresolved. Relative h lag40-160ms, vz120-200ms. H4 investigation supported, not proven.
    H6: Ano_OF.c decoder increments once per valid altitude UART frame, not periodic sensor task; measured arrival66.96/69.14/70.82/70.83Hz and visible range changes7.29/10.32/8.75/8.31Hz. Sensor conversion rate unknown.

- A3 DONE | c9b651bb | strict C99 host build/run; python API/tests/run_all_loop_equiv.py --only stab --base 012598a9 | raw .agent_state/z-cf-rootcause/A3-host.log and A3-all-loop.log
    A1 sine sample=40 newest=1.000 used=1.000 expected=1.000
    A1 fixed sine: newest-frame corrections 40/40
    A3 PROPOSED 40ms sine d=0: h phase=-2.8917 deg vz phase=3.6931 deg (host)
    A3 PROPOSED 40ms sine d=8: h phase=1.2792 deg vz phase=0.7715 deg (host)
    ALL ALT_CF TESTS PASSED (U1-U13, A1-A3) (24257 checks)
    Default delay0 whole-loop equivalence OK. Host 1Hz PROPOSED40ms delay d8 phase h+1.2792deg,vz+0.7715deg; d0 h-2.8917deg,vz+3.6931deg (velocity leads; not called lag).

- 2026-10-27 interruption: firmware A4, A5/A6 and C4 agents hit usage limit. User said continue; each dispatched exactly one retry. Existing files and completed test evidence retained. C1-C3 not started until A4 green.

- C4 DONE | c573c7e8 | python -m pytest ground_station/lab/tests/test_cf_vz_check.py -q | raw .agent_state/z-cf-rootcause/c4-pytest.log
    ....................                                                     [100%]
    20 passed in 39.99s

- A4 DONE | 62bf517c | strict C99 host gcc/run; python API/tests/run_all_loop_equiv.py --only stab --base c9b651bb | raw .agent_state/z-cf-rootcause/A4-host.log
    A3 PROPOSED 40ms sine d=8: h phase=1.2792 deg vz phase=0.7715 deg (host)
    A4 hover spread=0 max dh=0.00256574 m max dv=0.00554181 m/s final h=1.00000000 v=0.00000899 (host)
    A4 hover spread=1 max dh=0.00028616 m max dv=0.00056076 m/s final h=1.00000048 v=0.00000597 (host)
    A4 packets early/late drain; bias clamp; peak=20/20; sizeof packets=320 bytes (host)
    ALL ALT_CF TESTS PASSED (U1-U13, A1-A4) (24398 checks)
    A4 defaultoff whole-loop equivalence OK; 20x32 history update loop cost unmeasured on target. Host packet allocation320B + state history384B. Effective modes published.

- A5 DONE | 088065d4 | python -m pytest ground_station/lab/tests/test_z_cf_sim.py -q | raw docs/lab/data/z-cf-rootcause-A5/pytest.txt
    .......                                                                  [100%]
    7 passed in 4.61s
    Primary 180 scenarios + factor30 + cadence8. PROPOSED plant; no CF reproduction of +/-10-15cm wobble, max CF halfPP2.59cm. FD100ms high-lag cases outside model domain; not physical predicted amplitude. Full tables docs/lab/z-cf-rootcause-A5.md and data/sweep.csv.

- C1 DONE | f8b53f77 | strict C99 host gcc/run | .agent_state/z-cf-rootcause/C1-host.log
    A3 PROPOSED 40ms sine d=8: h phase=1.2792 deg vz phase=0.7715 deg (host)
    A4 hover spread=0 max dh=0.00256574 m max dv=0.00554181 m/s final h=1.00000000 v=0.00000899 (host)
    A4 hover spread=1 max dh=0.00028616 m max dv=0.00056076 m/s final h=1.00000048 v=0.00000597 (host)
    A4 packets early/late drain; bias clamp; peak=20/20; sizeof packets=320 bytes (host)
    ALL ALT_CF TESTS PASSED (U1-U13, A1-A4) (24448 checks)
    C1 bench state isolated from controller FB, diagnostic toggle/arm reset state and bias; flag clears on arm.

- A6 DONE | 7204e4eb | evidence paragraph checked against A5 saved CSV; no code change, no extra test required
    A5 scoped evidence: 7 passed in4.61s. KF0.42 vsCF1.26cm RMS in PROPOSED50ms/40ms scenario; PM66.53 vs66.36deg, no meaningful margin gain; KF candidate only after measurements.

- C4 review integrated | 43242837 | python -m pytest ground_station/lab/tests/test_cf_vz_check.py -q | raw .agent_state/z-cf-rootcause/c4-review-pytest.log
    ....................                                                     [100%]
    20 passed in 32.61s
    replay_faithful=false without effective mode evidence; separate caveated match flag. A5 report normalized from Windows cp1252 to UTF8, no numbers changed.

## Estimator x delay table (A5 primary PID, PROPOSED 40 ms motor)

All values are simulation outputs with PROPOSED plant/noise assumptions, not flight measurements. Upper GM `--` means no downward phase crossing in the scanned band; it is not an infinite-margin claim. Period 10.00 s is the lower frequency boundary of a 10 s record, not an identified sustained oscillator. OOB means trajectory left the model domain; nonlinear metrics are omitted. Settle `>25` means not reached within 25 s after the step.

| Estimator | Delay ms | PM deg | Upper GM dB | Overshoot cm | Settle s | Hover RMS cm | Spectral period s |
|---|---:|---:|---:|---:|---:|---:|---:|
| fd_lpf | 0 | 34.22 | 7.40 | 2.96 | >25 | 0.27 | 0.71 |
| fd_lpf | 25 | 23.59 | 4.29 | 3.06 | 24.18 | 0.37 | 1.00 |
| fd_lpf | 50 | 12.95 | 2.08 | 3.63 | 24.95 | 0.50 | 0.83 |
| fd_lpf | 75 | 2.31 | 0.34 | 4.69 | 24.92 | 0.98 | 0.83 |
| fd_lpf | 100 | -8.33 | -1.11 | OOB | OOB | OOB | OOB |
| current_cf | 0 | 71.32 | -- | 3.49 | 24.19 | 1.12 | 10.00 |
| current_cf | 25 | 73.99 | -- | 3.44 | 24.09 | 1.12 | 10.00 |
| current_cf | 50 | 76.44 | -- | 3.40 | >25 | 1.16 | 10.00 |
| current_cf | 75 | 78.38 | -- | 3.60 | 24.04 | 1.14 | 10.00 |
| current_cf | 100 | 79.52 | -- | 3.38 | 24.05 | 1.16 | 10.00 |
| cf_a2 | 0 | 66.33 | -- | 3.46 | 24.09 | 1.14 | 10.00 |
| cf_a2 | 25 | 68.71 | -- | 3.51 | 24.05 | 1.15 | 10.00 |
| cf_a2 | 50 | 71.32 | -- | 3.43 | 24.09 | 1.14 | 10.00 |
| cf_a2 | 75 | 73.99 | -- | 3.30 | 24.09 | 1.14 | 10.00 |
| cf_a2 | 100 | 76.44 | -- | 3.34 | 24.05 | 1.15 | 10.00 |
| cf_a2_a3 | 0 | 66.33 | -- | 3.46 | 24.09 | 1.14 | 10.00 |
| cf_a2_a3 | 25 | 66.35 | -- | 3.55 | 24.06 | 1.19 | 10.00 |
| cf_a2_a3 | 50 | 66.36 | -- | 3.68 | 24.09 | 1.26 | 10.00 |
| cf_a2_a3 | 75 | 66.37 | -- | 3.73 | 24.13 | 1.31 | 10.00 |
| cf_a2_a3 | 100 | 66.36 | -- | 3.76 | 24.30 | 1.41 | 10.00 |
| cf_a2_a3_a4 | 0 | 66.35 | -- | 3.65 | 24.12 | 1.19 | 10.00 |
| cf_a2_a3_a4 | 25 | 66.36 | -- | 3.67 | 24.23 | 1.24 | 10.00 |
| cf_a2_a3_a4 | 50 | 66.37 | -- | 3.75 | 24.30 | 1.32 | 10.00 |
| cf_a2_a3_a4 | 75 | 66.36 | -- | 3.90 | 24.36 | 1.40 | 10.00 |
| cf_a2_a3_a4 | 100 | 66.35 | -- | 3.88 | 24.20 | 1.49 | 10.00 |
| delayed_kf | 0 | 66.45 | -- | 2.78 | >25 | 0.37 | 3.33 |
| delayed_kf | 25 | 66.50 | -- | 2.80 | >25 | 0.40 | 3.33 |
| delayed_kf | 50 | 66.53 | -- | 2.78 | >25 | 0.42 | 2.00 |
| delayed_kf | 75 | 66.56 | -- | 2.73 | >25 | 0.43 | 2.50 |
| delayed_kf | 100 | 66.56 | -- | 2.90 | >25 | 0.48 | 2.50 |

- C2 DONE | df7199a5 | python -m pytest ground_station/livewatch/tests/test_campaign_capture.py ground_station/platform/tests/test_wfb_var.py ground_station/platform/tests/test_alt_cf_var_guard.py ground_station/service/tests/test_campaign_outputs.py ground_station/lab/tests/test_commands_doc.py -q; python tools/row_meta.py TASK/AutoflyTask.c | raw .agent_state/z-cf-rootcause/C2-tests.log C2-row-meta.log C2-budget.log
    C2-tests.log:
    ...s.................................................................... [ 34%]
    ........................................................................ [ 69%]
    ................................................................         [100%]
    207 passed, 1 skipped in 20.14s
    C2-row-meta.log:
    row-meta: 1 tables, 201 cells in range, 0 error(s)
    C2-budget.log:
    calculator REJECT CaptureError CF single-slot needs 70600 B/s, budget 70042; no variables were removed or slowed
    new-primary vars= 100 payload= 317 ranges= 47 calculated200Hz_Bps= 65800 planningcap= 70041.6 UARTactual_Bps= 91304.3 UARTutil_pct= 72.07
    All promoted slow groups are included above; additional slow-slot B/s = 0; slots1..3 stopped.
    calculator PASS request_bytes= 386
    Host calculation only: old fields resolved from existing AXF; new 4 uint8 cells use header/source widths and provisional addresses; new AXF preview/range fit and actual200Hz cadence UNVERIFIED.
    C2 projected wire budget PASS after omitting camera fields. New image layout/physical cadence UNVERIFIED; current ELF plus PROPOSED legal placeholder addresses for four new uint8 cells. New permanent rows 60-63 are guarded by both armed states. Legacy card requires no new symbols.

- C3 DONE | 1abbb602 | host doc/deck validation; labday --list | raw .agent_state/z-cf-rootcause/C3-doc-check.log C3-deck-list.log
    C3-doc-check.log:
    C3 PASS: parsed deck byte-equivalent after canonical JSON; all 39 planned flights/order/settings preserved
    C3 PASS: 16 resume command/path checks; UTF8 docs; old hand test and Z-step mismatch explicit BLOCKED
    C3 host doc validation only: no bench, flash, hardware or flight evidence
    C3-deck-list.log:
      model-based-adrc                   .\fly lab_e_model_adrc --once --gains D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.worktrees\z-cf-rootcause\logs\lab\2026-10-27\2026-10-27-gains.json -
      model-pid-figure8-full-speed       .\fly lab_e_model_pid_full --once --gains D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.worktrees\z-cf-rootcause\logs\lab\2026-10-27\2026-10-27-gains.json -
      model-lqr-figure8-full-speed       .\fly lab_e_model_lqr_full --once --gains D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.worktrees\z-cf-rootcause\logs\lab\2026-10-27\2026-10-27-gains.json -
      model-mpc-figure8-full-speed       .\fly lab_e_model_mpc_full --once --gains D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.worktrees\z-cf-rootcause\logs\lab\2026-10-27\2026-10-27-gains.json -
      model-adrc-figure8-full-speed      .\fly lab_e_model_adrc_full --once --gains D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.worktrees\z-cf-rootcause\logs\lab\2026-10-27\2026-10-27-gains.json -
    C3 documentation complete; future old-image motors-off estimator comparison and requested 0.8->1.0m step remain explicit CEO prerequisites. Parsed deck preserves all 39 flights/order/settings. No hardware was run.

## Final integration

All five requested GPT-6.1 medium agents returned final results and their changes are integrated into this branch. Usage-limit interruptions were retried once and recovered. No item failed or was skipped as an offline deliverable; unperformed hardware acceptance and the two C3 prerequisites remain explicit below.

- Integration repair DONE | 660303b4 | python -m pytest ground_station/lab/tests/test_cf_abba.py ground_station/lab/tests/test_commands_doc.py -q | raw .agent_state/z-cf-rootcause/final-repair-tests.log
    ...... [100%]
    6 passed in 1.10s
    Initial combined run: 3 failed, 239 passed, 1 skipped in46.20s. Failures were a double-quote-only flag checker and global ABBA command counts; fixed checker + regression and labelled-command assertions, without changing valid CLI/docs.

Final scoped commands:
- gcc -std=c99 -Wall -Wextra -Werror -Wdeclaration-after-statement -Wvla -ITASK API/tests/test_alt_cf.c -lm -o .agent_state/z-cf-rootcause/final-test-alt-cf.exe; run exe
- python API/tests/run_all_loop_equiv.py --only stab --base 62bf517c
- python -m pytest ground_station/lab/tests/test_z_cf_sim.py ground_station/lab/tests/test_cf_vz_check.py ground_station/lab/tests/test_cf_abba.py ground_station/livewatch/tests/test_campaign_capture.py ground_station/platform/tests/test_wfb_var.py ground_station/platform/tests/test_alt_cf_var_guard.py ground_station/service/tests/test_campaign_outputs.py tools/test_analyze_z_cf_b1.py ground_station/lab/tests/test_commands_doc.py -q -rs
- python tools/row_meta.py TASK/AutoflyTask.c
- python -m ground_station.flashtool.compile_commands (database generation only, no build/flash)

final-host.log last five output lines:

```text
A3 PROPOSED 40ms sine d=8: h phase=1.2792 deg vz phase=0.7715 deg (host)
A4 hover spread=0 max dh=0.00256574 m max dv=0.00554181 m/s final h=1.00000000 v=0.00000899 (host)
A4 hover spread=1 max dh=0.00028616 m max dv=0.00056076 m/s final h=1.00000048 v=0.00000597 (host)
A4 packets early/late drain; bias clamp; peak=20/20; sizeof packets=320 bytes (host)
ALL ALT_CF TESTS PASSED (U1-U13, A1-A4) (24448 checks)
```

final-stab-equiv.log last five output lines:

```text
all-loop equivalence: base 62bf517c
OK   stab: seed 1: same (tick 20000 hash 74cf0c4f8e994ebf calls 942658); seed 2: same (tick 20000 hash b3ed761ad0b866bc calls 952093)
ALL-LOOP EQUIV OK
```

final-pytest-repaired.log last five output lines:

```text
........................................................................ [ 88%]
.............................                                            [100%]
=========================== short test summary info ===========================
SKIPPED [1] ground_station\livewatch\tests\test_campaign_capture.py:58: needs the built firmware OBJ/JX_FLY.axf
244 passed, 1 skipped in 46.64s
```

final-row-meta.log last five output lines:

```text
row-meta: 1 tables, 201 cells in range, 0 error(s)
```

final-compile-commands.log last five output lines:

```text
wrote 113 entries (0 missing) -> D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.worktrees\z-cf-rootcause\compile_commands.json
```

The sole pytest skip needs built OBJ/JX_FLY.axf. Keil was expressly unavailable, so that image-dependent test remains skipped. Final pytest scope was confined to changed files and their directly affected harnesses. No whole-repository pytest or routed broad gate was run.

## Findings and limits

- H1 proven: baseline correction used previous frame in40/40 ramp/sine arrivals. A2 uses newest accepted inlier on arrival. Literal sample-median fusion would still delay monotonic signals, so sample median is an outlier reference. A3 defaultdelay0 and A4 defaultoff preserve A2 behavior; legacy sample mode preserves baseline. New settings latch disarmed.
- A3 host PROPOSED40ms/1Hz: matching d8 yields h+1.2792deg and vz+0.7715deg phase; d0 h-2.8917deg/vz+3.6931deg. This is synthetic timing evidence, not measured sensor latency.
- A4 host spreading reduces maximum hover height/velocity steps by about9x with equal steady state. Its target execution cost and real RAM headroom remain unmeasured; default off. Re-derived poles -1.9752204+/-2.0279634j and -0.2495592, wn2.8309241,zeta0.6977299,bias tau4.0070656s.
- A5: no tested CF factor reproduces reported +/-10..15cm wobble. Largest simulated CF half peak-to-peak2.59cm; PROPOSED70Hzframe/held20Hz sensitivity RMS1.07..1.27cm. FD100ms cases outside model domain are not physical amplitude predictions. A5 table above; all180 cases andfactor/cadence tables in docs/lab/data/z-cf-rootcause-A5/. No measured mass/thrust/latency identified; margins use averaged fundamental response, not exact lifted multirate stability.
- A6: at PROPOSED50ms range/40ms motor, KF RMS0.42 vs corrected CF1.26cm, similar PM66.53/66.36deg; KF misses4mm settle within25s. Candidate comparison after identification; no blanket replacement recommendation.
- B1 measured scaled-run peaks3.505/4.673s; second is window-sensitive. Legacy selected9.35s peaks are search-boundary/right-censored slow drift, not identified9.35s oscillators. CF-height relative lags40/160/160/40ms and velocity relative lags120/160/120/200ms. Slow branch suggests investigating H4, without proving causation or excluding H1/H2/H5. Bias/throttle correlations vary and are closed-loop confounded. Measured25Hz telemetry cannot resolve individual5ms corrections or independent motion/sensor latency. Full report/plot: docs/lab/z-cf-rootcause-B1.md.
- H6 provenance: API/Ano_OF.c valid ANOOF_FN_ALT decoder increments alt_update_cnt in the USART2 IRQ path once per accepted UART altitude frame, including unchanged centimetres. There is no periodic sensor task driving it. Four measured frame rates66.96/69.14/70.82/70.83Hz vs visible range-change rates7.29/10.32/8.75/8.31Hz. Sensor conversion rate remains unknown; a repeated quantized value is not proof of an old conversion.
- C1 diagnostic runs motors-off estimator only, never supplies controller feedback, clears on arm, resets history/bias on transitions. C2 permanent IDs60..63 check both arm states. C2 calculated new capture317payload+12overhead at200Hz=65800B/s, old325+12=67400B/s; camera retained would70600B/s and calculator rejected. New addresses were PROPOSED placeholders; actual new image/range packing/typed preview and physical cadence unverified.
- C3 preserves all39 planned flights. Old-image C1 unavailable and existing1.0->1.4/0.6m velocity-card mismatch to requested0.8->1.0m are explicit future CEO prerequisites. Delay cross-correlation alone includes filter lag and cannot certify sensor latency.
- C4 synthetic test passes; effective nonzero delay/spread and missing effective modes cannot certify baseline replay. Predictor residuals at sparse25Hz are interframe changes, not isolated correction steps.

## CEO open questions

1. What documented motors-off comparison replaces the unavailable old-image C1 test, or is that comparison explicitly waived?
2. What same-clock motion/acceleration or independent reference will identify sensor delay and uncertainty separately from CF dynamics? Retain d0 until justified.
3. Approve a separately reviewed0.8->1.0m campaign amendment while retaining existing velocity legs?
4. Confirm identical runtime Ki0.001 for the comparisons versus committed default0.005, and prioritize H4 acceleration/vibration/bias evidence before any gain retune or KF selection.
5. Can reviewed main-side build/identity, actual capture preview/cadence, and target A4 execution/memory cost be checked before future use? None was performed here.

## Commits (offline items and review repairs)

77d45c14 test(altitude): A1 reproduce stale ToF innovation on host
012598a9 fix(altitude): A2 fuse newest accepted ToF frame with sample outlier gate
8e275ef0 analysis(altitude): B1 measure slow hover variation and telemetry cadence
c9b651bb fix(altitude): A3 align delayed innovations with consistent state history
c573c7e8 analysis(altitude): C4 report lag periods and correction residuals safely
62bf517c feat(altitude): A4 optionally spread history-consistent corrections across ticks
088065d4 sim(altitude): A5 compare estimator delay and correction cadence offline
f8b53f77 feat(altitude): C1 add isolated disarmed estimator hand-test mode
7204e4eb docs(altitude): A6 assess filter choice against proposed-model evidence
43242837 fix(analysis): require effective timing evidence before certifying replay
df7199a5 feat(capture): C2 budget coherent root-cause telemetry and preserve old-image capture
1abbb602 docs(altitude): C3 order estimator bench and flight comparisons with explicit prerequisites
660303b4 test(lab): validate quoted flags and labelled ABBA commands

The final evidence/handoff commit follows these item commits. Feature-branch publication uses normal hooks only; never main, no bypass and no expansion to the prohibited broad test gate.

## Publication result

Evidence/handoff commit: 7976f9bb. Normal feature-branch push (`git -c credential.interactive=never push -u origin z-cf-rootcause`) was rejected by the repository pre-push guard; no main push or merge was attempted. Raw log: .agent_state/z-cf-rootcause/feature-push.log.

```text
agent_push_guard: tier-0 flight-critical files in the diff without operator approval:
  - API/ekf_of.c
  - API/land_profile.h
  - API/mrac.c
  - API/pid.c
  - API/wfb_safety.c
Approval is a committed line in docs/agent/streams/z-cf-rootcause.md:
  Tier0-Approved: <file>, <file> - operator <YYYY-MM-DD> "<the operator's words>"
error: failed to push some refs to 'https://github.com/YoussefChouj/FreeRTOS-adaptive-controller-codex.git'
```

The guard compares origin/main...HEAD. Required local-main base ef56fe57 already contains196 commits beyond origin/main; the five flagged tier-0 files are inherited from that base and were not edited by this task. The branch has382 changed paths against origin/main before final evidence files. Do not manufacture approval, widen unrelated scope, bypass the guard or run the prohibited broad gate to force publication. Feature branch remains local for authorized integration/review. After the inherited authorization problem, full routed-gate coverage would also need resolution under a separate authorization because this brief permits scoped tests only.

Offline work and tests are complete; publication is blocked. This final publication-record commit follows7976f9bb and the item commits listed above.
