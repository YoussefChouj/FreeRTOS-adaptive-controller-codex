# Spec: system identification that gives usable data (audit 10-21, then excite every axis properly)

Status: SPEC, written 2026-10-24 by the CEO planning session. Built by one agent in the worktree
D:\Projects\UAV_lab\fc-sysid (branch `sysid-excite`). Offline analysis plus new flight scripts. Firmware changes are
allowed only in API/sysid.c/.h. One small flagged call-site commit is allowed in TASK/StabilizerTask.c.
Why now: the operator judged the 10-21 sysid data "not good". The MRAC work needs measured plant models and inner
closed-loop bandwidths: the matched reference model, the K_SEP clamp of the all-loop spec, and the margins.

## 1. What we already know (measured from code and the 10-21 folders, 2026-10-24)

| Finding | Where | Consequence |
|---|---|---|
| No `lab-d-excite` flight was flown on 10-21. 10-21 is the last lab day in logs/flights. | logs/flights, logs/campaigns | The dedicated sysid flight does not exist yet |
| The sysid-like flights are 38 livetune-rate, 39 and 41 livetune-pos, 40 and 42 autotune | 10-21 folders | All of them are tuning flights, not sysid flights |
| Livetune rate window: a 1 s log chirp, 1-8 Hz, 30 deg/s, with 1.5 s ramps | ground_station/livetune/loop.py:73-79 | Frequency resolution is about 1 Hz. CMA-ES changes the gains every window, so each window is a different closed loop |
| Livetune pos window: a 3 s chirp, 0.3-2 Hz, 20 deg/s | loop.py:82 | Short and low-energy |
| Only roll and pitch were ever excited | loop.py, lab_d_excite.fly | No data for yaw, z or xy |
| lab_d_excite (never flown): a multisine, 0.5-15 Hz, 60 deg/s, 30 s, roll and pitch | campaigns/lab_d_excite.fly | It can fly today on the current firmware. Check its periods (S0) |
| Deck cap EXCITE_AMP_MAX_DPS = 60; firmware cap = 90 | scenario_schema.py:81; API/sysid.c:42 | The deck blocks anything above 60 deg/s |
| The signal is added to the rate setpoint | TASK/StabilizerTask.c:2351-2356 | The loop is closed: use a closed-loop method (section 3) |
| sysid.c has a z band and several signal types | API/sysid.c:22-23 (z band), :126-133 (chirp), :184 (minimum duration), :209 (multisine) | Which axes can be excited: S2 maps this |

Still unknown (S0 answers it): are the injected signal r, the controller output u and the response y logged in the
campaign CSVs, and at what rate? live_5hz.csv is too slow for sysid.

## 2. Work items (one commit each, in this order)

**S0. Audit the 10-21 data. Tool: tools/sysid_audit.py, with a test.**
- For every 10-21 flight that excited anything (38-42; list any others you find), and for each segment where the
  gains stayed fixed, report:
  - the axis excited
  - the band, the amplitude (deg/s) and the duration
  - the channels logged (r, u, y) and their rate
  - the coherence and the SNR per frequency line
  - the fraction of lines with coherence ≥ 0.6. Source of 0.6: Tischler & Remple's accuracy floor; verify it, or
    mark it PROPOSED.
- Also check lab_d_excite on paper: its period T_p, its number of periods P, its line spacing 1/T_p, and its peak
  factor.
- Output: section 1 of the report, a table with one row per flight segment, plus a verdict per axis: usable,
  marginal, or unusable, with the reason. This answers the operator's question with numbers.

**S1. Frequency-response estimator: ground_station/analysis/sysid_fr.py, with tests.**
- Periodic multisine processing:
  - drop the first period (transient);
  - average the P periods in the frequency domain;
  - estimate the noise variance from the spread between periods.
- Closed-loop estimates:
  - the closed loop T(f) = Y/R;
  - the plant by the joint input-output method, G = S_yr / S_ur (Ljung; Pintelon & Schoukens: verify both);
  - the loop gain at the setpoint injection point, L = T / (1 - T) with unity feedback; state the assumption;
  - the coherence of each.
- Also report: the gain and phase margin, valid only inside the excited band (otherwise print "outside band");
  the closed-loop -3 dB bandwidth; and a fitted low-order model (2nd order plus delay) for the MRAC matched
  reference.
- Test on a simulated closed loop with known parameters (2nd-order plant with delay, PID, noise): recover G, T and
  the margins. The test tolerance is your choice; write it in the test and in the report.

**S2. Map what the firmware can excite (report section 2, no code first).**
- Read API/sysid.c and .h whole (both are small), plus the injection site. Make a table per axis (roll rate, pitch
  rate, yaw rate, z velocity or thrust, xy velocity or position): can it be excited today, which signals, which caps,
  which CMD.
- If yaw or z cannot be excited, add them in API/sysid.c/.h:
  - the same signal code, a per-axis cap row in a `*_ROW` table (docs/firmware-table-pattern.md), default off;
  - excite off must give bit-identical output; write that test first.
- If the call site in StabilizerTask.c must change, make it one small commit, gated by a flag, and say so in the
  commit title.
- Never edit the MRAC files: another agent owns them today.

**S3. Signal design: campaigns/lab_s_*.fly, new files only.**
- Design rules (from section 3):
  - fixed gains;
  - MRAC adaptation frozen, or MRAC off;
  - Schroeder-phased multisine with integer periods;
  - T_p = 1 / f_low;
  - P ≥ 3 periods after one discarded transient period;
  - f_high ≤ (log rate) / 5 (PROPOSED), and below the rate where S0 saw coherence collapse.
- Scripts:
  - `lab_s_rate_rp`: roll and pitch on interleaved lines (one flight, two axes, separable);
  - `lab_s_rate_yaw`: only if S2 says yaw can be excited today;
  - `lab_s_z`: only if z can be excited today;
  - `lab_s_outer_*`: a lower band and a long T_p, for the angle and position loops. Get the band from S0 and the
    current outer bandwidths.
- Amplitude ladder, one record per rung:
  - roll and pitch: 30 deg/s (flown 10-21), then 45 (PROPOSED), then 60 (deck cap);
  - other axes: start at 25 % (PROPOSED) of that axis's logged setpoint range on 10-21, and write the log name.
- A rung above 60 deg/s needs a deck cap change. Do not edit scenario_schema.py. Propose the change in the report,
  only if S0 shows low coherence at 60.
- Run every new script through the parser and a fake-drone soak. Run them only; do not edit fake_drone.py.
- If the excitation helpers live in campaign_fly.py, do not edit it: the host agent owns it. Write what the script
  needs in the report.

**S4. Report: docs/design/2026-10-24-sysid-excitation-report.md.**
1. The S0 table and the verdict per axis.
2. The S2 axis map.
3. The new scripts, each with its band, T_p, P, amplitude, duration, and why.
4. What to log per script (r, u, y, rate), and whether the current log plan already does it.
5. The deck order proposed for the CEO.
6. Every number, each with its log name or marked PROPOSED.
7. What is left, plus a 10-line summary.

## 3. Method notes (the rules the scripts follow; verify each citation)

- **Closed loop.** The excitation enters at the rate setpoint, and the controller feeds back noise. The direct
  estimate Y/U is then biased (Ljung, closed-loop identification). Use the indirect or the joint input-output
  method with the known injected r.
- **Fixed plant per record.** No livetune or CMA-ES gain changes and no MRAC adaptation inside a record. Otherwise
  each period sees a different loop.
- **Periodic multisine** (Pintelon & Schoukens). Integer periods remove leakage. Averaging P periods raises the SNR
  by about √P. Period-to-period spread gives the noise level for free. A Schroeder phase keeps the peak factor low,
  so more energy fits under the same amplitude cap.
- **Resolution versus time.** Line spacing = 1 / T_p, and the record length = (P + 1) × T_p. The outer loops need
  low lines (long T_p). Fit them inside one battery's hover time; say how in the report.
- **Several axes in one flight.** Frequency-interleaved, orthogonal multisines, one set of lines per axis (Morelli
  2003, verify). The coupling between axes is then separable. If interleaving halves the lines per axis too much,
  fly one axis per record.
- **Energy.** SNR per line falls when the same amplitude is spread over more lines. Use the S0 coherence to choose
  between a wider band and more amplitude per line.

## 4. Rules

- Work only in D:\Projects\UAV_lab\fc-sysid. Read the flight logs (read-only) from
  D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs. Never write in that checkout.
- Never flash. Never touch the 8081 service or a real drone. Do not merge or push. Stage by path.
- Files you may write:
  - tools/sysid_audit.py
  - ground_station/analysis/sysid_fr.py and their tests
  - campaigns/lab_s_*.fly
  - API/sysid.c/.h, plus one flagged StabilizerTask.c commit
  - the report
- Do not touch: decks, scenario_schema.py, campaign_fly.py, fake_drone.py, the MRAC files, the estimator files.
- No invented numbers: each one comes from a log (with its name) or is marked PROPOSED.
- Done: S0-S4 committed, tests green, Keil clean if the firmware changed (or the worktree limit reported).

The prompt for this agent is in docs/agent/2026-10-24-agent-prompts.md (prompt 5).
