<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only (firmware sources, OBJ/, logs/, docs/flights/ledger.csv included).
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions (OSError, json.JSONDecodeError, pandas.errors.EmptyDataError, KeyError ...).
   Bare `except:`, `except Exception:` and `except ...: pass` are forbidden.
6. Do not change any existing module. Only the files below.
7. Python 3.10+, standard library + numpy + pandas only. NO scipy (use numpy.polyfit for slopes).
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Every number in the analysis doc must be printed by your script in this run. Never copy a number from this
   task into the doc as a result. Never write "all facts reproduced" unless every listed fact matched within 10%.
10. Write the digest `.agent-ops/out/wp9-r2.md` BEFORE printing DONE. Commit your work (do not leave files untracked).
11. Output: no preamble, no summary prose.
</guardrails>

<context>
FIX round for your own round-1 work (HEAD = commit fc68113 "worker wp9-r1"). Read your round-1 task `~/tasks/wp9-r1.md`
(on this VPS; same text as the task you ran) first: its context, hypotheses A/B/C and spec still apply in full; this file lists what the manager found wrong.
Logs: `/home/agent/data/logs/vofa` (use `--logs /home/agent/data/logs/vofa`; CLI default stays `logs/vofa`).

Gate output on your round 1 (verbatim):
  FAIL ruff: 1 new findings
    ground_station/analysis/tests/test_drift_rootcause.py:11 [F841] Local variable `t` is assigned to but never used
  GATE FAIL: size,ruff

Manager review of fc68113 (each item must be fixed):
1. `from scipy.stats import linregress`: scipy is not allowed. Use numpy.polyfit.
2. drift_rootcause.py:18 `except Exception: return None` and :37 `except Exception: pass` are forbidden. Catch
   the specific errors; a header-only CSV must be skipped and reported ("slot3 empty"), not silently swallowed.
3. `main()` has no `argv` parameter. Spec: `main(argv=None)` passing argv to `parse_args`.
4. Signal mapping not printed. Print once per flight the CSV column used for every quantity.
5. Pitch sign. The mixer uses u_gyroy = -gyroyPID.U, so the pitch need must be compared with -gyroyPID.U, and the
   pitch MRAC share is need - (-gyroyPID.U). Print, per flight, the residual need - (gyroxPID.U) for roll and
   need - (-gyroyPID.U) for pitch, so shadow flights show ~0 (identity check).
   Your digest says cross-flight mean pitch need 1.1 ticks; the CEO measured shadow10 -20.2, shadow14 -13.8,
   shadow4 -5.5, active15 -14.2. Explain the difference (print per-flight pitch need) and report it.
6. Cap share is only computed for the roll angle loop. Compute it for all four loops (roll, pitch, gyrox, gyroy):
   share of hover samples with |Ui| >= 0.95 x cap. Caps per era: angle 10 (old, Ki 0.1) / 2.4 (3ae4a23), gyro 10.
   Determine the era from the flight name list in wp9-r1.md AND print it; if the meta.json carries a firmware
   hash/commit, print that too and flag any mismatch.
7. `d_term_mean` is hard-coded 0.0. Compute it from data: Ud ~= Kd x (e_k - e_{k-1}) on the logged samples, and
   print its mean; also print mean(U) - Kp x mean(e) - mean(Ud) as the Ui estimate.
8. The firmware PID has TWO anti-windup modes (`API/pid.c` ~130-180): a back-calculation branch (Kt, no EMin gate)
   and AW_LEGACY (integrate only while |E| < EMin and U not saturated). Find which mode each of rollPID, pitchPID,
   gyroxPID, gyroyPID, locxPID, locyPID uses (quote file:line) and make `pid_step` take a mode argument and copy
   both branches exactly. Note EMin: angle 3 deg, gyro 2 deg/s, so the legacy integrator stops when |E| >= EMin;
   print the share of hover samples with |E| >= EMin per loop (an integrator that cannot integrate is a
   different failure from one at its cap).
9. T4 is wrong: it substitutes the push for Ui_pos. The relation is e = (vFB + Uv/3 - Ui_pos)/0.8, where Uv is the
   velocity-loop output (cm/s^2, see `docs/agent/reports/2026-10-01-drift-investigation.md` line 68: Uv = 3 x
   (Vdes - vFB), Vdes = 0.8 e + Ui_pos). Compute per axis (X AND Y):
   - measured: e mean, vFB mean (locxsPID.FB / locysPID.FB), Uv mean, Ui_pos (reconstructed), and the relation's
     prediction from those measured means (closure check vs measured e);
   - from A: a_A = 981 x sin(attitude error Des - FB) on the matching attitude axis;
   - from B: a_B = 981 x sin(hover attitude FB);
   - predicted e for Uv = a_A, a_B and a_A + a_B (with measured vFB and Ui_pos) vs measured e.
   Get the axis mapping and signs (which of roll/pitch moves X/Y, and the sign from Uv to angle Des) from the code
   in `TASK/StabilizerTask.c`; quote the lines in T5/doc. Do not guess the mapping.
10. T3 "inferred push" = mean Uv (velocity-loop output) per third, per axis, not roll error x 981. Print slopes of
    push and roll need vs real_voltage and vs time with numpy.polyfit.
11. T5 claims "Mahony Kp=0.5 Ki=0.001" with no source. Quote file:line from `API/imu_update.c` for the filter type
    and each gain (and where they are set if set elsewhere). If the gain is not in that file, say where it is.
    Then compute, per hovering flight, the "gap": hover pitch FB (deg) vs asin(-Acc_X/1000) and roll FB vs
    asin(Acc_Y/1000) (signs from the code), and print both; explain the gap from the code, not by assertion.
12. Non-hover flights: for pidonly7 and shadow13 print the evidence: duration (s), columns present (is
    Ctrler.Z_posPID.FB there?), p95 and max of Z, number of samples above 0.6 x p95 after the 15% skip.
13. Pitch FB: your doc says pitch trim std 0.9 deg, max +1.16 deg; the CEO measured hover pitch FB -0.65..-1.21 in
    every hovering flight. Check whether you mixed imu_data.pit and Ctrler.pitchPID.FB (sign or source), print
    both per flight when both exist, and report the contradiction FIRST in the doc if it survives.
14. The doc says "All facts reproduced" while the digest says shadow10 Des-FB is 1.61, not 1.92. Rewrite the doc:
    first a table "CEO fact | your number | match?" for every fact in wp9-r1.md context; mismatches first.
15. Cross-flight summary rows: T1 must print, per axis and per era, worst |need|, mean need, worst |Ui| and cap
    share per loop; T4 per axis mean measured e, mean predicted e (A, B, A+B). Add a final block "WP-13 numbers"
    printed by the script: angle and gyro SumEMax at current Ki so that Ki x SumEMax >= 3 x worst |need| share each
    loop must carry (show the arithmetic), attitude trim roll/pitch (mean, std, min, max across flights) and
    the push the outer integrators must absorb (mean and worst a_B, a_A+a_B per axis) with the position/velocity
    Ui cap that holds it x3. The doc copies those printed numbers.
</context>

<allow-list>
ground_station/analysis/drift_rootcause.py
ground_station/analysis/tests/test_drift_rootcause.py
docs/analysis/2026-10-02-drift-rootcause.md
.agent-ops/out/wp9-r2.md          (digest)
</allow-list>

<spec>
Same as wp9-r1.md spec, plus items 1-15 above. Keep the function names; `pid_step` gains a `mode` argument
("legacy" | "backcalc"). Runtime on all logs still under 2 minutes.
</spec>

<tests>
Update `ground_station/analysis/tests/test_drift_rootcause.py` (synthetic data only):
- fix the F841;
- pid_step: legacy mode stops integrating when |E| >= EMin; backcalc mode with Kt > 0 bleeds SumE when U saturates;
  reconstruct_ui matches internal Ui below and at the cap; cap = min(UiMax, Ki x SumEMax);
- mixer identity with d = +1 and -1, and pitch sign: building M1..M4 from u_y = -gyroyPID.U gives
  decomposed pitch == -gyroyPID.U;
- main(["--logs", str(tmp_path)]) on a synthetic dir with one header-only slot CSV prints "T1".."T5" and
  "WP-13 numbers" and does not raise.
Run and paste the last 3 lines of each:
- `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_drift_rootcause.py -q -p no:cacheprovider`
- `ruff check ground_station/analysis/drift_rootcause.py ground_station/analysis/tests/test_drift_rootcause.py`
  (if ruff is missing: `python -m pyflakes` on both; if that is missing too, write "ruff: not installed")
- `time PYTHONPATH=. python -m ground_station.analysis.drift_rootcause --logs /home/agent/data/logs/vofa`
- `git diff --stat HEAD~1` after your commit.
</tests>

<digest>
`.agent-ops/out/wp9-r2.md`, at most 40 lines: files changed; each command + last 3 output lines; the T1, T4 and
"WP-13 numbers" summary rows verbatim; the fact table mismatches; verdict A/B/C one line each; the AW mode per
loop with file:line; deviations; open risks.
</digest>
