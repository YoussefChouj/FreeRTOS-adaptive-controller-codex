<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only (firmware sources, OBJ/, logs/, docs/flights/ledger.csv included).
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Do not change any existing module. Only create the new files listed below.
7. Python 3.10+, standard library + numpy + pandas (+ the repo's own `ground_station` package). No new dependencies.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Every number you write in the analysis doc must come from your own script's output in this run. Never copy a
   number from this task into the doc as a result; the numbers below are the CEO's claims to CONFIRM OR OVERTURN.
10. Write the digest `.agent-ops/out/wp9-r1.md` BEFORE printing DONE.
11. Output: no preamble, no summary prose.
</guardrails>

<context>
Goal: hover-drift root cause from the f17 flight logs. Analysis only (no firmware change, no build, no flash).

Logs: on this VPS at `/home/agent/data/logs/vofa/f17_*` (per flight `<name>.meta.json` plus `<name>.slotN.csv`).
Use `--logs /home/agent/data/logs/vofa` for your runs; the CLI default must stay `logs/vofa`.
Loader: `ground_station/analysis/flightlab/loaders/vofa.py:17 load_vofa(meta_path)`, or read the slot CSVs with
pandas directly (`t_src_ms` = source time in ms). `load_vofa` raises on header-only CSVs (active6 slot0, active15
slot0, pidonly7 slot3): skip empty slots, never crash. Ignore `log_test_*`, `test_loging`, `dash_*`.
Flight list and firmware eras: `docs/agent/handoffs/2026-10-01-overnight-context.md` table "Today's flights".
Firmware 3ae4a23 = shadow10, active12, shadow13, shadow14, active15; every earlier flight = old angle Ki 0.1.
"shadow" = MRAC computes but does not inject; "active" = MRAC u_ad is added to the roll rate output.

CEO-measured facts (hover = `Z_posPID.FB > 0.6 x p95(Z_posPID.FB)`, first 15% of the flight skipped):
- Mixer `TASK/StabilizerTask.c` ~1109-1145: u_gyrox = gyroxPID.U (via Controller_Update, which adds MRAC u_ad in
  active mode), u_gyroy = -gyroyPID.U. M1 = T - u_y - u_x - d*u_z, M2 = T + u_y + u_x - d*u_z,
  M3 = T - u_y + u_x + d*u_z, M4 = T + u_y - u_x + d*u_z (d = g_yaw_mix_dir). So per motor:
  roll = (M2+M3-M1-M4)/4, pitch = (M2+M4-M1-M3)/4, yaw = (M3+M4-M1-M2)/4. In shadow flights this equals
  gyroxPID.U / -gyroyPID.U / -gyrozPID.U. Units: PWM ticks per motor, hover throttle about 3000.
- Hover means (ticks/motor), roll/pitch/yaw: shadow10 45.0/-20.2/-156.9; shadow14 37.1/-13.8/-134.6;
  shadow4 38.8/-5.5/-114.9; active15 32.9/-14.2/-89.1 with gyroxPID.U 0.2 (MRAC carries roll).
- PID table `API/pid.c` (columns Kp Ki Kd UMax UpMax UiMax UdMax SumEMax EMin); Ui cap = min(UiMax, Ki x SumEMax):
  rollPID/pitchPID `3.0 0.02 8 200 200 10 10 120 3` (cap 2.4; before 3ae4a23 Ki 0.1, cap 10),
  gyroxPID/gyroyPID `5 0.01 10 300 300 20 100 1000 2` (cap 10), locx/y `0.8 0.01 4.0 300 300 20 50 200 30` (cap 2),
  locxs/ys `3.0 0 6.0 600 600 100 100 200 10` (Ki 0). Read the PID update / anti-windup code in `API/pid.c`
  (~lines 153-171) before reconstructing Ui; your Python copy of the update must follow that code exactly.
- Steady roll Des - FB: shadow10 +1.92 deg, shadow14 +1.81 deg (3ae4a23); shadow2/3/4/5 +0.04/+0.14/+0.07/+0.12.
  rollPID.U in shadow +6.2..+8.7, gyroxPID.U +30.7..+45.6.
- Hover attitude FB roll -0.83..-1.59, pitch -0.65..-1.21 deg in every hovering flight. Hover Acc_X -10..-21 mg,
  Acc_Y -13..-30 mg; on the ground before takeoff X -29..+28, Y -7..+11. yawPID.FB about 0 (relative to arm).
- Drift relation (`docs/agent/reports/2026-10-01-drift-investigation.md`): e = (vFB + Uv/3 - Ui_pos)/0.8.
  The loc loops run at 100 Hz, so per-tick Ki = Ki_c x 0.01 there.
- pidonly7 and shadow13 did not pass the hover filter: find out why (no takeoff, different signal name, too short).

Hypotheses to test (do not assume them):
- A. Roll needs about 40 ticks/motor. PID-only: gyro Ui caps at 10, so the rate P term must supply 30, which needs
  about 6 deg/s from the angle loop, whose Ui caps at 2.4, so the angle P term needs about 1.8 deg of error.
  Old Ki 0.1 had room (cap 10) and showed no error. The outer loops see the 1.8 deg as a push.
- B. Even with no attitude error the drone hovers at about -1.2 roll / -0.9 pitch deg (IMU), which needs a standing
  position error because velocity Ki is 0 and the position Ui cap is 2.
- C. The push grows during a flight (12 -> 28 cm/s^2), maybe from battery sag: more ticks for the same roll torque
  -> more capped shortfall -> more error. Test against real_voltage and against time.
</context>

<allow-list>
ground_station/analysis/drift_rootcause.py
ground_station/analysis/tests/test_drift_rootcause.py
docs/analysis/2026-10-02-drift-rootcause.md
.agent-ops/out/wp9-r1.md          (digest)
</allow-list>

<spec>
1. `ground_station/analysis/drift_rootcause.py`, CLI `python -m ground_station.analysis.drift_rootcause --logs DIR`
   (default `logs/vofa`). Discovers every `f17_*.meta.json` in DIR, loads all non-empty slots, merges signals on
   time, segments hover, and prints for EVERY hovering flight these tables (plain text, one block per flight,
   then a cross-flight summary row per table):
   - T1 inner loop per axis (roll, pitch; yaw torque only): torque need from the mixer (ticks/motor), angle-loop and
     gyro-loop U means, reconstructed Ui = mean U - Kp x mean e (also print the D-term mean and confirm it is ~0),
     the Ui caps for that flight's firmware era, share of hover time with |Ui| at the cap (>= 95% of cap),
     Des - FB mean; in active flights the MRAC share = need - PID gyro U.
   - T2 lean and push: hover attitude FB and Des (roll, pitch), Acc_X/Y mean, Lin_Acc rebuilt
     `lin_x = Acc_X + 1000 sin(pit)`, `lin_y = Acc_Y - 1000 sin(rol) cos(pit)` (mg, angles in rad), velocity-loop
     U, position-loop U and reconstructed Ui, position error mean.
   - T3 hover split into thirds by time: real_voltage, roll need (ticks), roll error, inferred push; least-squares
     slopes of push and roll need vs voltage and vs time.
   - T4 position-error budget: standing error predicted from A (attitude error -> push) and from B (IMU lean ->
     push), via e = (vFB + Uv/3 - Ui_pos)/0.8, vs the measured position error.
   - T5 estimator: a fixed text block from your reading of `API/imu_update.c`: does the attitude filter level on
     the accelerometer, with what gain (quote file:line), hence whether "Lin_Acc about 0" is evidence or a
     tautology; and an explanation of the ~1.7 deg pitch-vs-accel gap.
   - Also print, for each f17 flight NOT hovering, one line saying why (no takeoff / missing signal / too short).
   Keep functions small and importable: at least `find_flights(dir)`, `load_flight(meta_path)` (returns a pandas
   DataFrame), `hover_mask(df)`, `mixer_decompose(m1, m2, m3, m4)` -> (roll, pitch, yaw), `pid_step(...)` (Python
   copy of the firmware PID update incl. anti-windup), `reconstruct_ui(u, e, kp)`, and `main(argv=None)`.
   Signal names: use the column names that actually appear in the CSVs; print the names you mapped.
   Total runtime on all logs under 2 minutes.
2. `ground_station/analysis/tests/test_drift_rootcause.py` on synthetic data only (no real logs):
   - hover segmentation: synthetic altitude ramp/hold/land -> mask covers the hold minus the first-15% skip;
   - mixer identity: build M1..M4 from random T, u_x, u_y, u_z, d=+/-1 with the formulas above -> decompose
     returns (u_x, u_y, d-signed yaw) exactly;
   - Ui reconstruction: run pid_step over a synthetic error series until Ui settles (below and at the cap) and check
     reconstruct_ui matches the internal Ui, and that the cap is min(UiMax, Ki x SumEMax);
   - main() on a tiny synthetic logs dir (written to tmp_path) prints "T1" .. "T5" and does not crash on a
     header-only slot CSV.
3. `docs/analysis/2026-10-02-drift-rootcause.md`: anything that contradicts the CEO facts above goes FIRST; then
   verdicts on A, B, C with your numbers; then the numbers the firmware fix needs: angle and gyro SumEMax (or Ki)
   so the Ui caps cover the worst roll and pitch need across flights with x3 headroom; attitude trim (deg) with its
   scatter (std, min, max) across flights; the push the outer integrators must still absorb (cm/s^2 and the
   position Ui cap that would hold it).
</spec>

<tests>
Run and paste the last 3 lines of each:
- `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_drift_rootcause.py -q -p no:cacheprovider`
- `time PYTHONPATH=. python -m ground_station.analysis.drift_rootcause --logs /home/agent/data/logs/vofa`
  (must list at least shadow2/3/4/5/10/14 and active1/5/6/8/12/15 as hovering)
- `git diff --stat HEAD~1` after your commit (only allow-listed paths).
</tests>

<digest>
`.agent-ops/out/wp9-r1.md`, at most 40 lines: files changed; each command + its last 3 output lines; the T1 and T4
cross-flight summary rows verbatim; verdict A/B/C in one line each; which CEO facts you could NOT reproduce;
deviations from this spec; open risks.
</digest>
