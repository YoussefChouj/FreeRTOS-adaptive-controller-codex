Your summary is right. Go ahead with all of it, in this order, and open ONE PR against `overnight-2026-10-08`:

1. `excitation.py` generator + `API/sysid_ms_table.h` (deterministic; header comment with the achieved peak factor
   per axis and the table's flash size).
2. Firmware MULTI mode in `API/sysid.c/.h` with the axis mask, the Z guards as VAR_WRITE rows from id 128 up, and
   the injection sites in `TASK/StabilizerTask.c` (rate setpoints for roll/pitch/yaw, `Z_ratePID.Des` for Z).
   The one-axis mode must stay bit-identical (golden test).
3. The C harness tests in `API/tests/`, run by `python tools/host_tests.py`, fully green, all old tests passing.
4. `sysid4` stream group and its price in B/s at 100 Hz.
5. `frf.py` `direct_iv` + the damping-pole `fit_plant` option + the pytest cases from the brief. The old indirect path
   stays the default and its results are unchanged.

Do not wait for confirmation between steps. A failing test is a blocker: fix it before you open the PR.
In the PR description list each test and its result, the RAM/flash cost, and any point where you followed the
code instead of the brief.
