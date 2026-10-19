Approved, proceed to the PR without further questions unless blocked. Decisions:

1. Ground effect: ONE file `lab_z_ground_effect.fly`. Take off at 0.8 m, hold 12 s, then out-and-back `goto` legs with
   only z set (x=0 y=0) and dwell_s=12 at z = 0.3, 0.5 and 1.2. The lowest height is whatever the existing parser and
   trajectory_pipeline.validate accept (0.3 m): do NOT change the parser, the fence or TrajLimits. If a height is
   rejected, drop it and list it. Header + PR body + run sheet: "below 0.3 m, ground effect is seen only in the
   takeoff and slow-landing segments (DSL/fence gap)".
2. Vertical-speed steps: please do NOT stub if the DSL can do it. A `goto` leg is a constant-speed ramp out and back,
   so from hover at 1.0 m use z-only `goto z=1.4` and `goto z=0.6` legs with the leg speed set to 0.2 m/s, then
   0.4 m/s (use the existing motion key that sets leg speed: see DEFAULT_MOTION in
   ground_station/service/scenario_schema.py; per-step k=v or the `motion` header). Each leg gives a +v and a -v
   segment. Stub only if no speed key exists, and say which key is missing.
3. Memory files: 104-105 s is too close to the 120 s cap given estimate error. Trim to <= 100 s computed
   (e.g. A1 28 s, B about 28 s, A3 28 s) and keep the analysis windows valid (A1 last 10 s, A3 first 5 s);
   update memory_premise.py segment timing to match.
4. Then memory_premise.py, tests, run-sheet section exactly as the brief says. Mark numbers PROPOSED.
