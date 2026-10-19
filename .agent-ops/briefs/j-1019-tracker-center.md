# Fix 10-19 live tracker startup corner latch and pre-arm trail jump line

Read AGENTS.md first. Touch ONLY `ground_station/analysis/live_tracker.py` and `ground_station/analysis/tests/test_live_tracker.py`. Do not touch `USER/`, `OBJ/`, or `logs/`.

## Context & Root Cause (2026-10-19 Lab Observation 1)
Operator observation from 2026-10-19 lab:
"Immediately after the live window tracker starts, it latches onto a corner of the drone's motor guard. Only after arming does the target circle jump to the center of the drone, leaving a straight line in the tracker trail."

Root causes in `ground_station/analysis/live_tracker.py`:
1. **Frame 0..29 (`static_n = 30`) missing `floor_scenery` mask (`live_tracker.py:L117-L200`):**
   - When `floor_scenery` is provided (from `--floor-dir` / `floor-1019/scenery.png`), `TrackCoral.__init__` leaves `self.static = None` until `len(self.static_buf) == self.static_n` (30 frames, ~5 seconds at 6 fps). During those first 30 frames, `step()` detects coral pixels without masking out the floor scenery, pulling the median centroid `(u, v)` onto a corner of the motor guard and locking `self.last_uv` (the 240 px local ROI) onto that corner!
2. **After `static_n` completes, `self.last_uv` stays trapped on the corner:**
   - When `len(self.static_buf) == self.static_n` builds `self.static`, `self.last_uv` is not reset, so the local ROI remains centered on the initial corner latch until `PadOrigin` or arming shifts it.
3. **`Preview.uvs` trail (`live_tracker.py:L790-L830`) accumulates pre-learning / pre-arm corner points and is never cleared:**
   - `Preview.draw` appends every `(u, v)` to `self.uvs` and draws `cv2.polylines(vis, [np.array(self.uvs, np.int32)], False, (0, 255, 255), 2)`. When the tracker jumps from the initial corner latch to the true drone center (after static learning or when arming), `self.uvs` still holds the pre-arm corner points, drawing a permanent straight yellow line from the guard corner to the center of the drone!

## Required Changes (`ground_station/analysis/live_tracker.py`)
1. **Apply `floor_scenery` from frame 0 (`TrackCoral.__init__` & `TrackCoral.step`):**
   - In `TrackCoral.__init__`: if `self.floor_scenery is not None`, initialize `self.static = self.floor_scenery.copy()` immediately so frame 0..29 already mask out the floor scenery while `self.static_buf` collects the first `static_n` frames.
   - In `TrackCoral.step`: keep collecting `self.static_buf` while `self.coral_static == "start" and len(self.static_buf) < self.static_n` (use a boolean `self._static_done: bool = False` instead of `self.static is None` to know whether the `static_n` startup frames have finished). When `len(self.static_buf) == self.static_n` and not `self._static_done`:
     - Compute the learned mask `learned = np.count_nonzero(np.stack(self.static_buf), axis=0) >= max(1, (self.static_n + 1) // 2)`.
     - Combine with `self.floor_scenery` if present (`learned = learned | (self.floor_scenery > 0)`), dilate as before, and set `self.static = learned`, `self._static_done = True`, `self.static_buf.clear()`.
     - Reset `self.last_uv = None` on the frame where `self._static_done` becomes `True` so the next detection re-centers on the full keep-disc / drone body instead of staying trapped in the pre-learning local ROI!
2. **Clear `Preview.uvs` (and pre-arm `PadOrigin` samples) when static learning completes and on arming transition:**
   - Add a `clear_trail(self)` method on `Preview` (`self.uvs.clear()`).
   - In the live loop (`run_live`, around `L510-L535` and `L790-L830`):
     - Track `was_armed = False` and `was_static_done = False`.
     - When `tr._static_done` transitions from `False` to `True` while not armed: call `preview.clear_trail()` and, if `origin` (`PadOrigin`) has not locked yet (`not origin.done`), clear `origin.buf.clear()` so `PadOrigin` only averages post-static-mask centered positions!
     - When `armed` transitions from `False` to `True`: call `preview.clear_trail()` so the trajectory trail shown in the window and recorded in `tracker.mp4` starts cleanly at the armed drone center without any pre-arm jump line.
3. **Unit Tests (`ground_station/analysis/tests/test_live_tracker.py`):**
   - Test that `TrackCoral` with `coral_static="start"` and `floor_scenery` applies `floor_scenery` from frame 0, still completes `static_n` learning at frame `static_n`, and resets `last_uv` when `_static_done` flips to `True`.
   - Test that `Preview.clear_trail()` empties `preview.uvs` and that transitioning from disarmed to armed (or completing static learning) leaves no pre-arm corner-to-center line in `preview.uvs`.

## Verification
Run and report in the PR:
- `python -m pytest ground_station/analysis/tests/test_live_tracker.py -q`
- `bash tools/check.sh doc-paths`
