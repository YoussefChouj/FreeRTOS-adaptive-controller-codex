Answers. Do all four, then open the PR.

1. zoom/crop and detect timing in CoralTracker.step(): keep the signature. Add an attribute `self.last_ms = {}` (set in __init__). In step(), take time.perf_counter() around the crop+resize block and store last_ms["crop"]. Take it again around coral_mask + static mask + detection and store last_ms["detect"]. That is two perf_counter calls per stage, nothing else. ab_replay reads tracker.last_ms after each step(). Time decode yourself around cap.read().

2. draw timing: add `headless: bool = False` to Preview.__init__. When it is True, skip namedWindow, imshow, waitKey, window placement and moving. Everything that builds the image still runs: the trail, the PiP, the 3D panel, the label, and the recorder if rec is set. ab_replay builds Preview(headless=True, hz=<the video fps or higher>) so that every frame draws, and times __call__ as "draw". Preview rate-limits by hz (t_last/dt), so count draw ms only on frames that actually drew, and report that count. Do not call _draw_landing_pip directly.

3. Tests (synthetic video, no real logs), each one asserted:
   - both runs detect the ring on at least 90% of frames;
   - ab_frames.csv: run B's zoom equals land_zoom exactly on the frames where z <= land_z_m and equals zoom on all the others (feed a z track that crosses land_z_m);
   - ab_summary.md exists and has p50/p95 rows for decode, crop, detect and draw, plus fps;
   - flags reach the tracker: parse --zoom 1.5 --land-zoom 3 --land-z-m 0.4 through the real parser and the real construction path, then assert the CoralTracker attributes;
   - the label shows the real zoom: factor the label text into a small pure function (for example pip_label(z) -> "LAND ZOOM 3.0x"), use it in _draw_landing_pip, and test it;
   - headless: monkeypatch cv2.imshow and cv2.namedWindow to raise, and run Preview(headless=True) once to show they are never called.

4. pandas: use the csv module unless live_tracker.py already imports pandas at module level. Ruff must be clean on the lines you touch. Do not reformat lines you do not touch: the file has 5 old E702s, leave them.

Run `pytest ground_station/analysis -k "tracker or replay" -q` and ruff on the changed files. Put both results in the PR description.
