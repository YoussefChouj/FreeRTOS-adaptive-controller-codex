# N1: inventory of the 2026-10-21 lab flights (read-only digest)

Read only. Do not edit, create or delete any repo file. Write your answer only to your output file.

## Inputs

- 49 flight folders: `logs/flights/2026-10-21/01_*` .. `49_*`. Each folder has:
  - `debrief.txt`: outcome line, ABORTED reason, HOVER and TRAJ x/y/z rms and max cm, landing offset, camera landing;
  - `live_5hz.csv`: 5 Hz telemetry, `t` plus Des/FB for Z_posPID, locxPID and locyPID, and prim_state and traj_state;
  - `outputs.txt`: the path of the campaign folder `logs/campaigns/<name>_<stamp>/`.
- In each campaign folder, look for the event log and the abort reason (events, scorecard or summary files; list
  the file names you find in one campaign folder before using them).

## Deliverables (markdown, at most 170 lines)

1. **Table, one row per flight.** Columns:
   - number and folder name;
   - controller: from the name or the yaml (pid, lqr, mpc, adrc, mrac on/off, u0 or u100);
   - outcome: complete or ABORTED, plus the reason;
   - air time in s;
   - HOVER rms x/y/z and TRAJ rms and max;
   - landing offset in cm;
   - camera landing in cm.
2. **Initial oscillation on the exp-controller flights (19-27, 43, 44) vs the PID flights (24, 30, 31, 49 and the
   robust flights 28 and 32).** From `live_5hz.csv`, compute the x and y error (FB minus Des) over:
   - the first 5 s after `prim_state` leaves takeoff;
   - the last 5 s of the hover.
   Report both peak-to-peak values per flight. Estimate the dominant oscillation period from the zero crossings.
   State whether the oscillation decays and roughly how fast.
3. **Aborts.** Group every abort by reason: oscillation, pos_err, heartbeat or link, operator, other. For each
   group, quote the evidence line (file plus line text). For oscillation aborts, give the time from the first
   visible oscillation in `live_5hz.csv` to the abort event. The operator says the gauge triggers late.
4. **Presets that stopped halfway.** List every flight where `traj_state` never reached its done value, or where a
   step after takeoff never ran. Quote the event or result line that explains it, for example `var_write not
   applied` or `traj_go not confirmed`.
5. **Speed ladder (29, 33-37).** Report the drift and position error vs commanded speed per level. Report the
   camera landing offset per level.
6. **Data gaps.** List folders with missing files, empty CSVs, or a debrief showing "n/a".

## Rules

- Every number must come from a file you read or computed. Name the file. If you could not compute a number,
  write UNVERIFIED instead of a guess.
- If running Python helps, run it read-only, from outside the logs folders, with `python -I`.
- Keep under the 160K cap. Skim the CSVs with pandas; do not print them.
