# S6 — Firmware experiment runtime

The experiment runtime is implemented in
`ground_station/platform/experiments.py`. It models the firmware experiment
contract without sending an excitation to the live aircraft:

- explicit `IDLE`, `SETTLING`, `MEASURING`, `COMPLETE`, and `ABORTED` states;
- exact parameter snapshots taken before updates;
- settling and measuring windows with event markers;
- safety predicate and operator abort paths;
- unconditional restoration of the original parameter map on completion or
  abort, including the reason marker.

The deterministic tests simulate a two-sample sweep, verify the exact restored
parameter dictionary, reject overlapping or unknown-parameter runs, and abort
on a safety predicate failure. `python -m pytest ground_station/platform/tests
-q` passes 17 tests. No live excitation or arming command was sent; the running
aircraft remains disarmed and the runtime is ready for S12 integration after
the remaining observability and service layers are in place.
