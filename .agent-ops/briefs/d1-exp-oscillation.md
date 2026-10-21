# D1 - why LQR / MPC / ADRC (with and without MRAC) oscillated on 2026-10-21 (READ-ONLY analysis)

You are a log-analysis worker. Do NOT edit any tracked file, do NOT commit, do NOT touch hardware, 8081 or any
serial/WiFi port. Scratch scripts go in /tmp or .agent-ops/out/d1/. Every number you write must come from a
command you ran (quote the command or script). Unsourced claims: mark UNVERIFIED.

## Symptom (operator, 10-21)
LQR, ADRC and MPC (alone and with MRAC) all flew worse than PID, with a severe oscillation right after takeoff that
settles a bit but stays worse. LQR and MPC flew well on earlier days. CEO's first pass: exp controllers oscillate
with a 1.4-1.8 s period vs 2-3.5 s for PID; every flight has a 30-60 cm peak-to-peak y transient after takeoff.

## Data
- Logs are NOT in the git tree: they are copied to /home/agent/lablogs/logs/ (same layout).
- 10-21 flights: logs/flights/2026-10-21/NN_<deck-id>/ (live_5hz.csv, debrief.txt, outputs.txt, *.yaml, *.fly).
  Exp-controller flights: 19-27 (lab-e-exp-*), 43 lab-c-lqr, 44 lab-c-mpc. PID references: 24, 28, 30, 31, 49.
- Earlier flights: logs/flights/2026-10-14 .. 2026-10-19 (find the LQR/MPC/ADRC ones that flew well) plus any
  higher-rate stream logs under logs/ (grep for csv with Ctrler.* or rate/attitude columns).
- Firmware: TASK/, USER/ C sources (StabilizerTask.c, AutoflyTask.c, controller files). Use
  `git log --since=<date of last good exp flight> -- TASK USER '*.c' '*.h'` to list firmware commits between the
  last good exp flight and 10-21 (the 10-21 flash included cd8326de exp latch, a5ffa3cd VFF, 868f4ba4 landing hold,
  226ecd45/cdf54487 drift NN, ff305d78 x/y MRAC CMD 0x1D, Z MRAC ed5ece83 6e275bcb 6cd8298e 6efb758f).

## Questions (answer each with evidence)
1. Per flight (exp + PID refs, 10-21 and the last good days): x/y/z rms error in hover and TRAJ, dominant
   oscillation period (FFT or zero crossings) and amplitude, first 5 s after takeoff vs later. One table.
2. Is the oscillation in the outer (position) loop or the inner (attitude/rate) loop? Which columns show it first?
3. Which firmware change between the good days and 10-21 touches the exp-controller path? For each candidate
   (exp latch, VFF feed-forward added on top of LQR/MPC/ADRC, x/y MRAC, gain defaults, rate_err feed, delay
   compensation, takeoff handover at 0.5 m), cite file:line and say whether the 10-21 data supports it.
4. How does the exp controller take over (at takeoff? at hover?), and do its integrator/observer states start from
   zero or from the PID state at handover? (ADRC ESO and MPC warm start especially.)
5. Your ranked root-cause list with the evidence for and against each, and the smallest fix you would try, plus
   how to verify it in sim (sim/ folder) before flying.

## Output
Write .agent-ops/out/d1-exp-oscillation.md: bottom line first (3 lines), then the table, then answers 1-5.
Under 250 lines. End with one status line: `D1 DONE` or `D1 BLOCKED: <why>`.
