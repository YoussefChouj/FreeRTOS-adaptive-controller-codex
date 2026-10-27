You are the orchestrator for the Z-axis oscillation root-cause task in the repo
D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex (STM32 quadrotor, FreeRTOS, Keil; host tests with gcc + pytest).

Read first, fully: .agent-ops/briefs/z-cf-rootcause.md (the spec, with hypotheses H1-H6 and work items A1-A6, B1, C1-C4).
Then AGENTS.md (project rules) and docs/firmware-table-pattern.md.

Plan:
1. Create branch z-cf-rootcause from main. Never push to main. Never merge into main.
2. Launch sub-agents in this order (A1->A2->A3->A4 touch the same C file, so run them one after another;
   B1 and A5 and C4 can run in parallel with them; C1-C3 last, after A2-A4 are green):
   - Sub-agent 1: A1, A2, A3, A4 (firmware TASK/StabilizerTask.c/.h + API/tests/test_alt_cf.c), sequential.
   - Sub-agent 2: A5 + A6 (Python closed-loop sim, report table).
   - Sub-agent 3: B1 (analysis of the four 2026-10-26 recordings -> docs/lab/z-cf-rootcause-B1.md).
   - Sub-agent 4: C4 (extend ground_station/lab/cf_vz_check with a synthetic-log test).
   - Sub-agent 5 (after 1 is green): C1 firmware flag + C2 capture list + C3 doc updates.
3. WAIT for every sub-agent to finish and merge each result into z-cf-rootcause before you end.
   Never end on "I have launched". If a sub-agent fails, retry it once, then record the failure.
4. After each finished item, append a line to .agent-ops/briefs/z-cf-rootcause.progress.md
   (item, status, commit sha, test command and its last 5 output lines). On restart, read this file and resume.

Hard rules:
- NO flashing, NO rebuild_and_flash, NO SWD/ST-Link probe, NO port 8081 service, NO talking to the drone.
- Stage files by explicit path only (never `git add -A` or `git add .`). One commit per item.
- Run scoped tests only: the host C test build in API/tests and the pytest files you touched.
- Every number you did not measure is marked PROPOSED. Never invent measurements.
- Keep firmware defaults bit-identical to today's behaviour unless the spec says otherwise; new behaviour sits
  behind runtime flags that change only while disarmed.
- A Keil build is not available to you: keep the C to plain C99 the existing code already uses.

Hand back (in your final message and in the progress file):
- branch name and the list of commits;
- per item A1-A6, B1, C1-C4: done / failed / skipped, with the test output tail;
- the A5 table (estimator x delay -> margins, overshoot, hover RMS, oscillation period) and which factor
  reproduces the 0.6-0.9 m wobble;
- the B1 finding (oscillation period, lags) and which hypothesis it points to;
- who increments alt_update_cnt and at what rate (H6);
- open questions for the CEO.
