# Jules task j-sysid-torque: plant-input (torque) injection target for API/sysid.c

Repo rules: AGENTS.md; firmware tunables follow docs/firmware-table-pattern.md (aligned *_ROW macro tables).
Base branch overnight-2026-10-08. You cannot run Keil: host C tests only (`python tools/host_tests.py`).

Today API/sysid.c generates chirp / multisine and adds it to ONE axis's RATE SETPOINT (reference injection).
Add a second target, selected at runtime:
- sysid target 0 = setpoint (today's behaviour, bit-exact, the default); 1 = torque: add d to that axis's rate-loop
  OUTPUT (torque command c) just before the mixer, so the plant input is u = c + d.
- Per axis, expose and stream c, d and u (u = c + d) as float telemetry in a new subscribe group `sysid_cdu`
  (follow how existing groups are declared; stay within the existing stream guard/budget logic).
- Safety: d = 0 unless sysid is running AND airborne; per-axis amplitude clamp in a *_ROW table (PROPOSED values,
  commented as such); d forced to 0 on any safety trip or land; the target can only change while sysid is stopped
  (refuse otherwise, in the existing error-code style).
- The target selector is VAR_WRITE-able, registered like the existing sysid parameters; update the ground-station
  var catalog / firmware_contract if they enumerate sysid vars.
- Host C test (new file under tests/firmware_host or API/tests, wired into tools/host_tests.py): target 0 output is
  bit-exact to before; target 1 leaves the setpoint untouched and adds d to c; u == c + d; clamp holds; d == 0 when
  not airborne / stopped / tripped; target change refused while running.

## Acceptance
`python tools/host_tests.py` all pass (26/26 before, plus yours); scoped pytest for any touched Python passes; PR body
lists every touched file:line and the default-behaviour guarantee.
