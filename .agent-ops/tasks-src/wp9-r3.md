<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only (firmware sources, OBJ/, logs/, docs/flights/ledger.csv included).
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest. Round 2's digest said
   "ruff ... passed" but the manager's ruff run found a finding: paste the real output, never a summary.
5. Catch only specific exceptions. Bare `except:`, `except Exception:` and `except ...: pass` are forbidden.
6. Do not change any existing module. Only the files below.
7. Python 3.10+, standard library + numpy + pandas only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Every number in the analysis doc must be printed by your script in this run. A verdict is TRUE only if the
   printed numbers close (prediction within 30% and same sign); otherwise write NOT CONFIRMED and why.
10. Write the digest `.agent-ops/out/wp9-r3.md` BEFORE printing DONE. Commit your work.
11. Output: no preamble, no summary prose.
</guardrails>

<context>
FINAL FIX round (HEAD = 1d1bd96 "worker wp9-r2"). The tasks `~/tasks/wp9-r1.md` and `~/tasks/wp9-r2.md` on this VPS
still apply in full; this file lists what is still wrong. Logs: `--logs /home/agent/data/logs/vofa`.

Gate on round 2 (verbatim):
  FAIL ruff: 1 new findings
    ground_station/analysis/drift_rootcause.py:149 [E702] Multiple statements on one line (semicolon)
  GATE FAIL: ruff

Firmware facts the manager read (quote them in T5/doc, re-read to confirm):
- `TASK/StabilizerTask.c:1376-1377`: des_pitch = -(locysPID.U)*Cos_Yaw_01 - (locxsPID.U)*Sin_Yaw_01;
  des_roll = -(locxsPID.U)*Cos_Yaw_01 + (locysPID.U)*Sin_Yaw_01. des_* are accelerations in cm/s^2 (the stick path
  at :1384 scales by GRAVITY_MSS*100). `:1388` accel_to_lean_angles(des_pitch, -des_roll, &pitchPID.Des, &rollPID.Des).
  So locxs drives roll and locys drives pitch only when yaw ~ 0; the signs pass through two negations and
  accel_to_lean_angles. Find accel_to_lean_angles and where Cos_Yaw_01/Sin_Yaw_01 are computed (grep), and use them.
- `TASK/StabilizerTask.c:1342,1351`: locysPID.Des = locyPID.U, locxsPID.Des = locxPID.U (limited at +/-120).
- `API/imu_update.c:20-21` Kp = 0.5f, Ki = 0.001f; `:106-107` kp_eff/ki_eff blend from IMU_KP_BOOST/IMU_KI_BOOST
  over a boost window; `:112-134` accel is normalised, cross product with the estimated gravity gives ex/ey/ez,
  which is fed into the gyro (Gyro_X_Real += kp_eff*ex + exInt): the attitude levels on the accelerometer.
  `:69-76, ~200`: linear accel = measured - gravity direction.
- rollPID.U is the rate setpoint for gyroxPID (deg/s), NOT ticks. Only gyroxPID/gyroyPID outputs are ticks.

Still wrong in 1d1bd96 (fix every item):
1. ruff E702 at drift_rootcause.py:149.
2. `main()` line 318: `res['name'] in ['pidonly7', 'shadow13']` never matches (names are `f17_..._pidonly7` etc.),
   so the non-hover evidence never prints. Print the evidence for EVERY non-hovering f17 flight.
3. Axis mapping is a guess ("X axis maps to roll" comment). Implement the real chain: per sample, Uv_x, Uv_y ->
   des_pitch/des_roll with the logged/computed yaw (same formula as :1376-1377) -> accel_to_lean_angles copy ->
   predicted pitchPID.Des / rollPID.Des. Print per flight the closure: mean predicted Des vs mean logged Des, for
   roll and pitch. If yaw is not logged, say which signal you used and print its hover mean.
4. T4: compute a_A (from Des - FB) and a_B (from FB lean) in the body frame, map them back to world X/Y through the
   inverse of the same chain (so signs come from code), then predicted e = (vFB + a/3 - Ui_pos)/0.8 per axis.
   Round 2 printed Y: measured +3.7, predicted (A+B) -7.1: opposite sign. Print per flight and cross-flight:
   measured e, closure "pred e from measured Uv/vFB/Ui_pos", pred A, pred B, pred A+B. Verdicts follow guardrail 9.
5. WP-13 numbers are dimensionally wrong (angle loop "SumEMax >= 7850" sized in ticks). Recompute:
   - gyro loop (ticks): Ui_needed = 3 x worst |need| per axis; print SumEMax = Ui_needed / Ki AND the UiMax needed
     (current UiMax 20 also caps it: cap = min(UiMax, Ki x SumEMax)).
   - angle loop (deg/s): once the gyro Ui carries the torque, the angle loop only needs Ui for the steady rate
     setpoint = mean gyroxPID.FB / gyroyPID.FB in hover (rate bias). Ui_needed = 3 x worst |mean rate setpoint
     the gyro loop needs| (print the per-flight values used); SumEMax = Ui_needed / Ki and UiMax needed.
   - outer loops: worst push per axis (cm/s^2) from a_B and a_A + a_B; position Ui cap (cm/s) that holds it x3 is
     3 x a/3 = a (since Vdes = vFB + a/3 at zero error); print the arithmetic.
6. shadow10 Des - FB roll: round 1 printed 1.61, round 2 ~0.8, CEO measured 1.92. Find out why. For shadow10 and
   shadow14 print Des - FB (a) on the merged frame, (b) using only rows of the slot CSV that natively holds both
   Ctrler.rollPID.Des and Ctrler.rollPID.FB (no cross-slot fill), plus hover sample count and time span. Remove the
   `.bfill()` in load_flight (it invents values before a slot starts) and restrict hover to the span where every
   used column has real data.
7. T5: rewrite from the code facts above with file:line; state whether "Lin_Acc ~ 0" is a tautology. Print per
   flight pitch FB vs asin(-Acc_X/1000) and roll FB vs asin(Acc_Y/1000) (check the signs against :200 and the
   Mahony cross product) and the gap in deg.
8. Doc `docs/analysis/2026-10-02-drift-rootcause.md`: first the table "CEO fact | your number | match?" for every
   fact in wp9-r1.md context (mismatches first, including item 6); then verdicts A/B/C under guardrail 9; then the
   recomputed WP-13 numbers copied from the script output.
</context>

<allow-list>
ground_station/analysis/drift_rootcause.py
ground_station/analysis/tests/test_drift_rootcause.py
docs/analysis/2026-10-02-drift-rootcause.md
.agent-ops/out/wp9-r3.md          (digest)
</allow-list>

<spec>
Same as wp9-r1.md and wp9-r2.md, plus items 1-8 above. Add `accel_to_lean(des_pitch, des_roll)` (Python copy of
the firmware function) and `yaw_rotate(ux, uy, yaw_deg)` (copy of :1376-1377). Runtime under 2 minutes.
</spec>

<tests>
Add to the test file (synthetic only, keep the existing tests): accel_to_lean matches hand-computed values for
two points incl. a negative one; yaw_rotate at yaw 0 and 90 deg; main() on a synthetic dir prints the
non-hover evidence line for a flight named `f17_x_pidonly7`.
Run and paste the last 3 lines of each:
- `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_drift_rootcause.py -q -p no:cacheprovider`
- `ruff check ground_station/analysis/drift_rootcause.py ground_station/analysis/tests/test_drift_rootcause.py`
- `time PYTHONPATH=. python -m ground_station.analysis.drift_rootcause --logs /home/agent/data/logs/vofa`
- `git diff --stat HEAD~1` after your commit.
</tests>

<digest>
`.agent-ops/out/wp9-r3.md`, at most 40 lines: files changed; each command + last 3 output lines; T1, T4 and
"WP-13 numbers" summary rows verbatim; item-6 explanation; Des closure (item 3) cross-flight; fact-table mismatches;
verdict A/B/C one line each; deviations; open risks.
</digest>
