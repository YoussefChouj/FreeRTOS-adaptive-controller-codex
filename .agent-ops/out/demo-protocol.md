STATUS: done
FILES CHANGED:
- docs/lab/demo-pid-vs-mrac.md (new file with lab demo instructions for PID vs MRAC)

VERIFICATION:
- Check `TASK/AutoflyTask.c` and `TASK/send_data.c` for CMD 0x0C and CMD 0x11 (circle and figure8 path) parameters and duration rules. (pass)
- Check `docs/research-platform/CONTROLLER_INTERFACE.md` and `ground_station/platform/firmware_contract.py` for CMD 0x1F. (pass)
- Check `docs/research-platform/SIMPLEX.md` for abort / fade logic. (pass)
- Read `docs/analysis/flightlab-spec.md` for analysis commands and metric names. (pass)

OPEN QUESTIONS / RISKS:
- UNKNOWN: Exact names of loops in `loops.yaml` required for querying specific loop attitude/rate `e_rms` (checked: `docs/analysis/flightlab-spec.md`).
- Not verified: whether `circle_path.duration` / `figure8_path.duration` are set by default to 0.0f strictly through global initialization, though logic dictates it is indefinite.

FILES READ:
- docs/research-platform/CONTROLLER_INTERFACE.md
- docs/research-platform/SIMPLEX.md
- API/controller.c
- ground_station/livewatch/multi_slot_presets.yaml
- TASK/AutoflyTask.c
- TASK/send_data.c
- ground_station/platform/firmware_contract.py
- docs/analysis/flightlab-spec.md
- docs/dashboard-platform/AGENT_GUIDE.md
- docs/RUNBOOK.md

CONTRADICTIONS: none
SUBSTITUTIONS: none
