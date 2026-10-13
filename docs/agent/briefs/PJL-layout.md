# PJL: PlotJuggler layout generated for every capture

Operator request (2026-10-13): "I don't want to drag variables and organize them in tabs. Include legends. Do it
automatically for every new flight."

## Context

- `ground_station/livewatch/stream_log.py --plot 127.0.0.1:9870` already streams each row as flat JSON over UDP to
  PlotJuggler 3.17.2 (Windows, `D:\PlotJuggler\plotjuggler.exe`, UDP Server plugin, protocol json, timestamp field `t`,
  buffer 400 s). See `stream_log.py` around the `--plot` code.
- `ground_station/lab/camera_truth.sh fusion <name> <mode>` starts stream_log with
  `--frames ground_station/livewatch/r5_extpos_frames.md`. That frames file lists the variables per slot.
- Today PJ shows data but the plot is empty until curves are dragged in by hand.

## Step 1: research first (quote sources)

From the PlotJuggler 3.17 source or docs (github.com/facontidavide/PlotJuggler), with quoted file/line:
1. The layout XML schema: tabs (`<tabbed_widget>`/`<Tab tab_name=...>`), docking splits, `<plot>`, `<curve name=... color=...>`, legend options.
2. How the UDP Server JSON parser names a flat key like `mrac_state.pitch.xm` (exact curve name in the tree).
3. The command-line flags to load a layout and start the UDP streamer without clicks (`--layout`, `--start_streamer`, others),
   and whether the layout can store the UDP Server settings (port, protocol, timestamp field).
4. Whether a running PJ can reload a layout, or it must be restarted.

## Step 2: build (extend existing code, no one-off scripts)

- Add to `stream_log.py` (or a module next to it in `ground_station/livewatch/`) a generator: frames file -> layout XML.
  One tab per variable group (e.g. MRAC pitch, MRAC roll, MRAC yaw, MRAC z, position/EKF, ext-pos/camera, motors),
  related curves in the same plot, legends on, readable tab names. Group by the variable name prefix; keep the
  grouping rule in one small table.
- `camera_truth.sh fusion|start`: write the layout for this capture (e.g. `logs/<name>.pj.xml`) and start PlotJuggler with
  it and the UDP streamer running (restart PJ if that is the only way). Keep `PLOT=` override and `PLOT=` empty = off.
- Tests: scoped pytest for the generator (tabs present, every streamed var in exactly one tab, valid XML).
  Run only the changed files' tests.

## Rules

- Do not touch port 8081, do not flash, do not use the SWD probe, never arm.
- Never write a number you did not measure. Mark guesses PROPOSED or UNVERIFIED.
- Commit only your files; never `git add -A`.

## Report

`.agent-ops/out/PJL.md`: research answers with quotes, files changed, test output, how to run it.
