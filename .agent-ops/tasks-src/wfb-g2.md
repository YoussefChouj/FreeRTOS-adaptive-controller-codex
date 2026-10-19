<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including all existing tests,
   conftest.py, model.py, registry.py, pipeline.py, config/*.yaml and schema/*.json.
2. Make every assertion pass by fixing implementation code. Keep existing tests and assertions exactly
   as they are. [ImpossibleBench, arXiv 2510.20270]
3. Write complete implementations. Every function body does real work; no "...", TODO, `pass` stubs or
   "rest of code" comments.
4. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
6. Catch only specific exceptions (ValueError, KeyError, ...) and re-raise or record them in "warnings".
   Bare `except:` and `except Exception: pass` are forbidden. [general, arXiv 2603.28592]
7. Compare floats with `np.isclose`/`pytest.approx`; test NaN with `np.isnan`/`math.isnan`.
8. State array axes explicitly (`axis=0`); write interval ends as half-open [t0, t1) unless told otherwise;
   check lengths before slicing. [general, arXiv 2511.18782]
9. Keep every function signature, name and return shape exactly as the brief specifies.
10. Import only: python stdlib, numpy, scipy, pandas (loaders only), matplotlib, jsonschema, yaml.
    Use only functions you have seen documented; when unsure, check with `python -c "help(...)"`.
11. If a command or edit fails twice the same way, change approach; never repeat an identical call.
12. Write the digest `.agent-ops/out/<id>.md` BEFORE printing DONE; the supervisor rejects a run whose
    digest file is missing, regardless of exit code.
13. Research tasks: cite canonical URLs only (no vertexaisearch / google.com/url redirects); quote <= 25 words.
14. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
Repository: STM32F407 flight-controller firmware plus a Python ground station (package `ground_station`).
This is "task G2" of workflow B (autonomous tuning flights): the host-side trajectory pipeline. It turns a
shape into a timed list of points that the firmware accepts, and it predicts the firmware's verdict.
Read first, fully: docs/workflow-b/interfaces.md sections 0, 1 (the CMD 0x1B table and "COMMIT checks")
and 4 (the `service/trajectory_pipeline.py` row); API/wfb_traj.c function wfb_traj_commit (the firmware
side of the checks your `validate` must reproduce exactly); the existing path library (find it with
`git ls-files | grep path_library`; stored paths are dicts of points in centimetres).
Frame: x forward, y left, z up, metres; yaw in degrees; t in seconds. The hover point is (0, 0, hover_z).
Every trajectory starts and ends at the hover point. Python 3.10+, standard library only (math, struct,
zlib, dataclasses). Type hints on every public function. No I/O except reading a stored path in the
"library" shape. No global mutable state.
</context>

<task>
Implement the trajectory pipeline module and its tests.

ALLOW-LIST (create only these two files, nothing else anywhere):
- ground_station/service/trajectory_pipeline.py
- ground_station/service/tests/test_trajectory_pipeline.py

Interface (names and signatures exact):
- @dataclass(frozen=True) TrajPoint: x: float, y: float, z: float, yaw_deg: float, t: float
- @dataclass(frozen=True) TrajLimits: x_abs_m=0.8, y_abs_m=1.3, z_min_m=0.3, z_max_m=1.5, v_max_mps=1.0,
  endpoint_tol_m=0.10, yaw_abs_deg=180.0, max_points=600
- @dataclass(frozen=True) Profile: v_cruise_mps: float, a_max_mps2: float, ds_m: float, hover_z_m: float,
  yaw_deg: float = 0.0
- SHAPES: dict[str, callable] with keys "line", "circle", "figure8", "square", "library".
  Each shape function takes `params: dict` and returns a closed list of (x, y) tuples in metres that
  starts and ends at (0.0, 0.0), dense enough that straight segments between them follow the shape within
  1 mm. params: line {length_m, heading_deg} (out and back); circle {radius_m} (starts at the origin,
  tangent to the x axis); figure8 {width_m, height_m} (crossing point at the origin); square {side_m}
  (one corner at the origin); library {path_id} (loads through the path library, converts cm to m,
  shifts the first point to the origin and appends a closing point when the path is open).
  A missing or non-positive parameter raises ValueError naming the parameter.
- shape_xy(shape: str, params: dict) -> list[tuple[float, float]]        (step 1; unknown shape -> ValueError)
- tilt(points_xy, hover_z_m: float, tilt_deg: float, axis_deg: float) -> list[tuple[float, float, float]]
  (step 2) Rotates the flat shape, lying in the plane z = hover_z_m, by tilt_deg about the horizontal
  axis through the hover point whose heading is axis_deg. tilt_deg = 0 returns z = hover_z_m everywhere.
- resample(points_xyz, ds_m: float) -> list[tuple[float, float, float]]
  (step 3) Fixed arc-length resample along the 3-D polyline: first and last points kept exactly,
  interior spacing equal (arc length / ceil(arc length / ds_m)). ds_m <= 0 raises ValueError.
- time_profile(points_xyz, profile: Profile) -> list[TrajPoint]
  (step 4) Trapezoidal speed along arc length: accelerate at a_max_mps2 from rest, cruise at
  v_cruise_mps, decelerate to rest at the end; triangular when the path is too short to reach cruise.
  t[0] = 0.0, t strictly increasing, yaw_deg = profile.yaw_deg on every point.
- generate(shape: str, params: dict, profile: Profile, limits: TrajLimits = TrajLimits()) -> list[TrajPoint]
  Runs steps 1 to 4 (tilt_deg and axis_deg come from params, default 0.0), then `validate`; raises
  ValueError carrying every validate message when the list is not empty.
- validate(points: list[TrajPoint], limits: TrajLimits, hover_z: float) -> list[str]
  Returns [] when the firmware would accept the upload. Works on float32-rounded values
  (struct.unpack('<f', struct.pack('<f', v))[0]) because the firmware sees float32. Checks, in this order,
  each message starting with the tag shown and naming the point index:
  COUNT  (fewer than 2 points or more than limits.max_points)
  RANGE  (any non-finite field)
  TIME   (t[0] != 0, or t not strictly increasing)
  BOUNDS (|x| > x_abs_m, |y| > y_abs_m, z outside z_min_m..z_max_m, |yaw_deg| > yaw_abs_deg)
  ENDPOINT (3-D distance from the first or the last point to (0, 0, hover_z) > endpoint_tol_m)
  SPEED  (3-D distance between consecutive points / their time difference > v_max_mps)
  All problems are reported, not only the first. The order of the returned list follows the order above.
- crc32(points: list[TrajPoint]) -> int
  zlib.crc32(struct.pack('<%df' % (5 * n), x0, y0, z0, yaw0, t0, x1, ...)) & 0xFFFFFFFF

Required tests (plain pytest functions, pytest.approx for floats):
1. Each shape in SHAPES except "library" is closed, starts at (0, 0) and rejects a missing and a
   non-positive parameter. "library" is tested with a monkeypatched path-library loader returning a small
   open path in centimetres: the result is in metres, starts at the origin and is closed.
2. tilt: 0 deg keeps z == hover_z; a circle of radius 0.3 tilted 30 deg about axis 0 has
   max z - min z == 2 * 0.3 * sin(30 deg) (abs 1e-3) and keeps every 3-D distance to the circle centre.
3. resample: consecutive spacing equal within 1e-9; first and last points unchanged; ds_m <= 0 raises.
4. time_profile: t[0] == 0; strictly increasing; peak segment speed <= v_cruise (rel 1e-6); a long line
   reaches cruise, a short one is triangular (peak < cruise); total time of a long straight path equals
   L / v + v / a (abs 1e-6) before resampling effects, tested on a 2-point path.
5. generate: default limits accept circle radius 0.3 with Profile(0.3, 0.5, 0.02, 0.5); a circle of radius
   1.0 raises ValueError whose text contains "BOUNDS"; a profile needing more than 600 points raises with
   "COUNT"; Profile(1.5, 5.0, 0.02, 0.5) on the radius 0.3 circle raises with "SPEED" (a_max 5.0 so the
   1.9 m path really reaches 1.5 m/s).
6. validate, one test per tag, on hand-built lists: COUNT, RANGE (NaN), TIME (t[0] = 0.1; equal t),
   BOUNDS (x; z below; yaw 180.5), ENDPOINT (first; last), SPEED. A list with two different problems
   returns both messages in the documented order. A valid list returns [].
7. crc32 of two known points equals zlib.crc32 of the hand-packed 40 bytes, and changes when one
   coordinate changes by one float32 step (math.nextafter on the float32 value is not required: add 1e-3).

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/service/tests/test_trajectory_pipeline.py -q

Digest: write .agent-ops/out/wfb-g2.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
