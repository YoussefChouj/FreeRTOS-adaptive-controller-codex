# Tracker zoom: CLI flags, label fix, offline A/B replay with stage timings

Read AGENTS.md first. File: `ground_station/analysis/live_tracker.py` (tests `ground_station/analysis/tests/test_live_tracker.py`).
The landing zoom was just committed: tracker `zoom` (default 1.0), `land_zoom` 2.0 used when 0 < z_agl <= land_z_m 0.30 m,
and a landing zoom PiP in the viewer.

1. CLI flags `--zoom`, `--land-zoom`, `--land-z-m` reach the tracker (defaults unchanged). Validate (zoom >= 1).
2. The PiP label hardcodes "LAND ZOOM 2.5x": show the real zoom in use. Remove any other hardcoded copy of these numbers.
3. Offline A/B replay: a CLI subcommand or module (`python -m ground_station.analysis.live_tracker replay <video> [--z-csv f]`)
   that runs the same saved raw camera video through the tracker with zoom settings A and B (default A = 1.0/1.0,
   B = current defaults), z from an optional CSV (t_s, z_m), and writes `<out>/ab_summary.md` + `ab_frames.csv`:
   detection rate, track losses, uv jitter (RMS of the frame-to-frame second difference, px), A-vs-B uv disagreement,
   and per-stage time per frame (decode, zoom/crop, detect, draw) with p50/p95 and the implied fps.
   The live grab runs at about 6 fps; the stage timings should show where the time goes.
4. Tests with a synthetic video (cv2-drawn moving ring, written to a temp file): both runs detect it, zoom B is used only
   below land_z_m, the summary files exist and parse, flags reach the tracker, label shows the real zoom.
Keep existing behaviour and tests green. Do not touch USER/, OBJ/ or logs/. Run
`python -m pytest ground_station/analysis/tests/test_live_tracker.py -q` and list the result in the PR.
