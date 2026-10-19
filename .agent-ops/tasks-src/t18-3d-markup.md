# T18: the Paths tab's 3D view is unreachable because its markup was never added

Do not contact 127.0.0.1:8081, UDP 14550 or the probe, and do not flash. Commit with LF line endings on your branch.

## Evidence (supervisor, measured on main 1fc90e5, 2026-09-25)

In `docs/dashboard-platform/shell/plugins/path-panel.js`, the code references these IDs, but no element with them exists anywhere in the rendered markup (the template around line 1022 only has `pp-2d-wrap`):

| ID | Referenced at |
|---|---|
| `pp-view-2d` | line 1160 |
| `pp-view-3d` | line 1161 |
| `pp-3d-wrap` | lines 1165, 1178, 1228 |

- The CSS for `.pp-view-toggle`, `.pp-view-btn`, `.pp-3d-btn` and `.pp-3d-legend` exists (lines 998–1007).
- The live served file contains `pp-view-3d` exactly once: the `q()` lookup.
- A headless Chrome clicking `[id$="pp-view-3d"]` on the Paths tab timed out.
- `/vendor/three/three.module.min.js` returns 200.

## Task

1. Read the whole 3D section: the state at around line 31, `initThreeJS`, the render loop, OrbitControls, the reset/preset view buttons if any, the legend, and the 2D/3D toggle handlers at around lines 1158–1240.
2. Add the missing markup to the panel template, next to `pp-2d-wrap`:
   - a `.pp-view-toggle` containing `pp-view-2d` (active by default) and `pp-view-3d` buttons;
   - a `pp-3d-wrap` div, hidden by default, sized like the 2D canvas wrap, with `position: relative` so the legend/buttons can overlay it;
   - any other elements the 3D code looks up by id or class (legend, view buttons such as top/side/iso/reset, an info line).
3. Make sure `initThreeJS` appends its WebGL canvas into `pp-3d-wrap`, sizes it to the wrap, and resizes on window/wrap resize.
4. Make sure the 3D scene draws:
   - the actual path, from the same data the 2D view uses;
   - the desired path/waypoints, in a different colour;
   - axes/grid;
   - the camera auto-fitted to the data.
   - OrbitControls must allow left-drag rotate, wheel zoom and right-drag pan.
5. Add a static test (Python, in `ground_station/service/tests/`) that parses `path-panel.js` and asserts that every literal id passed to `q('...')` / `getElementById('...')` appears in an `id="..."` in the file's markup strings. This guard would have caught this bug.

## Finish

Run `python -m pytest ground_station/service -q`.

Report in `.agent-ops/out/t18-report.md`:

- what was missing;
- the ids added;
- the verbatim test counts;
- what was NOT done (for example, no real-browser check; the supervisor does that).
