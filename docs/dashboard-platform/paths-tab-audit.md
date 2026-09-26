# Paths Tab Audit

## Why the traces are missing

We investigated why the `Paths` tab was not drawing the actual and desired traces:
1. **Are the desired-position fields in any telemetry slot?**
   Yes. `Ctrler.locxPID.Des`, `Ctrler.locyPID.Des`, and `Ctrler.Z_posPID.Des` are packed into `frame_b` under the `pid.locx.Des`, `pid.locy.Des`, `pid.z_pos.Des` keys. They can also be dynamically streamed via the `flight_test_position` or `outer_loops` manifests.
2. **Does `extractPosition` find the actual position fields?**
   Yes, it correctly references `c.earth_x`, `c.earth_y`, and `c.altitude` (from `frame_c`).
3. **Is the BufferGeometry `drawRange` / `needsUpdate` set?**
   Yes, the three.js `_threeLineDesired` calls `setDrawRange` and sets `needsUpdate = true`.
4. **Are cm and m mixed?**
   Yes! `c.earth_x` and `c.earth_y` are published in cm, whereas `c.altitude` is in metres. The `pid.locx.Des` / `pid.locy.Des` fields are also in cm, while `pid.z_pos.Des` is in metres. The UI was not normalizing these units to metres uniformly. (This was just fixed by committing the outstanding diff).
5. **The Root Cause:**
   The `updateDesiredPath` function in `path-panel.js` completely ignored telemetry. Instead, it was iterating over `_waypoints` (the planned static path points) and pushing them into `_desiredPath`. As a result, the live desired trace (where the PID loop *actually* thinks it should be right now) was never recorded or drawn.

## Measurements
* **Cap/Limits**: `_MAX_3D_POINTS` and `MAX_TRAIL` are 20,000.
* **Performance**: With a real recording (e.g., `logs/activity/2026-09-26.jsonl`), drawing 20,000 points natively in `Float32Array` on `three.js` easily sustains 60fps. However, the 2D canvas drawing function caused lag by rendering a gradient over all 20,000 historical points per frame. (We fixed this by limiting the 2D gradient tail to the most recent 100 points in the uncommitted diff).
* **RAM**: The firmware uses `RW-data=2692 ZI-data=122012` (~124KB RAM). The F4 has 192KB, leaving ~67KB of free RAM.

## Decision on Canonical Symbols

* **Actual Position**: `c.earth_x / 100`, `c.earth_y / 100`, `c.altitude` (origin = takeoff point, units = metres).
* **Desired Position**: `pid.locx.Des / 100`, `pid.locy.Des / 100`, `pid.z_pos.Des` (units = metres).
* **Telemetry Budget**: Using `compute_multi_slot_budget` with the `flight_test_adaptive` preset (which loads `inner_loops`, `flight_test_outer`, `flight_test_position`), the telemetry load is 26,880 bps (29.1% of the 115200 limit). `safe=True`. Therefore, these fields easily fit in the telemetry budget without requiring a change to the payload size. 

## Execution Backend (Phase D)
For path execution, we will use the following approach:
* **D1 — Native Presets (No firmware change)**: The firmare already contains `AutoflyTask_RunCircle`, `RunSinusoid`, and `RunFigure8`. These map perfectly to the 0x0C, 0x0B, 0x11 commands + 0x12 (spacing). We will ship this first since it requires 0 firmware changes.
* **D2 — Arbitrary Paths**: A firmware waypoint buffer is recommended over host-streamed `TWC_TARGET` setpoints. Streaming over the WiFi UDP link means loss or jitter becomes reference jumps. 
  * We will use a chunked upload sequence (`PATH_BEGIN`, `PATH_CHUNK`, `PATH_COMMIT`). 
  * The coordinates will be compact `int16_t` cm to save RAM. Since we have ~67KB free RAM, allocating a static buffer of 1000 points (6 bytes each = 6KB) `g_path_buf` is completely safe.
