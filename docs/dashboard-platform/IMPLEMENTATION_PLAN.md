# Implementation specification

## Goal

Create a long-lived, agent-native ground station for this STM32/FreeRTOS flight
controller. Wi-Fi/MicoAir is the operational data and command plane. Wireless
SWD/Keil remains the build, flash, probe, and recovery plane. The dashboard core
must not know firmware globals or byte offsets directly.

## Non-negotiable principles

- One typed registry generates firmware descriptors, telemetry schemas, command
  schemas, dashboard metadata, documentation, and tests.
- New controllers, estimators, telemetry groups, commands, plots, animations,
  and experiments are statically linked firmware/dashboard plugins.
- Commands are transactional: requested, accepted/rejected, applied, observed.
- A central safety/authority manager owns actuator authority.
- Every session records raw frames, decoded data, commands, acknowledgements,
  events, firmware identity, presets, operator/agent actions, and artifacts.
- Active and shadow controllers share inputs and produce comparable outputs.
- The current custom protocol remains primary; MAVLink is an optional adapter.
- All changes retain simulation tests and deterministic rebuild/flash gates.

## Firmware work packages

### F1 Identity and capabilities
Expose build ID, hardware ID, protocol/schema/registry versions, compiled feature
capabilities, plugin list, limits, and boot/reset cause. Reject incompatible
dashboard schemas safely.

### F2 Static typed registry
Add descriptors for variables, enums, parameters, commands, telemetry groups,
events, plugins, tasks, and resources. Descriptors include ID, name, type, unit,
scale, range, owner, update rate, permissions, safety class, dependencies, and
version. Use static tables suitable for Keil ARMCC; no runtime allocation.

### F3 Command service
Preserve existing wire encodings initially, but add transaction ID, command
name/target, precondition checks, acknowledgement, rejection reason, applied
value, and sequence. Provide idempotence and timeout semantics. Migrate numeric
handlers out of `send_data.c` into registry-backed command modules.

### F4 Event service
Emit sequence-numbered events for mode transitions, command outcomes, safety
interlocks, controller changes, estimator changes, path lifecycle, calibration,
watchdog expiry, deadline overruns, saturation, faults, and resets.

### F5 Safety and authority manager
Centralize emergency stop, RC/SDK authority, arm state, bench restrictions,
flight-state preconditions, heartbeat timeout, output ownership, and conflict
resolution. Controllers request authority; they never directly bypass it.

### F6 Plugin runtime
Define static interfaces for controller, estimator, adaptive layer, path, and
diagnostic plugins: init/reset/configure/update/state/health. Runtime states are
disabled, shadow, candidate, active, faulted. Declare inputs, outputs, resource
budget, safety level, and telemetry groups.

### F7 Experiment runtime
Implement a firmware state machine: IDLE, PRECHECK, CONFIGURE, SETTLE, RUN,
MEASURE, RESTORE, COMPLETE, ABORTED, FAULTED. Accept parameter sweeps, active or
shadow target, timing, safety limits, abort conditions, telemetry preset, and
restore policy. Host orchestration owns storage and analysis; firmware owns timing
and safety.

### F8 Resource observability
Expose task periods, execution maxima, runtime share, stack high-water marks,
heap minimum, queue occupancy, drops, DMA busy time, sensor freshness, and
deadline misses. Add diagnostic build profiles without destabilizing production.

## Protocol and schema work packages

- Define versioned envelopes with sequence, tick/timestamp, build ID, schema ID,
  payload length, flags, and CRC.
- Add capability/schema discovery over Wi-Fi.
- Keep multi-slot subscriptions; make slot manifests typed and generated.
- Add explicit command acknowledgements and event frames.
- Define bandwidth negotiation and rejection when requested telemetry exceeds
  budget.
- Keep SWD-only operations (flash, halt, memory probe) out of the flight command
  plane.

## Ground-station work packages

- Python service layer reusing current Wi-Fi bridge, decoder, manifests, and
  simulator.
- Local API with WebSockets for live state and HTTP for commands/configuration.
- SQLite metadata/event store plus CSV/Parquet telemetry artifacts.
- Raw-frame capture and deterministic replay.
- Plugin manifest system driven by firmware capability/schema data.
- Browser UI panels: connection/safety, estimator/bias, adaptive control,
  motor bench, paths, telemetry explorer, experiments, resources, replay.
- Agent API exposing current state, event history, commands, schemas, plots, and
  artifacts as structured JSON.
- Plotly-style interactive plots, with optional VOFA+/PlotJuggler adapters.

## Generated firmware/resource map

Generate JSON and Markdown from registry, ELF/DWARF, FreeRTOS declarations, and
telemetry manifests. Each item answers: who owns it, who reads/writes it, where
it lives, whether it is streamed/commandable, units/range, update rate, safety
conditions, and version. Include task/resource producer-consumer graphs.

## Safety requirements

- No arm/disarm, motor, path, parameter, or controller command without a visible
  precondition result and firmware acknowledgement.
- Motor bench commands require explicit bench state, disarm constraints, bounded
  output, heartbeat, and immediate abort.
- Parameter changes declare whether they are live-safe, boundary-safe, disarmed,
  or reset-required.
- Experiment abort must restore the baseline configuration or report failure.
- Stale telemetry and stale acknowledgements are visible and actionable.

## Acceptance gates

Each session must pass relevant unit/protocol tests, host simulation, Keil build,
ELF verification, and hardware smoke tests when its scope reaches hardware.
Release requires: no unknown schema fields, no unacknowledged commands, replay
equivalence, active/shadow comparison, safe abort, resource-map generation, and
documented rollback.

## Research-derived constraints

See `RESEARCH.md`. In particular, treat the MicoAir’s vendor range and throughput
figures as hypotheses to measure; preserve UDP framing defenses; keep all new
firmware facilities statically bounded for STM32F4; and use MAVLink/uORB/Foxglove/
OpenTelemetry as semantic references rather than dependencies.
