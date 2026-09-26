# Task room3d-3d: shell header -> sidebar, and room-scale Paths 3D polish

Read `.agent-ops/tasks-src/vps-common.md` first; its rules apply. Digest: `.agent-ops/out/room3d-3d.md`.
Dashboard front end only. No firmware, no tier-0 files, no drone commands, no push, no hard deletes.


## Hard rules (a previous attempt was rejected for breaking these)
- Never commit node_modules, package.json, package-lock.json or logs/. `git status` before commit; stage files by name.
- No duplicated blocks, no empty handlers, no stubs. Every button you add must do what its label says.
- The digest lists exactly the files in `git diff --stat HEAD~1` and nothing else. Claims you did not verify = rejection.
- Node is available for the harness; do not npm install anything.

Do not touch shell/index.html or shell CSS (another worker owns them).

## B. Paths 3D (`docs/dashboard-platform/shell/plugins/path-panel.js`)
The lab flight volume is small: about 2 m^3. Everything must be room-scale (metres, trail data already in m).
1. Crisp rendering: `renderer.setPixelRatio(Math.min(devicePixelRatio,2))`, antialias on, resize via the
   existing ResizeObserver updates renderer size + camera aspect. Keep the existing rAF loop at end of
   `initThreeScene` (damped OrbitControls need update() each frame) and `frustumCulled=false` on ribbons.
2. Room box: configurable W x D x H (default 1.4 x 1.4 x 1.0 m), persisted in localStorage, drawn as a
   translucent wireframe box; floor grid 0.1 m minor / 0.5 m major lines; axes 0.3 m; camera near 0.01,
   presets (top/side/front/iso) and follow-cam framed to the room, not 20 m. Marker/arrow sized ~5-8 cm.
   Path points outside the box are drawn red and counted in the stats label.
3. Mouse path tracing: "Draw" mode in 3D (raycast onto a horizontal plane at a chosen altitude, slider
   0..H) and on the existing 2D canvas. Points thinned to >= 2 cm spacing, clamped to the room. Undo
   last point, clear, show the drawn path as the desired path.
4. Save/load via the existing `/api/paths` routes (read their server code + schema first; reuse the
   Path JSON schema and resample/smooth). List saved paths in a dropdown. Test via the service's
   test client in pytest, never a live server.
5. Presets fitted to the room (with a margin): hover, line, square, circle, figure-8, helix. Size and
   altitude parameters.
6. "Reset world origin": client-side display offset = current drone position (x,y; z optional) so the
   drone shows at the room centre; "Clear offset" button; persisted. Display only - sends nothing.
7. Polish: hemisphere + directional light, floor plane, drop line from drone to floor, trail coloured
   by time (or by tracking error when desired exists, with legend), "fit to path" camera button, a
   small quad-rotor drone model with heading instead of sphere+cone.

## Tests
- Extend `ground_station/service/tests/path_panel_harness.js` (node) for: presets inside room bounds,
  out-of-bounds count, origin offset math, draw-point thinning/clamp. It must print ALL CHECKS PASSED.
- pytest for any backend touch. Run `PYTHONUTF8=1 python3 -m pytest -q -p no:cacheprovider
  ground_station/service/tests` and `node ground_station/service/tests/path_panel_harness.js`; paste summaries.
- Keep functions render3D calls at plugin scope (harness CHECK 9).

## Digest
Changed files with line refs, exact test summary lines, what the supervisor must check in the browser.
