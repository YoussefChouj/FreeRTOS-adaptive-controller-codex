# UAV Ground Station Improvement Specification

This is the implementation contract for subsequent coding sessions. Work in the order below. A work package is complete only when its acceptance evidence is attached to the session report and the live browser view model agrees with the wire/probe evidence.

## Target architecture

Use a device-bound, metadata-driven platform with four layers:

1. **Firmware contract**: versioned signal catalog, command catalog, telemetry capability negotiation, bounded framing, source timestamps, counters, and explicit safety preconditions. Keep EKF shadow-only and preserve the no-motor-initialization rule.
2. **Transport/host core**: one Wi-Fi owner, a typed subscribe state machine, raw-frame recorder, decoder, freshness calculator, command lifecycle, and persistent event journal. SWD remains a separate recovery/readback adapter.
3. **Semantic store**: immutable raw samples plus typed signals (`value`, unit, source, sequence, received time, source time, freshness, quality, build/schema ID). Replay and live data must enter the same reducers.
4. **Plugin workspaces**: controller, estimator, safety, telemetry/capture, paths, motor bench, experiments, replay, and diagnostics. Plugins declare required signals, commands, safety gates, view model, and tests; the shell supplies navigation, permissions, lifecycle, and observability.

## Ordered work packages

### WP0: Evidence and provenance

Add `/api/diagnostics/bundle` and a CLI equivalent that records process command line/cwd, git HEAD and dirty state, ELF hash, schema ID, transport owner, health, recent raw frames, decoded samples, command journal, plugin/view-model snapshot, console errors, and a screenshot. Add an evidence ledger with `source_confirmed`, `live_observed`, `replayed`, or `unverified` status. No report may say “fixed” without a reproducible journey and evidence bundle.

### WP1: Contract generation and safety truth

Create one versioned manifest (JSON/YAML source) generating firmware-facing IDs, host decoder metadata, UI labels, units, ranges, readback signals, command preconditions, and dangerous-action policy. Include exact 0x1E bias modes, 0x0F flag/mode indices, 0x04 abort/recovery semantics, and all firmware-supported commands. Reject unknown or stale build/schema IDs. Preserve raw float precision. Add contract tests that compare generated definitions with `send_data.c` handlers and DWARF names.

### WP2: Repair subscribe transport

Make request sizing a negotiated capability, not a macro contradiction. Either increase every actual DMA/staging buffer safely or split requests into bounded batches; advertise the true maximum and reject oversized host plans before transmit. Preserve requested slot, ranges, divider, and transport; remove slot-0 hidden dashboard override. Give each request an ID and explicit states: planned, sent, schema received, streaming, degraded, stopped, failed. Serialize Wi-Fi ownership and queue USART3/USART5 ingress without a shared mutable reply transport. Add offline tests for 30, 31, 54 ranges and malformed/partial/CRC frames, then a live capture proving schema plus data frames and nonzero samples.

### WP3: Truthful semantic state

Implement per-signal freshness and quality, with `unknown`, `stale`, `replay`, `degraded`, and `live` states. Never display “current” for null or optimistic values. Commands expose `submitted`, `acknowledged`, `applied`, `rejected`, `timed_out`, and `observed`; `applied` is not `observed`. Parameter panels show desired, firmware-reported, last-observed, age, and mismatch. Add readback subscriptions for safety limits, mode, authority, bench state, flags, estimator state, and command result.

### WP4: Operator shell and plugins

Replace the vertical stack with task workspaces: Overview, Control and Modes, Estimator, Adaptive Controller, Telemetry/Capture, Experiments, Paths, Bench, Replay, Diagnostics. Use a registry-driven plugin loader and capability-based visibility. All controls are disabled until required telemetry, freshness, disarm/SDK/bench gates, and transport health are satisfied. Replace demo animation with explicit simulation/replay mode; every state-machine animation shows state, transition cause, timestamp, and source. Generate controller axes/features/weights from metadata rather than hardcoded six values.

### WP5: Capture, analysis, and experiments

Build a rate planner from actual byte budgets and measured rates across four slots. Capture raw frames and decoded CSV/Parquet-like records with schema/build metadata and source clocks. Time series and FFT use sample timestamps and report jitter, gaps, effective rate, and window. Experiments are declarative plans with preflight, shadow/active mode, parameter sweep, abort/rollback, safety gates, and result artifacts. Shadow mode must never write control outputs; active mode requires explicit policy and live interlock.

### WP6: Agent observability and validation harness

Expose the same view model rendered by the browser at `/api/view-model`, plus `/api/events`, `/api/faults`, `/api/actions`, and a deterministic replay endpoint. Add a headless journey runner that starts from a fixture or live read-only session, asserts visible text/state, captures screenshot and console errors, and emits the diagnostics bundle. Agents must use this before and after changes; browser appearance, wire evidence, and firmware/probe evidence must agree.

## Mandatory acceptance journeys

1. Connect to MicoAir, negotiate a bounded slot plan, receive schema and data, and show live age/rate/loss with no placeholder values.
2. Select FIXED, EMA, and EKF bias modes; show pending, firmware-applied, and observed state. Do not connect EKF output to control paths.
3. Toggle one MRAC flag and prove the exact firmware variable changed, then restore it.
4. Change one safe parameter and show desired-versus-readback mismatch if readback is absent; never claim current on HTTP acceptance alone.
5. Record a capture, stop it, replay it through the same panels, and prove FFT/rate calculations use recorded timestamps.
6. Attempt an unsafe command while its precondition is false; show a disabled/rejected reason and prove no wire command was sent.
7. Generate an agent diagnostics bundle that lets a fresh agent reproduce the issue without manually navigating the UI.

## Session order for coding agents

Run sessions serially: `WP0 evidence`, `WP1 contract`, `WP2 telemetry`, `WP3 truth model`, `WP4 shell/plugins`, `WP5 capture/experiments`, `WP6 agent harness`, then a final integration and hardware validation session. Each session reads `.claude_state.md`, the prior WP report, and the evidence ledger; it updates the checkpoint before ending. Parallel edits to firmware protocol and host decoder are prohibited until WP1 defines the generated contract.

## Validation and safety rules

Use `python -m ground_station.livewatch verify` before SWD reads. Do not initialize motors, do not send arm/flight/path commands in automated validation, and keep EKF shadow-only. Flash only through the deterministic rebuild/flash protocol and verify the resulting ELF/device identity. Hardware tests must be bounded, logged, and abort on stale telemetry, armed state, loss of transport, or schema mismatch.
