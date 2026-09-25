# T15: Paths tab, interactive 3D view of the flown path against the desired path

Do not contact 127.0.0.1:8081, do not touch the probe, and do not flash. Commit with LF line endings on your branch.

## Context

`docs/dashboard-platform/shell/plugins/path-panel.js` (825 lines) draws the path on a 2D canvas. Read it and `docs/dashboard-platform/shell/plugin-api.md` first. The operator wants a real 3D view: rotate with left-drag, zoom with the wheel, pan with right-drag or shift-drag, like a CAD / 3D-model viewer. It must show the actual path against the desired path.

## Requirements

1. Use three.js with OrbitControls, **vendored locally** under `docs/dashboard-platform/shell/vendor/three/`. The field laptop sits on the drone's WiFi AP with no internet, so there are no runtime CDN URLs.
   - Download a pinned release (e.g. `three@0.160.x` `build/three.module.min.js` plus `examples/jsm/controls/OrbitControls.js`) from cdn.jsdelivr.net once, commit the files, and record the version and source URL in `vendor/three/VERSION.txt`.
   - Check how the service serves static files (`ground_station/service/api.py`, the static_root handling). Make sure `.js` module files are served with a JavaScript MIME type. Fix that if needed and add a test.
   - If the shell loads plugins as classic scripts, not modules, use a dynamic `import()` from the plugin.
2. Scene:
   - Actual path: a solid line whose colour ramps with time, plus the current position marker (a small drone glyph or sphere with a heading arrow).
   - Desired path: a dashed line in a contrasting colour.
   - A ground grid, XYZ axes with labels, and an axis scale in metres. Use the same frame convention as the existing 2D panel (keep its data source and field names), and say in the report whether Z is up.
   - A legend and a live readout of the current tracking error (distance to the nearest desired point).
   - Buttons: reset view, the top/side/front/iso presets, "follow drone" toggle, "clear trail".
   - Keep the existing 2D view as a toggle (2D | 3D) and do not delete it.
3. Performance: use preallocated BufferGeometry with a ring buffer. Cap it (e.g. 20k points), update the draw range instead of rebuilding, and render only when the data or camera changed. There is no leak when the tab is hidden and shown repeatedly: dispose on unmount.
4. The same viewer must also work offline on a recorded session. Add a "Load session" option that fetches the recorded path from an existing API route if one exists (check `/api/routes` in the code, `/api/recording`, `/api/sessions`). If none exists, say so in the report; do not invent a server route in this task.
5. Tests: whatever the repo uses for shell plugins (look for existing JS or python tests of plugins, e.g. `browser_smoke`). At least a python test that the vendor files exist and are served with the correct MIME type. Run `python -m pytest ground_station/service -q`.

Report in `.agent-ops/out/t15-report.md`:

- files changed;
- the three.js version;
- the frame convention;
- which data keys feed the actual and desired paths;
- the test counts, verbatim;
- anything NOT done.
