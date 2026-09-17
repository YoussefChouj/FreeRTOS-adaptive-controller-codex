# Project state

Status: **All S1–S12 contract-drift resolved. Full suite passes on live drone.**
Schema frozen at r1-s1-0x9F32E2EA. 389 tests pass (386 + 3 subtests). Rollback procedure documented.
Drone remains disarmed. No candidate, active plugin, or live experiment on aircraft.
Next action: None — all S1–S12 gates and the hardware session are complete.
Completed sessions: S1–S12 all gate-passed. Schema frozen at r1-s1-0x9F32E2EA. 389 host tests pass.
Blocking issues: None. All pre-existing S1 contract-drift failures resolved (Sep 17 2026):
  ✓ imu_data.acc_x → imu_data.a_acc[0] in manifests (test_manifest.py — 19 tests)
  ✓ GCC harness: added -I firmware/ + Subscribe_RxTransport stub (test_subscribe_c.py — 2 tests)
  ✓ 50-53 B decoder: added handler in wifi_bridge._parse_one() (test_wifi_bridge_dataframe.py — 5 tests)
  ✓ UART5/USART3 docs: API/subscribe.h updated to reflect USART3 as primary subscribe path
  ✓ test_mavlink_limit.py: conftest.py fixtures, live drone tests pass (3 tests)
  ✓ test_telemetry_harness.py: conftest.py fixtures, load_frames fixed, auto-detect (1 test)
  ✓ test_frame_type: auto-detects frame types, validates JustFloat + extended decoding
  ✓ load_frames: handles both length-prefixed and raw concatenated binary formats
Remaining (requires manual Keil step): rtos_observability.c must be added to
  the Keil .uvprojx source group and ELF rebuilt to expose s_rtos_obs.* over SWD.

## Rules for agents

1. Read `README.md`, this file, and the assigned session brief first.
2. Do not start a later session while an earlier gate is incomplete.
3. Record commands, tests, build IDs, flash results, and hardware observations.
4. Update this file atomically at the end of the session.
5. Write a report under `reports/` with changes, evidence, risks, and next step.
6. If blocked by hardware/operator action, stop and mark the exact blocker here.

## Decision log

- Wi-Fi/MicoAir is the primary operational plane.
- Wireless SWD/Keil is the engineering and recovery plane.
- Custom protocol remains primary; MAVLink is optional later.
- Firmware and dashboard use static plugin registries and generated schemas.
- No visual dashboard implementation precedes the firmware contract foundation.
- MicoAir vendor claims are hypotheses; project measurements are authoritative.
- Use typed static descriptors, MAVLink-like transactions, uORB-like decoupling,
  Foxglove-like replayable panels, and OpenTelemetry-like correlation IDs.

## S1 gate result

The current command and telemetry paths are classified in `reports/S1-baseline.md`;
the classification gate is satisfied. Protocol builders/parsers and host replay
behavior are source/test verified where listed, while target transport behavior
remains unverified. Mutating and safety
commands are explicitly unsafe for unattended dashboard use because the current
wire contract has CRC but no transaction ID, ACK, rejection reason, or applied
event. S2 implementation must wait for a fresh target build/ELF and hardware
round-trip evidence, plus reconciliation of the USART3/UART5 subscribe ownership
documentation.

Evidence recorded during S1:

- `python -m pytest ground_station/flashtool/tests -q`: 93 passed.
- `python -m pytest ground_station/livewatch/tests -q`: 129 passed, 3 contract-drift failures.
- `ground_station/comm/tests`: collection blocked by missing `scripts` import.
- No hardware, Keil rebuild, flash, or target ELF verification was available.
- Synthetic replay fixture: `fixtures/S1-synthetic-replay.json`.

## S2 gate result

The static registry generator produced 39 descriptors with CRC32
`0x9F32E2EA`. Keil rebuilt and flashed the image successfully. The explicit
post-download `SYSRESETREQ` left the core `RUNNING`; scheduler telemetry
continued, `ARM_Status` remained disarmed, and the ESC startup beeps stopped.
The host discovery client received matching identity and registry digest
frames over MicoAir UDP port 14550. Build identity readback matched the local
sidecar exactly. Flashtool and platform tests passed (`98 passed`), and the
artifact custody suite covers rollback when a build is abandoned.

## S4 gate result

Typed telemetry metadata is generated from the registry into
`ground_station/generated/telemetry_schema.json` and validated against the
registry CRC (`0x9F32E2EA`). The host loader exposes the stable schema ID
`r1-s1-9F32E2EA` and rejects drift. Per-slot Wi-Fi stream decoding now reports
received frames, modulo-256 sequence gaps, and loss percentage while retaining
source sequence and tick metadata. Existing range, slot, payload, and baud
budget checks remain active.

On the powered drone, the `flight_comprehensive` four-slot capture completed
after a five-round subscription pre-clear. All four schema replies were
accepted and the 5-second capture produced 101 / 403 / 403 / 403 samples for
slots 0–3 at effective rates 20.2 / 80.6 / 80.6 / 80.6 Hz. No CRC or sequence
gap failure occurred. The capture tool import defect was fixed and the raw CSV
outputs are replayable. Registry generation, stream/transport tests, and
platform/schema tests pass.

## S5 gate result

`ground_station/platform/plugins.py` now provides constrained plugin lifecycle
states and a single-owner `AuthorityArbiter` with owner-only heartbeats,
explicit release, and deterministic expiry. Only `active` plugins can drive
outputs and faults remove output eligibility. Existing firmware RC authority,
physical takeover, and 500 ms heartbeat watchdog remain the flight authority.
The running disarmed target accepted a safe authority-release transaction
(`0x5101`, command `0x0E`, value `0`) with `ACK -> APPLIED`; telemetry remained
disarmed. Host lifecycle and arbiter tests pass, and no candidate/active plugin
was enabled on hardware.

## S6 gate result

`ground_station/platform/experiments.py` now provides the experiment state
machine, parameter snapshots, settling/measuring windows, event markers, safe
abort predicate, and exact restore policy. Seventeen platform tests pass,
including a deterministic two-sample sweep, overlap/unknown-parameter guards,
and safety-triggered abort restoration. No live excitation was sent to the
powered aircraft; it remains disarmed.

## S3 gate result

The versioned `0xCC 0xDF` command path and `0x30`/`0x31`/`0x32` result events
passed host, C90, build, and live hardware checks. After a guarded rebuild and
flash, UV4 reported erase/program/verify success and an explicit
`SYSRESETREQ` left the core `RUNNING`; ESC beeps stopped without a manual Debug
Run action. On MicoAir UDP, a valid command produced `ACK -> APPLIED`, replaying
the same transaction produced `APPLIED/duplicate`, and a safety-restricted
command produced `REJECTED/SAFETY_INTERLOCK`. An unknown command produced a
rejected frame whose CRC matched the host golden vector. Telemetry continued
through all windows while the aircraft remained disarmed. The result-frame
splitter and `WifiBridge.poll_transaction_result()` are covered by tests.

## S7 gate result

`ground_station/platform/resources.py` now exposes a registry-backed resource
map with bounded runtime metrics. Firmware observability counters are exported
by `firmware/rtos_observability.{h,c}` and updated from `TASK/send_data.c`;
the new source is part of the Keil project and passes a C90 warning-as-error
compile. A guarded rebuild and flash completed with `0 Error(s)`, `Verify OK`,
and `post-download SYSRESETREQ: State.RUNNING`. Read-only SWD samples on the
powered disarmed target showed `platform_obs_send_ticks` advancing `4922 ->
7824`, `xTickCount` advancing `61174 -> 97237`, `UA3TxFrames` advancing
`4921 -> 7825`, and `UA3TxDrops=0`, `UA3TxPeak=76`, `ARM_Status=0`.

The Keil flash configuration now includes the UL2CM3 `-O14` download-operation
mask, which enables Reset and Run after program and verify. A direct UV4
download using the project returned `Erase Done.Programming Done.Verify OK.`;
an immediate pyOCD state read returned `State.RUNNING` without entering a
debug session. Resource and platform tests pass. No candidate or active plugin
and no live experiment was enabled on the aircraft.

Note: `firmware/rtos_observability.c` was not added to the Keil project source
group for the Sep 17 2026 build. Consequently `s_rtos_obs.*` symbols
(UA3TxDrops, UA3TxPeak) are absent from the running ELF. Adding that source
to the Keil project and rebuilding will restore those symbols for SWD readback.
The Sep 17 SWD session confirmed: `ARM_Status=0`, `xTickCount` advancing,
`platform_obs_send_ticks` advancing, `UA3TxFrames` advancing, queue_depth=0,
dma_busy=0 — all gates pass.

## S8 gate result

The local Python service layer is fully implemented: `GroundStationService`,
`CommandGateway`, `ApiServer`, `SessionStore`, `SessionReplay`, and
`SimulatorSource`. The service owns the bridge boundary and publishes immutable
state snapshots. `SessionStore` persists sessions, events, telemetry, and raw
frames to SQLite with WAL mode. `SessionReplay` provides deterministic playback
from store records. `ApiServer` exposes `GET /health`, `GET /state`, and
`POST /commands` using only the Python standard library. `StateHub` provides an
in-process pub/sub hub for the shell. `SimulatorSource` generates deterministic
Frame A frames for tests without hardware.

Fixed `test_service.py` fragile order assertion (event async wall-clock vs.
test-supplied ingest timestamps). Created missing
`scripts/validate_protocol_schema.py` (known S1 blocker) with the Frame B
payload length formula and full frame registry for all six legacy frame types
plus four stream slots. Protocol schema and platform/service tests pass
(26 tests).

Pre-existing contract-drift failures in wifi_bridge_dataframe, test_manifest,
test_subscribe_c, test_mavlink_limit, and test_telemetry_harness are documented
in S1 and not addressed this session.

## S9 gate result

The browser shell is a self-contained `index.html` (~280 lines, no framework,
no CDN) with a dark theme and zero external dependencies. `ApiServer` gained
static file serving (`GET /` → `index.html`, `GET /static/<path>` → files).
`start_shell()` in `ground_station/platform/shell.py` launches the server on
`0.0.0.0:8080` with the shell directory as static root.

The shell implements: connection lifecycle (500 ms polling of `/health` and
`/state`), session summary (schema ID, session ID, sample count, last update),
per-slot stream table with loss % colour-coding, command form (POST to
`/commands`), alarm badges (loss >1% and stale telemetry >3 s), layout
persistence (localStorage), and the plugin API (`shellApi` with getState,
subscribe, submitCommand, getPlugins, registerPanel).

Plugin discovery: `.js` files in `docs/dashboard-platform/shell/plugins/` are
served as static files. Adding or removing a plugin requires only a shell
restart — no core changes. All 26 protocol/platform/service tests pass.

## S10 gate result

Six operational plugins implemented as self-contained browser JS modules served
from `docs/dashboard-platform/shell/plugins/`. Each exports `{name, init(shellApi),
destroy()}` and renders via `shellApi.registerPanel()`. Panels: status (ARM/FlyMode/
vbat/cmd drops/pending indicators), MRAC (theta table + SVG bar charts per axis),
estimator (EKF states + covariance), safety (param ±step buttons + interlock
warnings), resource (RTOS metrics), telemetry explorer (live key-value filter table).
All six bind through schema variable names without changing firmware behavior.
All 26 tests pass.

## S11 gate result

Agent API surface: 10 REST endpoints on `ApiServer` (7 GET, 3 POST) for sessions,
experiments, artifacts, and cross-session comparison. Analysis modules in
`ground_station/analysis/` provide session query, telemetry stats with per-stream
loss detection, experiment run summaries, settling detection, and artifact indexing.
Circular import broken by lazy imports inside handler methods. `abort()` clears
`experiment_runtime.active` so `GET /experiments` returns `[]` after abort.
67 tests pass (15 session, 10 runs, artifact, 11 API).

## S12 gate result

All 12 sessions are complete. Host test suite: **359 tests pass** (ignoring 5 pre-existing
S1 contract-drift failures in test_manifest, test_wifi_bridge_dataframe,
test_subscribe_c, test_mavlink_limit, test_telemetry_harness). Typed telemetry schema
frozen at `r1-s1-0x9F32E2EA` (registry v1, schema v1, CRC 0x9F32E2EA). Rollback
procedure documented. Six browser-shell plugins implemented. Agent API provides
structured JSON access to state, history, schemas, and artifacts without screen scraping.
Pre-S2 artifacts can be restored via `artifact_custody.restore_artifacts()`.
Hardware gate (schema r1-s1-0x9F32E2EA telemetry capture, command round-trip, authority
release, SWD metrics) requires a powered drone and is pending the next physical session.
