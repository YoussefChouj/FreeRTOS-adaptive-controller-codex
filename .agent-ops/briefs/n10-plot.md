# Task n10-plot: live_plot, a fast VOFA+ replacement for our WiFi telemetry

Build a live plotting tool fed by `ground_station/livewatch/stream_log.py`. Commit on your branch. Python only.
Do not touch firmware, `BSP/`, `USER/`, `OBJ/`, `logs/`. You cannot reach the drone: test with the demo sender and pytest.

## Data path (add to stream_log, keep everything else unchanged)
- New option `--plot HOST:PORT` (WiFi transport, like `--vofa`). Every slot (not only slot 0) is forwarded:
  - header datagram, JSON: `{"type":"hdr","slot":k,"names":[...]}` with the CSV column names of that slot
    (`columns_for(schema)`, line ~119), sent once at start (`on_start` point) and again every 1 s so a plot
    started late still learns the names;
  - one datagram per decoded row: `{"type":"row","slot":k,"seq":seq,"t":t_ms,"v":[...]}` (the `flat` values,
    same order as names). Rows reach 199 values at 25 Hz plus 75 at 50 Hz plus 13 at 100 Hz: JSON on localhost is fine.
- Implement it next to the existing `vofa_sock` code in `_usart3_loop` / the WiFi loop: find where `on_row` fires.
  Existing `--vofa` and `--relay-port` behaviour must not change.

## The tool: `ground_station/livewatch/live_plot.py`, run as `python -m ground_station.livewatch.live_plot`
- `--port 47900` (UDP listen, default 47900), `--csv logs/<stem>` (replay: load every `<stem>.slot*.csv`, header row
  = names), `--demo` (sends synthetic sines/steps for 3 slots through the same UDP path to itself),
  `--layout <name>` (load a saved layout).
- GUI with DearPyGui (installed on the laptop; it may be missing on your machine: import it only inside the GUI
  function so the core and tests run without it).
  - Left: variable tree built automatically from the header names, grouped by dot prefix
    (`Ctrler.Z_posPID.FB` → Ctrler / Z_posPID / FB), with a filter box. Clicking a variable adds it to the
    currently selected plot; right-click or a button removes it.
  - Right: stacked plot panels with a linked time axis; buttons to add/remove a panel; mouse wheel zoom, box
    zoom and pan (DearPyGui built ins); pause/resume (pausing freezes the view, data keeps buffering);
    time window (5 to 300 s); autoscale y toggle; a status bar with per-slot rate (Hz), rows and seq gaps.
  - Redraw at 30 fps or better with 20 traces; decimate to about 2000 points per trace when the window is long.
  - Save/load the layout (panels and their variables) as JSON in `ground_station/livewatch/plot_layouts/`.
    Ship one layout `fusion.json` with panels: `earth_x`/`earth_y` style EKF position vs external position,
    Z position and setpoint, roll/pitch: pick names that exist in `ground_station/livewatch/r5_extpos_frames.md`
    (read it; names must match exactly).
- Core (no GUI) in the same module: datagram parser, per-slot ring buffers (numpy, preallocated, 300 s at the
  slot rate), name tree builder, decimation, layout load/save. Unknown slot rows before a header are buffered
  by slot and named `slot<k>[i]` until the header arrives, then renamed.

## Tests: `ground_station/livewatch/tests/test_live_plot.py`
Header then rows → buffer contents; rows before header; ring wrap-around; tree grouping; decimation keeps
min/max of each bucket; layout round-trip; stream_log `--plot` sends a hdr and a row datagram (unit-test the
helper that formats them, plus one loopback UDP test). Run: `python -m pytest ground_station/livewatch/tests/test_live_plot.py -q`.

## Docs
Add a short section "Live plot" to `.claude/skills/stream-log/SKILL.md`: the two commands (stream_log with
`--plot 127.0.0.1:47900`, and live_plot), plus replay with `--csv`. Max 15 lines.

## Digest
`.agent-ops/out/n10-plot.md` (max 30 lines): files changed, test output tail, how to launch, known gaps.
