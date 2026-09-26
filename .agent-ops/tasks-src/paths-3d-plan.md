# Task: Paths tab — high-quality 3D view, mouse path authoring, path library, reproducible execution

You are working on the UAV firmware + dashboard repo at
`C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex` (STM32F4 firmware, Keil ARMCC V5.06;
Python ground-station service on port 8081; plain-JS dashboard plugins).
Before anything else, read `AGENTS.md`, `docs/dashboard-platform/AGENT_GUIDE.md` and `.claude_state.md`
(top block). If `.agent-ops/HANDOFF-2026-09-26.md` exists, read its HARD LIMITS section: it overrides this brief.

## Goal (operator's words, condensed)
The Paths tab's 3D visualization is low quality. The actual path and the desired path are not
drawn as traces. The operator also cannot author paths. Deliver:

1. A 3D view that is good enough for publications. It draws the actual (flown) trace and the desired
   (reference) trace live, plus the planned path as a preview.
2. A mouse path editor on a 2D plane (XY, XZ or YZ, with the third coordinate fixed). It has
   configurable point spacing and other parameters.
3. A path library: save, load, duplicate and version paths, so a path can be executed again exactly.
4. Parametric presets (circle, figure-8, sinusoid, line, square/polygon, helix). Their parameters
   are editable, saveable and executable.
5. Execute a path, abort it, and review the flown result against the plan afterwards.

## Hard limits (non-negotiable)
- **Never arm.** Never send MOTOR_BENCH (0x16) or any motor-spin command. Never send a path-execute
  command to a real drone. Execution is verified only on host tests, the firmware host test and
  a *disarmed* upload/readback dry-run. The operator executes real flights.
- Never POST to the live 8081 service, except through the dashboard MCP tools (if they exist in
  your session). Use `browser_smoke` and the local test server for UI checks.
- Never flash while armed. Firmware flash safety net:
  1. Before the first flash, `git tag` the current known-good firmware and copy `OBJ/JX_FLY.axf/.hex`
     to `firmware_backups/<tag>/`.
  2. After every flash, run `python -m ground_station.livewatch verify` and a disarmed telemetry check.
  3. If either fails, reflash the tag. Never leave unverified firmware on the drone.
  4. If you are a worker without flash authority, stop before flashing and report instead.
- Never hard-delete. A deleted path goes to a trash folder (or the Recycle Bin), never `rm`.
- Record only numbers you measured. No invented baselines (RAM, latency, RMS error).
- Commits contain no `patch_*.py` and no binaries (the tracked OBJ outputs are the exception, and
  only after a real build). Use the attribution trailer that `.claude_state.md` / handoff specify.
- Firmware conventions: C89-style (declarations at block top, no VLAs), match surrounding style.
  `TASK/AutoflyTask.c` is flight-critical (tier 0), so any change there needs a host test first.

## What exists today (verify, don't trust)
- **Panel:** `docs/dashboard-platform/shell/plugins/path-panel.js` (~1320 lines, has an
  **uncommitted diff** — `git diff` it first and decide keep/commit/revert with reason).
  - It has a 2D canvas (trail, planned path, waypoints table, auto-fit) and a three.js scene.
  - three.js is loaded from local vendor files: `/vendor/three/three.module.min.js` and `OrbitControls.js`.
    See `test_three_vendor.py`.
  - The scene holds `_threeLineActual`, `_threeLineDesired`, the drone marker, grid, axes and view presets
    top/side/front/iso.
  - `extractPosition(state)`, `metreAltitude()` and `lookupValue()` pull position from `/api/state`.
  - The panel loads a recording via `/api/recording`.
  - `MAX_TRAIL=20000`. There is a `_MAX_3D_POINTS` cap.
- **Tests:** `ground_station/service/tests/test_path_panel.py`, `test_path_panel_markup.py`,
  `test_path_generator.py` (find the generator module it covers), `test_three_vendor.py`.
- **Firmware:** `TASK/AutoflyTask.c`.
  - Presets `AutoflyTask_RunCircle`, `RunSinusoid`, `RunFigure8`, arbitrated by
    `AutoflyTask_PathArbitrate`.
  - All of them feed `AutoflyTask_CommitRef(x,y,z)`, a waypoint-density quantizer driven by
    `waypoint_spacing` (cm). x/y are in cm and z in m: watch the units.
- **Command contract:** `ground_station/platform/firmware_contract.py`.
  - 0x0A TWC_TARGET (x,y,z m, yaw, execute).
  - 0x0B SINUSOID_PATH, 0x0C CIRCLE_PATH, 0x11 FIGURE8_PATH, 0x0D ABORT_ALL_PATHS, 0x12 WAYPOINT_SPACING.
  - Every path command requires SDK mode and has `danger_level` dangerous/caution.
- **Manifest drift:** changing a firmware symbol breaks `test_capability_manifest_no_drift`.
  Regenerate with `python -m ground_station.platform.capability_manifest`.
- **Telemetry budget:** `from ground_station.livewatch.manifest import ManifestStore,
  MultiSlotPresetManager, compute_multi_slot_budget`. The budget must satisfy `budget.safe`
  (≤1600 B/s). Presets are in `ground_station/livewatch/multi_slot_presets.yaml`.

## Strategy
Work in phases. Commit and push at each verified phase boundary. Phases A–C are pure
dashboard/service work with zero flight risk, so do them first. Phase D (firmware) is last and
gated.

### Phase A — Diagnose before rewriting (short, written down)
Produce `docs/dashboard-platform/paths-tab-audit.md` with measured findings:
- **Why the traces are missing.** Check each of these:
  - Are the desired-position fields (loc PID `Des`/reference, e.g. `Ctrler.*PID.Des` or whatever
    `CommitRef` writes) in any telemetry slot at all?
  - Does `extractPosition` find the actual position fields (EKF `s_ekf.x` position vs
    loc-PID feedback)?
  - Is the BufferGeometry `drawRange`/`needsUpdate` set after each update?
  - Are cm and m mixed?
- **Measurements.** Frame rate and point count with a real recording.
- **Screenshots.** Take them from `python -m ground_station.service.browser_smoke` or the local test server.
- **Decision.** Decide which firmware symbols are the canonical "actual" and "desired" position,
  in metres, in one world frame (state the axis convention and origin: takeoff point).
  Check whether they fit the telemetry budget. If they don't fit, propose a slot/preset change
  with `compute_multi_slot_budget` numbers.

### Phase B — 3D view quality
Refactor in place. Split into modules only if the file becomes unmanageable, and follow the
plugin conventions of the other panels.
- **Data layer.** One ring buffer of `{t, actual xyz, desired xyz}` in metres, fed from live state
  *and* from a loaded recording.
  - Implement it as preallocated `Float32Array` position attributes with `setDrawRange`.
  - Never reallocate geometry per frame.
  - Render with `requestAnimationFrame` only when the panel is visible and dirty.
- **Traces.**
  - Actual trace: solid, optionally coloured by tracking error or by time.
  - Desired trace: dashed or contrasting colour.
  - Planned path: faint preview with numbered waypoint spheres.
  - Optional error vectors actual→desired at a decimated rate.
  - Use fat lines (`Line2`/`LineMaterial` from three addons). Vendor the files locally next to
    the existing three files, add them to `test_three_vendor.py`, and use no CDN.
  - Fall back to `THREE.Line` if the addon fails to load.
- **Scene quality.**
  - Antialiasing, `devicePixelRatio` capped at 2, and a `ResizeObserver` so the canvas is never stretched.
  - A grid scaled to the data bounds with metric labels, axis labels X/Y/Z (m), and a ground
    projection (shadow) of the traces for depth perception.
  - A drone marker oriented by attitude (roll/pitch/yaw), a legend, and a light/dark theme via the
    dashboard's CSS tokens.
  - Keep the view presets and follow-drone. Add "fit to data".
- **Playback.** When a recording is loaded, add a time scrubber and play/pause/speed controls with
  a trail up to the cursor. Show live metrics: RMS, max and per-axis tracking error, path progress
  %, and elapsed time.
- **Export.** PNG screenshot of the 3D view. CSV of the trace (t, actual, desired, error).

### Phase C — Path editor, presets, library (service + UI, no firmware change)
- **Path model.** Define one versioned JSON schema (`schema_version`) and document it in
  `docs/dashboard-platform/path-format.md`. Fields:
  - `id`, `name`, `created`, `notes`
  - `frame`: world frame, origin = takeoff/home, units m
  - `kind`: `freehand` | `polyline` | `preset`
  - `plane` + `fixed_coord` for drawn paths
  - `preset` `{type, params}` for parametric paths
  - `points` `[[x,y,z],...]` after resampling
  - `params`: spacing_m, speed_mps, yaw_mode (fixed/tangent/heading-hold), yaw_deg, dwell_s,
    start_mode (from current position / fly-to-start), loop count, smoothing
  - `limits`: geofence box, z_min/z_max, max speed, max accel
  - `sha256` of the canonical points + params, for reproducibility
- **Shared geometry module (Python).** Extend or replace the module that `test_path_generator.py`
  covers. It is the single source of truth, and the UI calls it through a new GET/POST
  `/api/paths/...` family. It must provide:
  - arc-length resampling at `spacing`
  - Chaikin or Catmull-Rom smoothing
  - RDP simplification
  - preset generators (circle, figure-8, sinusoid, line, polygon, helix) that produce the same
    point list the firmware presets would
  - a time parameterization from speed (trapezoidal velocity profile with max accel)
  - validation that returns explicit errors: out of geofence, spacing too small for the upload
    limit, speed/accel exceeded, self-intersection warning only
  - Host unit tests for every function.
- **Editor UI.**
  - A 2D canvas with a plane selector XY/XZ/YZ, a fixed-coordinate input, a metric grid with
    snap, and a zoom/pan that matches the 3D bounds.
  - Modes: freehand draw (mouse down/move/up, then resample), click-to-add polyline, drag points,
    insert/delete points, undo/redo, clear.
  - A parameter form bound to the schema above. The result previews live in the 3D view as the
    planned path.
  - Show the point count, length, estimated duration and validation state.
  - Presets appear as the same form, with a type dropdown and param fields. "Convert to editable
    points" is optional.
- **Library.**
  - Server-side store under the service's existing data/storage convention: find how `storage.py`
    places files, one JSON file per path.
  - Operations: list/search, save, save-as (version bump), duplicate, rename, and delete-to-trash.
  - Import/export `.json`.
  - Path writes go through a new service route. Follow how existing write routes are guarded
    (tier/permission checks, `api.py` patterns). Register it so `GET /api/routes` and
    `AGENT_GUIDE.md` list it.
- **Session linkage.** When a path is executed, write the path `id` + `sha256` + params into the
  recording's session metadata (see `_manifest_context` in `core.py`), so a recording can reload
  its exact plan and the replay overlays plan vs flown.

### Phase D — Execution (design first, firmware gated)
Pick an execution backend and justify it in the audit doc:
- **D1 — native presets (no firmware change).** circle/sinusoid/figure-8 presets map onto
  0x0C/0x0B/0x11 + 0x12 spacing, and use native firmware generation. Ship this first.
  - UI: an "Execute" button through the existing command-panel/approval-queue flow, with the
    command's danger level. SDK mode is required.
  - An **Abort (0x0D)** button stays visible and always enabled while a path is active.
  - A pre-flight checklist gates execution: SDK mode, EKF healthy, position valid, path validated,
    geofence OK.
- **D2 — arbitrary paths.** Recommend a **firmware waypoint buffer** over host-streamed
  TWC_TARGET setpoints. Streaming over the WiFi UDP link means loss or jitter becomes reference
  jumps, and TWC semantics may be non-streaming: read the firmware to confirm. Design:
  - **Upload.**
    - Use new chunked command(s), e.g. PATH_BEGIN(n, crc), PATH_CHUNK(index, k points), PATH_COMMIT.
    - Use compact points (int16 cm, or float) and a static buffer sized from *measured* free RAM:
      read the map file/`OBJ/JX_FLY.htm`, and report the numbers.
    - The firmware verifies the CRC and exposes `g_path_buf` status (count, crc, state) so the
      host can confirm the upload by telemetry/probe readback.
  - **Follower.** Add `AutoflyTask_RunBuffer()`. It advances a reference along the buffer by time
    (speed profile computed on the host, sent as per-segment dt or a global speed) and feeds
    `AutoflyTask_CommitRef`. It joins the `PathArbitrate` mutual exclusion and 0x0D abort, and
    uses the same yaw modes.
  - **Safety.**
    - The follower refuses to start unless it is in SDK mode, the upload is complete and the
      CRC is valid, and the first point is within X m of the current position (otherwise it does a
      fly-to-start leg at limited speed).
    - It stops on EKF unhealthy or link loss, following the existing failsafe behaviour. Document
      which one applies.
  - **Tests.** Host test in `tests/firmware_host/` covering: CRC reject, bounds, follower progress,
    abort, and arbitration with the other presets. Add contract entries in `firmware_contract.py`.
    Regenerate the manifest.
  - **Verification.** Build, flash (safety net above), `livewatch verify`, then a disarmed dry-run:
    - Upload a saved path and read back count/crc via the probe.
    - Do **not** send start/execute on the real drone.
    - The operator does the first real execution.

### Phase E — Post-flight review
Load a recording and overlay its linked plan. Compute tracking metrics (RMS/max per axis,
lateral/cross-track error, time lag) in the Python module, not only in JS. Show them in the panel
and in `analyze_session` if that tool exists. Export a small report (PNG + CSV + metrics JSON).

## Verification required per phase
- Run the full pytest tree (not a subdirectory). The whole tree must be green.
- Run `browser_smoke` screenshots of the Paths tab before and after. Attach the paths in your
  report.
- For JS: add/extend markup/behaviour tests the way `test_path_panel*.py` do.
- For firmware:
  - Keil build: `UV4 -b -t JX_FLY -j0 JX_FLY.uvprojx` from `USER/`.
  - Host test passes.
  - Manifest regenerated.
  - Flash only with authority.
- Numbers go into the docs only if measured this session (fps, point caps, RAM, upload time).

## Deliverables
- Code for phases A–C (and D1). D2 is fully implemented if you have flash authority; otherwise it
  is designed + host-tested up to the build.
- Docs:
  - `paths-tab-audit.md`
  - `path-format.md`
  - an updated `AGENT_GUIDE.md` route list
  - a short operator guide section: how to draw, save, execute and abort a path
- Final report with:
  - what changed, per phase
  - test counts
  - screenshots
  - measured numbers
  - open risks
  - an explicit list of the steps that need the operator: first real execution, any arming,
    and anything you chose not to do

Update `.claude_state.md` at the end.
