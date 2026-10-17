# j-att-ctrl v2: fresh start of the attitude-controller job (the first session failed before pushing)

Branch: `overnight-2026-10-08`. Open ONE PR against it.

A first session on this task worked for 94 minutes and then stopped with "unable to complete the task" before it
pushed anything, so nothing of it exists. Start from the branch head.

## What to build

1. The full spec is in `.agent-ops/briefs/j-att-ctrl.md` (on this branch). Read it first and implement all of it.
2. The review points in `.agent-ops/briefs/j-att-ctrl-reply1.md` (on this branch) are part of the spec. Build them
   in from the start.
3. The first session had reached these design decisions. Keep them unless the code says otherwise:
   - an RLS estimator for the online parameters, with a forgetting factor and covariance reset or bounds stated as
     named PROPOSED constants;
   - Riccati convergence and finiteness bounds checked in code and in tests (iteration cap, a NaN/Inf check, and
     a fallback when the bounds fail);
   - an assertion or test that nothing is written while disarmed is violated (the disarm assertion).

## How to work (this matters: the last session died before it pushed)

- Commit and push in small steps to your PR branch as each part passes its tests. Do not hold everything until
  the end.
- Run only scoped tests: the host harnesses with `python tools/host_tests.py` (it must stay 26/26 plus your new
  cases), pytest on the new and changed test files only, and `ruff check` on the changed .py files.
- If a test fails that you cannot fix in one try, do not loop. List it in the PR body with its error line and go on.
- Line endings: CRLF in .c, .h and .py, like the existing files.
- Do not edit `ground_station/livewatch/campaign_capture.py`, `ground_station/livewatch/stream.py`,
  `ground_station/livewatch/var_scales.yaml`, `ground_station/analysis/hover_fit.py`,
  `ground_station/analysis/att_fit.py`, `USER/`, or `ground_station/service/campaigns/*.fly`. Other sessions own
  these files. If you need a change there, put a diff block in the PR body.
- If a spec point conflicts with the code, follow the code and say so in the PR body.
- In the PR body, list each test and its result, and mark every reply1 point done or not done.
- Do not ask before opening the PR.
