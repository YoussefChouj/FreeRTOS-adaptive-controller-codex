1) STATUS: done

Trace table:
| key | firmware variable | unit | source |
| --- | --- | --- | --- |
| dx | Ctrler.locxPID.Des | cm | TASK/AutoflyTask.c:68, 89 |
| dz | Ctrler.Z_posPID.Des | metres | TASK/AutoflyTask.c:70, 89 |

Verdict:
Raw `dz` in `rec_logs.py` is correct. `Z_posPID.Des` is already in metres in firmware, while `locxPID.Des` is in cm. The scaling in `rec_logs.py` (`dz` raw, `dx/100`) matches firmware.

2) files changed:
- ground_station/service/rec_logs.py: Added comment on dz assignment citing firmware source.
- ground_station/service/tests/test_rec_logs.py: Added test_dz_is_metres_raw.
- ground_station/service/tests/path_panel_harness.js: Added CHECK 21 for replay frame z scaling.

3) verification:
- `python -m pytest ground_station/service/tests -q -k "rec_log or replay"`: 30 passed, 434 deselected.
- `node ground_station/service/tests/path_panel_harness.js`: ALL CHECKS PASSED SUCCESSFULLY (exit 0).

4) open questions / risks:
- Not verified: Other potential unit inconsistencies in firmware variables outside this path.

SUBSTITUTIONS: none
