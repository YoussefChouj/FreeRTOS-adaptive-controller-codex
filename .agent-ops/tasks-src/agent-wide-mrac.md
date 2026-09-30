# Task agent-wide-mrac: classify wide MRAC element commands CMD 0x20..0x2B in service/agent.py (+ tests)

You are a worker in a git worktree of this repo. Small Python change plus tests. No network, no port 8081, no
hardware, no probe, no firmware edits.

EDIT ONLY: `ground_station/service/agent.py`, `ground_station/service/tests/test_agent.py`,
`.agent-ops/out/agent-wide-mrac.md` (your report).

## Background (checked by the supervisor)
- `ground_station/comm/mrac_param_encoder.py:12,40-41`: wide encoding CMD_ID = 0x20 + (field << 2) + axis, block
  `WIDE_CMD_BASE`..`WIDE_CMD_LAST` = 0x20..0x2B; INDEX = elem. Firmware handles them in `TASK/send_data.c`
  (`MRAC_ELEM_CMD_IS(id)`, `CommandSafetyReject`) and writes MRAC config (tier 0, `API/mrac.c`).
- `ground_station/service/agent.py:232-253`: `CRITICAL_PARAM_WRITE` and `PARAM_WRITE_TIER` list 0x01..0x1E only.
  The comment says "A command missing here counts as tier 0", so 0x20..0x2B are only implicitly tier 0.

## Do
1. Add 0x20..0x2B to `CRITICAL_PARAM_WRITE` and to `PARAM_WRITE_TIER` (tier 0). Derive the range from
   `mrac_param_encoder.WIDE_CMD_BASE` / `WIDE_CMD_LAST` (import them), do not hard-code twelve literals. Keep the
   sets `frozenset` / the dict shape; update the comment block above them (one line naming the wide block and its
   source file).
2. Check every other list/map in agent.py keyed by command id (grep `0x1E`, `0x19`) and say in the report whether
   the wide block needs to be there too. Change it only if the same "missing means X" logic applies; otherwise report.
3. Report only, do not change: whether 0x1F CTRL_SELECT and 0x0A/0x0B/0x0C/0x11 (paths) are classified anywhere in
   agent.py or `ground_station/service/core.py`, with `path:line`.

## Tests (in test_agent.py, follow its existing style and fixtures)
- every id in 0x20..0x2B is in `CRITICAL_PARAM_WRITE` and maps to tier 0; 0x1F and 0x2C are unchanged by your edit
  (assert their membership equals what it was before: read it, do not assume).
- a plan step that writes a wide MRAC param (build it with `encode_mrac_param_wide`) is classified critical /
  tier 0 by the same function the existing tests use for 0x02 (find it; mirror that test).

## Acceptance (verbatim tails in the report)
- `python -m pytest ground_station/service/tests/test_agent.py -q`
- `python -m pytest ground_station/service/tests/test_agent_mcp.py -q`
Report: files changed, answers to Do 2 and 3 with `path:line`, anything you could not verify.
