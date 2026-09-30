# Task demo-protocol: write `docs/lab/demo-pid-vs-mrac.md` (lab demo: PID vs PID+MRAC)

You are a worker in a git worktree of this repo. Documentation task: read source, write one Markdown file.
The operator runs this protocol in the lab tomorrow morning; a wrong command or invented number costs a flight.

EDIT ONLY: `docs/lab/demo-pid-vs-mrac.md` (new) and `.agent-ops/out/demo-protocol.md` (your report).
No code edits. No network, no port 8081, no hardware, no probe.

## Hard rules
- Every command, CMD id, parameter name, preset name, MCP tool name and number in the doc must come from a file you
  read. Put the source as `path:line` in a trailing comment or a Sources column. If you cannot find a fact, write
  `UNKNOWN (checked: <files>)` in place of it. Never write a threshold, gain or duration you did not read.
- Width <= 110 columns. Plain Markdown, tables where it helps. No emojis.

## Sources to read (start here, follow references)
- `docs/research-platform/CONTROLLER_INTERFACE.md`, `docs/research-platform/SIMPLEX.md` (or find them with `rg -l`).
- `API/controller.c` (runtime A/B: `g_ctrl_select_req`, CMD 0x1F CTRL_SELECT).
- `TASK/send_data.c` around lines 1670-1713 (GS commands that start/stop `circle_path` / `figure8_path`),
  `TASK/AutoflyTask.c` (path generators: radius, period, altitude handling).
- `ground_station/livewatch/multi_slot_presets.yaml` (preset `flight_test_adaptive`: its variables and rates).
- `ground_station/service/` MCP / action lists (`rg -n "def \|actions\|CTRL_SELECT\|circle\|figure8" ground_station/service`),
  `docs/dashboard-platform/AGENT_GUIDE.md`, `docs/RUNBOOK.md`, `AGENTS.md` (flash + verify commands, arm rules).
- `ground_station/analysis/flightlab/__main__.py` + `docs/analysis/flightlab-spec.md` (analysis commands).

## Document content, in order
1. Purpose (2 lines) and the comparison design: 4 conditions (hover, hover with off-centre load, circle, figure8),
   each flown PID first then PID+MRAC, same battery pack, same preset `flight_test_adaptive`. Say what is held equal.
2. Morning flash step: `ah lock hw`, the exact rebuild/flash command, `livewatch verify`, what a pass looks like.
3. Props-off bench checklist, one checkbox line each with the exact command and the expected observation:
   CTRL_SELECT 0x1F toggles PID <-> MRAC (how to read back which controller is active); MRAC telemetry streams in the
   preset; circle and figure8 start + stop on the ground; simplex fade behaviour (what the source says it does).
4. Per-condition run card (table): setup, command to start recording, command to switch controller, command to start
   and stop the path (circle/figure8), duration (from source, else UNKNOWN), what to watch.
5. Abort criteria and how to abort (RC ch10 hard kill per AGENTS.md; any source-defined simplex / safety trips).
6. Analysis after each pair: the exact flightlab commands (`analyze <meta>`, `compare <A> <B>`) and which metrics
   answer "did MRAC help" (tracking error, attitude e_rms per loop, from the metrics schema names).
7. Open questions for the operator: every UNKNOWN you wrote, in one list.

## Report `.agent-ops/out/demo-protocol.md`
Files read (list), every UNKNOWN with the files you checked, and any contradiction between docs and source
(source wins; say which).
