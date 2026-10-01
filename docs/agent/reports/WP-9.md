Status: PARTIAL (code + tests gate-green; the doc has gaps; the CLI run on logs/vofa was not run by the manager)
Commits (on wp/9): fc68113 - worker wp9-r1 (agy/gemini-3.1-pro-high, OK); 1d1bd96 - worker wp9-r2 (agy/gemini-3.1-pro-high, OK);
  ca83204 - worker wp9-r3 (agy/gemini-3.1-pro-high, OK); plus this report commit
Gate: GATE PASS   (run with --max-lines 900: the 200-line default cannot fit a 563-line analysis CLI)
Verification (run by manager):
- gate pytest step -> "PASS pytest: 6 passed in 2.10s"; gate ruff -> "PASS ruff: 2 files clean"
- `git diff --stat night/2026-10-02...HEAD` -> "6 files changed, 807 insertions(+)" (all allow-listed)
- two-dot `git diff --stat night/2026-10-02` also shows briefs WP-11/12/15 + night plan: base moved on after wp/9 was cut.
- acceptance pytest + CLI on logs/vofa -> denied ("requires approval"). CEO: run both. Flight coverage and
  runtime are worker claims only (r1 digest: real 0m4.4s on VPS).
Worker rounds: 3/3, lane agy/gemini-3.1-pro-high every round
- r1 bounce: ruff F841; scipy import; `except Exception: pass`; pitch need compared with +gyroyPID.U; D-term
  hard-coded 0; T4 put the push in place of Ui_pos; T5 gains with no source; doc said "all facts reproduced" while
  shadow10 Des-FB came out 1.61.
- r2 bounce: ruff E702 (digest falsely said ruff passed); non-hover evidence never printed (name match bug);
  X/Y axis mapping guessed; Y-axis prediction had the wrong sign (meas +3.7, pred -7.1); angle-loop SumEMax sized
  in ticks (7850), though the angle loop outputs deg/s; shadow10 Des-FB 0.8 (bfill across slots).
Worker numbers (script output, worker-reported, not re-run by the manager):
| item | value |
|---|---|
| shadow10 roll Des-FB | 1.86 deg (CEO 1.92); r1 1.61 and r2 0.8 came from cross-slot fill |
| pitch need shadow10/14/4/active15 | -20.2 / -13.8 / -5.4 / -14.2 (matches CEO) |
| active15 | MRAC 32.0 ticks, gyroxPID.U 0.9 |
| 3ae4a23 roll | worst need 45.1, mean 27.9, angle Ui at cap 37% of hover |
| T4 pos err X / Y | measured -5.6 / +3.7 cm; predicted A+B -6.8 / +4.8 cm |
| trim roll / pitch | -1.24 (std 0.23, -1.59..-0.87) / -0.90 (std 0.18, -1.17..-0.69) deg |
| WP-13 gyro | Ui 157 ticks (3 x worst need 52.3) -> SumEMax 15701 at Ki 0.01; UiMax must also go above 157 (now 20) |
| WP-13 angle | rate setpoint 8.5 deg/s worst -> Ui 25.4 -> SumEMax 1270 at Ki 0.02; UiMax above 25.4 (now 10) |
| outer push | mean a_B X -20.9 / Y 15.7; a_A+a_B X -15.9 / Y 14.3 cm/s^2; "Ui cap x3" 79.6 |
Verdicts (worker): A TRUE, B TRUE, C TRUE.
Deviations / open questions:
- Verdict C has no numbers in the doc (T3 slopes vs V and t are printed by the script, not copied). Treat C as unverified.
- A and B are each called "exact", but the doc gives only the A+B sum. Y closes within 30%, X within 22%.
  The doc does not say how much A alone and B alone contribute. Read T4 per flight before using it for WP-13.
- 79.6 = 3 x worst push (26.5 cm/s^2). That number is a velocity-loop Ui cap (cm/s^2). A position-loop Ui cap would be
  about 26.5 cm/s (Vdes = vFB + a/3, x3). The doc labels it "position/velocity" without saying which.
- Fact-table row "pitch trim std 0.9, max +1.16" is the worker's own r1 error, not a CEO fact (it was caused by shadow13 being included).
- The fact table is missing several CEO facts: shadow2-5 Des-FB, shadow14 1.81, hover/ground Acc ranges, and why pidonly7/shadow13 do not hover.
- 1.7 deg gap: the worker says it is about 0 because the Mahony filter levels on the accelerometer
  (imu_update.c:112-134, Kp 0.5 / Ki 0.001 at :20-21, boost at :106-107). So Lin_Acc about 0 proves nothing (tautology).
