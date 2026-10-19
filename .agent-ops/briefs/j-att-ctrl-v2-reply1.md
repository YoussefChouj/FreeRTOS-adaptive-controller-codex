Your session stopped with FAILED after real progress (att_ctrl.py reference model, VAR_WRITE_ROWs from ID 54 in
`TASK/AutoflyTask.c`). Do not start over. Continue from your current tree:

1. Finish the brief `.agent-ops/briefs/j-att-ctrl-v2.md` in its order. If the whole brief does not fit, finish roll and
   pitch for all 3 kinds (ADRC, LQR, MPC) first, then yaw, then z. Keep the 100% latch and the shadow logging for each.
2. Run `python tools/host_tests.py` (26/26 plus your new cases), the scoped pytest on your new test files, and
   `ruff check` on every changed .py (clean). CRLF line endings.
3. Open ONE PR against `overnight-2026-10-08` now, even if part is not done. In the PR body list each brief item as done
   or not done, and each failing test with its error line. Do not ask again.
