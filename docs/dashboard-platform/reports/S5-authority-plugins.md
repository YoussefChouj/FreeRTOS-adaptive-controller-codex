# S5 — Safety authority and plugin runtime

The existing firmware RC input seam already centralizes output authority,
physical takeover, and the 500 ms ground-station heartbeat watchdog. The new
host runtime contract in `ground_station/platform/plugins.py` makes the same
safety policy explicit for dashboard services:

- `PluginState` supports `disabled`, `shadow`, `candidate`, `active`, and
  `faulted` states with constrained transitions. Only `active` plugins may
  drive output; a fault immediately removes output authority.
- `AuthorityArbiter` provides exclusive ownership, owner-only heartbeats,
  explicit release, and deterministic heartbeat expiry.
- Static registry descriptors already identify the baseline PID, MRAC adaptive
  layer, EKF9 estimator, and optical-flow estimator.

Host checks pass for lifecycle restrictions, shadow-mode isolation, fault
handling, exclusive claims, and heartbeat expiry. A live disarmed transaction
(`0x5101`, command `0x0E`, value `0`) returned `ACK -> APPLIED`, confirming the
authority release path remains available on the running target without changing
arm state.

The hardware gate used the safe authority-release transaction while the target
was disarmed: command `0x0E`, index `0`, value `0` returned `ACK -> APPLIED`.
The subsequent telemetry stream continued with `status.arm=0` and no output
authority claim. Enabling SDK authority solely to force a heartbeat timeout
would invoke the firmware's arm preconditions and is therefore excluded from
the live-aircraft gate; the timeout and takeover paths remain covered by the
firmware implementation and the deterministic arbiter tests.

S5 is complete with the baseline PID/MRAC/estimator descriptors retained and
no candidate or active plugin enabled on the aircraft.
