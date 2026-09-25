# T13: flight-test telemetry preset (PID-only vs MRAC, asymmetric load)

Do not contact 127.0.0.1:8081, do not touch the probe, and do not flash. Offline ELF lookups are allowed (`python -m ground_station.livewatch names --filter X` and `fields X` read `OBJ/JX_FLY.axf` only). Commit with LF line endings on your branch.

## Context

Today the operator flies real tests:

- (a) Baseline PID only.
- (b) PID with the MRAC adaptive layer active.
- (c) Both again with an asymmetric (off-centre) payload.

The logs must support a paper-grade comparison: tracking error, disturbance rejection, control effort, adaptation convergence, and the path flown against the desired path.

## Tasks

1. Read the firmware to find each variable and its exact DWARF name, verified with `names`/`fields`. Look in `TASK/`, `API/` and `USER/`; not in `stm32_lib/`, `FreeRTOS/` or `OBJ/` sources. Variables needed:
   - Attitude roll, pitch and yaw (deg).
   - Attitude setpoints / RC commands.
   - Body rates and rate setpoints (inner loop: `gyrox` is roll, `gyroy` is pitch).
   - PID outputs per axis: P, I and D terms if they are cheap, otherwise the total.
   - MRAC:
     - the enable flag or variant switch that says whether adaptation is active;
     - reference-model states;
     - tracking error;
     - adaptive parameters (`mrac_state.*.Theta` / `What`);
     - adaptive control contribution (`u_ad`);
     - any Simplex/freeze state.
   - Motor outputs (all 4) and throttle.
   - `vbat`, `arm`, `flymode`.
   - Position / velocity / optical-flow estimate, plus the position/path setpoint (for the 3D path plot).
   - Existing presets live in `ground_station/livewatch/multi_slot_presets.yaml`. Read `flight_comprehensive` and `mrac_characterization` first and reuse what they have.
2. Add a new preset `flight_test_adaptive` to that yaml that covers everything above within the link budget.
   - Four slots maximum.
   - Slot cost is `(7 + payload_bytes) * 100 / divider` B/s.
   - The total must stay at or below **1600 B/s** (the measured drop-free ceiling).
   - Put attitude, rates and setpoints at the highest rate (target ≥ 50 Hz if the budget allows), MRAC parameters slower, and vbat/status slowest.
   - Show the budget arithmetic.
3. Write `ground_station/analysis/flight_signals.yaml`. It maps analysis roles (for example `roll`, `roll_sp`, `motor1`, `mrac_active`, `theta_roll`, `pos_x`, `pos_x_sp`) to the telemetry key names the preset produces.
   - Keys arrive as `slot<N>.<dwarf name>`, or `<dwarf name>[i]` for arrays. Check `ground_station/comm/wifi_bridge.py` around `json_payload` and the recorder in `ground_station/service/` for the exact key format that reaches the CSV/JSONL.
   - The analysis script (another worker) reads this file.
4. Add a test: the preset loads, fits the budget, and every name resolves in the ELF (skip when the ELF is absent).
5. Run `python -m pytest ground_station/livewatch ground_station/analysis -q`.

Report in `.agent-ops/out/t13-report.md`:

- a table of role → DWARF name → type/length → slot/rate;
- the budget arithmetic;
- what could not be found (for example, if no position setpoint exists, say so plainly);
- how to tell PID-only from MRAC-active in the log;
- the verbatim test counts.
