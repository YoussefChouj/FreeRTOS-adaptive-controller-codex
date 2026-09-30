STATUS: done

Files changed:
- ground_station/service/agent.py: Added WIDE_CMD_BASE..WIDE_CMD_LAST (0x20..0x2B) to CRITICAL_PARAM_WRITE and PARAM_WRITE_TIER. Updated comment blocks.
- ground_station/service/tests/test_agent.py: Added tests `test_wide_mrac_classification_and_tier0` and `test_wide_mrac_param_is_critical_and_tier0`.

Answers to Do 2 & 3:
- Do 2: Checked other list/maps in agent.py keyed by command id. The only other occurrence of `0x1E` is an `if cid == 0x1E` check in `command_flags()` (`ground_station/service/agent.py:283`). The wide block does not need to be there since that checks specifically for OF_BIAS_MODE.
- Do 3: 0x1F CTRL_SELECT and 0x0A/0x0B/0x0C/0x11 are NOT classified anywhere in `agent.py` or `ground_station/service/core.py`. (Grep found no matches for any of these ids).

Verification:
- `python -m pytest ground_station/service/tests/test_agent.py -q` -> 26 passed
- `python -m pytest ground_station/service/tests/test_agent_mcp.py -q` -> 3 passed

Open questions / risks:
- None. All verified.

SUBSTITUTIONS: none
