# Agent guide: driving the ground station and dashboard

This guide is for agents (LLM or scripted) that inspect, test or extend the dashboard. Humans should start at [README.md](README.md).

## 1. Ground rules

- The service (`python -m ground_station.service`) listens on **`http://127.0.0.1:8081`** by default.
- **Set `NO_PROXY=127.0.0.1,localhost`.** The workstation runs a Clash proxy, and without this, local requests go to the proxy and fail.
- **A live service is connected to the drone. Never POST to it.** POST routes can reach the drone or the telemetry bus: `/commands`, `/subscribe`, `/experiments`, `/experiments/<name>/abort`, `/replay/<id>/play` and `/sessions/<id>/export` (and `/subscribe/preview` for pre-flight validation). Validate POST routes with unit tests that use mocked gateways and bridges (`ground_station/service/tests/`).
- GET routes are read-only and safe to call against the live service.

## 2. Discover before you guess

| Route | What it gives you |
|---|---|
| `GET /api/manifest` | **Single machine-readable capability manifest** describing the whole system: DWARF firmware symbols, commands, published telemetry keys, panels, and routes. Recorded with ELF identity and explicit staleness caveat. |
| `GET /api/routes` | Every GET/POST route with a one-line description or its query params, plus the UI selector scheme. **Machine-readable source of truth.** |
| `GET /api/symbols` | DWARF symbol names from the firmware ELF (`?prefix=N&parent=P&limit=N`; default/max limit 100/1000). Names only; no addresses. |
| `GET /api/view-model` | The state the shell renders: streams, keys, freshness. Cheap by default. **`?stats=1` adds `session_stats` (per-stream sample counts and source rate) by scanning the whole session — on a long session that call takes tens of seconds.** Only the Firmware Resource Map's Refresh button asks for it. |
| `GET /api/contract` | The firmware contract: schema ID, command IDs and telemetry layout (`ground_station.platform.firmware_contract`). |
| `GET /api/diagnostics/bundle` | Recent frames, commands and faults, for bug reports. There is no bare `/api/diagnostics`; it returns 404 by design. |
| `GET /api/actions`, `/api/events`, `/api/faults` | Action journal, event log and fault log. |
| `GET /health`, `/health/slots`, `/slots`, `/state` | Service liveness, slot statistics, active subscriptions, and full state snapshot. |

Query strings are parsed with `urlsplit`, so `/sessions?limit=5` routes the same as `/sessions`.

### Paging: always page the record routes

`GET /sessions/<id>/records` and `GET /replay/<id>` take `?limit=N&offset=N`. Both **default to 1000 rows**; `limit=0` means unlimited and an unparseable `limit` falls back to the default. Both responses carry `count`, `offset`, `limit` and `truncated`, so a reader knows whether to fetch the next page:

```bash
curl -s "http://127.0.0.1:8081/sessions/$SID/records?limit=500&offset=0" | python -m json.tool | head
```

The cap is not cosmetic. Before it existed, one live flight session answered `?limit=5` with **181 MB in 6.9 s** — the `limit` was parsed by the shell, not the server — which stalled the replay panel and any agent that read it. Paging is pushed into SQL (`storage.iter_records(session_id, limit=None, offset=0)`), so a capped read never materialises the full session. Regression tests: `test_records_route_pages_and_caps_by_default`, `test_replay_route_pages_like_records_route`, `test_store_iter_records_offset_without_limit`.

## 3. UI selectors (stable for automation)

| Selector | Element |
|---|---|
| `[data-testid="tab-<workspace>"]` | Workspace tab. `<workspace>` is lowercase, e.g. `tab-replay` or `tab-diagnostics`. |
| `[data-testid="panel-<slug>"]` | Plugin card, e.g. `panel-session-replay` or `panel-system-overview`. |
| `#plugin-body-<slug>` | The plugin's content root. |
| `[data-testid="session-id"]` | Session id in the replay detail view. |
| `[data-testid="replay-play"]` | "Play to Live View" button. **It POSTs, so do not click it against a live service.** |

The same list is served at `GET /api/routes` → `ui_testids` / `ui_ids`.

## 4. How does an agent drive this dashboard programmatically?

Agents frequently ask how to automate interactions with this dashboard. There are two distinct mechanisms with strict safety boundaries:

### 1. `journey.py` is GET-only with a click whitelist by design

`ground_station/service/journey.py` provides a declarative headless journey runner for smoke-testing the live shell.
**Its safety restriction is deliberate and must never be widened:**
- **No POST requests:** The only HTTP call `journey.py` makes is `GET /api/diagnostics/bundle`. It never issues POSTs to `/commands`, `/subscribe`, or `/experiments`.
- **Strict click whitelist:** The runner refuses any click selector not matching the whitelist:
  - Workspace tabs: `.ws-tab` or `[data-testid="tab-..."]`
  - Replay session rows: `.rp-session-item`
- **Rejection of action targets:** Clicks on command buttons, arm switches, parameter adjustment inputs, or export buttons are rejected with an error.

> [!IMPORTANT]
> **Do not widen the journey runner or add a POST path.** The live dashboard service binds hardware transports (UDP 14550 / USART3) communicating directly with a powered aircraft. Allowing programmatic button clicks or arbitrary POSTs through a browser runner creates unacceptable risk of accidental motor spin, parameter corruption, or flight abort during testing.

### 2. Offline testing via DOM harnesses (the recommended approach)

When an agent needs to test or verify panel logic, variable rendering, user interaction, or error handling, **it should assert against panel state offline using a DOM harness**, without a running browser or live service.

This is the established pattern across the test suite:
- `ground_station/service/tests/time_series_panel_harness.js` (Time Series panel)
- `ground_station/service/tests/overview_panel_harness.js` (System Overview panel)
- `ground_station/service/tests/motor_bench_panel_harness.js` (Motor Bench panel)

A DOM harness runs under Node.js (`node <harness>.js`) or pytest (`test_*_panel.py`):
1. Instantiates a minimal fake DOM (`Element`, `document`, `window`).
2. Stubs the `shellApi` (`getState()`, `subscribe()`, `submitCommand()`).
3. Executes the panel's plugin script in a sandboxed VM context.
4. Feeds realistic or edge-case telemetry dictionaries into the panel's `render()` / `onState()` callbacks.
5. Directly inspects and asserts element text, styles, classes, and fallback badges (e.g. verifying "NOT PUBLISHED" or "NO DATA" states).

## 5. Smoke checks

```bash
# Browser walk: all tabs, screenshots, console errors, HTTP >= 400, NaN/undefined text.
# Read-only: it clicks tabs and one replay session row, never a command/export/play button.
NO_PROXY=127.0.0.1,localhost python -m ground_station.service.browser_smoke \
    --out logs/smoke --no-replay-detail
# Exit code 0 means no console errors and no bad responses.

# Unit tests (mocked; safe; run through win.sh for Windows Python environment)
.agent-ops/win.sh "pytest -q ground_station/service/tests ground_station/platform/tests ground_station/comm/tests"
```

Requirements: Playwright (`pip install playwright`) and a local Chrome install (`channel="chrome"`).

Pass `--no-replay-detail` against a live service with a long session: without it the walk opens one session row, and that record fetch is the heaviest request the shell makes. `--settle` (default 4.0 s) is the dwell per tab; raise it if panels are still loading when the screenshot is taken.

Last verified walk — 10 tabs, `nan=0 undef=0` everywhere, **ERRORS 0 / BAD RESPONSES 0**.
Panel membership re-measured 2026-09-21 with GETs against the live service (served shell + plugin meta resolution):

| Tab | Panels |
|---|---|
| Overview | `panel-system-overview`, `panel-flight-status`, `panel-safety-limits` |
| Control | `panel-flight-status`, `panel-safety-limits`, `panel-command-panel` |
| Estimator | `panel-ekf-estimator` |
| MRAC | `panel-mrac-controller` |
| Telemetry | `panel-telemetry-explorer`, `panel-time-series`, `panel-fft-spectrum`, `panel-bandwidth-manager`, `panel-slot-manager` |
| Experiments | `panel-experiment-runtime` |
| Paths | `panel-path-planning` |
| Bench | `panel-motor-bench` |
| Replay | `panel-session-replay` |
| Diagnostics | `panel-rtos-resources`, `panel-fft-spectrum`, `panel-bandwidth-manager`, `panel-firmware-resource-map` |

### Running your own service instance

Do not start a second *real* service: it would bind UDP 14550 and could send a subscribe to the drone. For route-level work, build an in-process one on an ephemeral port instead — and note that **the shell is only served when `static_root` is passed**, otherwise `/` is 404 and the tabs never appear:

```python
from ground_station.service.api import ApiServer
api = ApiServer(service, host="127.0.0.1", port=0,
                static_root="docs/dashboard-platform/shell")
```

`make_handler(service, hub=None, static_root=None, experiment_runtime=None)` is the handler factory behind it.

## 6. Behaviours worth knowing

| Capability | Behaviour |
|---|---|
| **Arm gate (fail closed)** | `GroundStationService.arm_state()` returns `armed`, `disarmed` or `unknown`. If there is no arm sample, or the latest one is older than 2 s (`ARM_STALE_NS`), the result is `unknown`, and `submit_command` rejects motor-bench commands (0x16) unless the state is `disarmed`. A rejection is logged as a `SAFETY_INTERLOCK` fault. |
| **Command timeout** | A command still `SUBMITTED` 1.0 s after sending (`COMMAND_TIMEOUT_NS`) moves to `TIMED_OUT` in the action journal. |
| **Slot loss / freshness** | A slot not updated for 30 s is evicted from the `/state` snapshot. The shell also renders per-key staleness from `last_update_ns` / `_key_ts`. |
| **`WifiBridge.subscribe_slot(slot, divider, ranges)`** | Sends one 0x21 request for a single slot. `divider=0` stops the slot. `ranges` are DWARF names (resolved against `OBJ/JX_FLY.axf`) or `StreamRange` objects. Wire format: [docs/telemetry-protocol.md](../telemetry-protocol.md). |
| **Replay → live view** | `POST /replay/<id>/play` pushes stored telemetry onto the local bus so live panels render it. **Nothing is sent to the drone.** |
| **Analysis** | `GET /analysis/{jitter,gaps,effective-rate}?session_id=&stream=` and `GET /analysis/compare?a=&b=&stream=&key=`. |
| **Action journal ordering** | `/api/view-model` returns the **10 most recent** actions, newest first (`list(reversed(service.action_journal()[-10:]))`). `GET /api/actions` returns the journal in its natural oldest-first order. |
| **Zero is not missing** | View-model stats use `x if x is not None else None`, never a truthiness test — a legitimately zero rate or count must render as `0`, not as `—`. |

## 6a. Session recording is opt-in — how an agent drives it

Nothing is written to disk until recording is started explicitly. An agent (or
operator) controls it over HTTP; it is a **host-side habit**, not a safety gate —
recording, notes and events never send a drone command and never change command
gates (shadow responsibilities only).

- **Start a recording**
  ```bash
  curl -s -X POST http://127.0.0.1:8081/api/recording/start \
    -H 'Content-Type: application/json' \
    -d '{"requested_by":"agent:my-agent","reason":"measuring tau step","label":"tau-step-01"}'
  ```
  `requested_by` must be `operator` or `agent:<name>`. `label` is appended to the
  directory name (`logs/sessions/<YYYYmmdd-HHMMSS>-<label>/`). Starting while a
  recording is already active is a no-op that returns the current state.
- **Stop** — `POST /api/recording/stop` (idempotent). Finalises `manifest.json`.
- **Status** — `GET /api/recording` →
  `{recording, session_dir, started_at, rows, bytes, reason, enabled}`.
- **Leave a note for the operator / future self**
  ```bash
  curl -s -X POST http://127.0.0.1:8081/api/session/note \
    -H 'Content-Type: application/json' \
    -d '{"text":"started hover test","kind":"goal","source":"agent:my-agent"}'
  ```
  `kind` is `note` | `goal` | `marker`. Notes added while *not* recording are
  buffered in memory (last 50) and flushed into the next recording's
  `events.jsonl`. `GET /api/session/notes` returns the buffered ones.
- **Recordings on disk** — each recording gets its own directory with
  `telemetry.csv` (long-format rows), `events.jsonl` (command lifecycle, arm
  state changes, stream stalls, notes, view [SESSION_DATA.md](SESSION_DATA.md))
  and `manifest.json` (metadata, stopped/started times, subscribe layout).
- **Auto-start at boot** — `GS_RECORD=1` starts recording at service start;
  `GS_RECORD=0` forbids starting at all; the default (unset) is "stopped until
  an agent/operator starts it".

## 7. Where the code is

| Path | Role |
|---|---|
| `ground_station/platform/capability_manifest.py` | Generator for the system capability manifest (`docs/dashboard-platform/capability_manifest.json`). |
| `docs/dashboard-platform/capability_manifest.json` | Generated machine-readable system capability manifest. |
| `ground_station/platform/firmware_contract.py` | `COMMAND_TABLE`, subscribe limits, and protocol contracts. |
| `ground_station/service/api.py` | HTTP routes; `_ROUTE_MAP` feeds `/api/routes`. **Update it when you add a route.** `test_http_api_routes_endpoint` GETs every parameterless route in it. |
| `ground_station/service/journey.py` | Headless, read-only journey runner (GET-only; tab/replay click whitelist). |
| `ground_station/service/core.py` | Service state, arm gate, command lifecycle, slot freshness. |
| `ground_station/service/storage.py` | SQLite session store. `iter_records(session_id, limit=None, offset=0)` pages in SQL. |
| `ground_station/service/browser_smoke.py` | The browser smoke walk. |
| `ground_station/comm/wifi_bridge.py` | UDP bridge, telemetry decoders, and subscribe protocol. |
| `docs/dashboard-platform/shell/` | Shell (`index.html`) and plugins (`plugins/*.js`). See [shell/plugin-api.md](shell/plugin-api.md). |

## 8. The service owns the WiFi port — stream-log cannot run beside it

A running service holds **TCP 8081 and UDP 14550** in one process:

```
TCP    0.0.0.0:8081    LISTENING    <pid>
UDP    0.0.0.0:14550                <pid>
```

So `python -m ground_station.livewatch.stream_log --transport usart3` (whose data path defaults to `udp:14550`) **cannot bind while the dashboard service is up**. Find the owner with `netstat -ano | grep -E "14550|8081"`. Do not kill the service to make room: ask the operator to stop it, run the capture, then restart it.

The UART5 fallback is not an escape hatch in this build. `SUBSCRIBE_UART5_ENABLED` is never `#define`d, so it evaluates to 0: `Uart5_Subscribe_TxSend` is a no-op stub, and since 2026-09-20 `API/subscribe.c:537` explicitly rejects a UART5 subscribe with `E:UART5 disabled` rather than ACKing a slot whose frames would all be dropped. `stream_log --transport uart5` therefore gets no data from this firmware.

Probe reads have no such conflict — they go over SWD and work with the service running:

```powershell
python -m ground_station.livewatch read --transport swd <symbol>
python -m ground_station.livewatch verify   # always first: catches a stale ELF
```

## 9. What an agent still cannot learn without reading C source (Ranked Gap List)

The capability manifest (`GET /api/manifest` / `docs/dashboard-platform/capability_manifest.json`) and this guide provide a queryable ground truth covering firmware symbols (DWARF names), command tables, verified published telemetry keys, dashboard panels, and HTTP API routes. This eliminates the need for agents to start cold and guess surface contracts.

However, key firmware operational characteristics and internal contracts remain visible only by inspecting C source code. Below is the ranked list of remaining gaps, ordered by how often they bite and the cost of the resulting failure, anchored in real project incidents.

### 9.1 Retrospective on Known Project Failures

| Known Project Failure | Status Under Manifest & Guide | Where Solved / Why Open |
|---|---|---|
| **Panels shipped bound to telemetry keys nobody published** (e.g. `ano_of.*`, `ahrs.rol`, `ekf.pos_x`, `status.status_bits`) | **CLOSED** | `manifest.telemetry.verified_published_keys` provides an explicit, machine-derived whitelist of all 114 keys published by `wifi_bridge.py` frame decoders. `manifest.telemetry.unverified_keys_in_panels` catalogs historical phantom keys and the reason for their absence. Section 2 guides agents to query `/api/manifest`. |
| **Worker fabricated function (`generateDemoPoint` in `path-panel.js`) that reached a spec** | **NOT CLOSED by manifest; MITIGATED by test harness design & process** | The manifest indexes panel files, slugs, workspaces, gates, and consumed keys (`manifest.panels[]`), but does **not** index JavaScript functions or AST exports. An agent cannot query `/api/manifest` to verify internal panel functions. Mitigated by Section 4.2 (mandatory offline DOM harness testing; banning synthetic data in production plugins) and `.agent-ops/STANDING-RULES.md` (mandating verification of cited `file:line` before adding to specs). |
| **Symbol names guessed wrong** (`mrac_state.What` vs `Theta`/`Whatf`; `rpm[4]` vs `rpm_ch[]`; `fault_capture.c` in `USER/` not `API/`) | **PARTIALLY CLOSED** | **Global symbols: CLOSED.** `manifest.firmware_symbols.names` and `GET /api/symbols` index all 12,000+ DWARF global symbols. Querying `rpm` immediately reveals `rpm_ch` (disproving `rpm[4]`).<br>**Struct members & nested fields: OPEN.** Manifest lists only top-level symbol names (`mrac_state`), not internal struct members (`Theta` vs `Whatf`).<br>**Source paths: OPEN.** Manifest records `elf_path` but no mapping of modules to directories (`USER/` vs `API/`). |

---

### 9.2 Ranked Gap List

#### Rank 1: Telemetry Data Types, Physical Units, Fixed-Point Scaling, and Coordinate Frames
- **What an agent cannot answer:**
  - What physical units a telemetry variable represents (e.g. `c.altitude` is meters, while underlying C struct `ano_of.of_alt_cm` is centimeters; battery voltage is 0.01 V integer; body gyro rates are deg/s vs rad/s).
  - What fixed-point multiplier is applied in C when packing into the wire frame (e.g. `TASK/send_data.c:1003-1005` scales `s_ekf.x[3]` by `1000.0f` into `int16_t` for `ekf.vel_body_x`; `mrac_state.e_filter` scaled by `1000.0f`).
  - What the dynamic numerical range is, and whether values risk signed integer overflow.
  - What coordinate frame a vector belongs to (NED earth frame vs body frame vs sensor frame).
- **Evidence manifest and guide do not answer it:**
  `manifest.telemetry.verified_published_keys` is a flat list of strings (`["c.altitude", "c.gyro_x", ...]`). It contains zero type, unit, scale factor, or frame metadata. While `manifest.commands` provides `unit`, `min_val`, and `max_val` for command parameters, telemetry keys have none. `AGENT_GUIDE.md` documents no unit conversions.
- **Project failure anchor:**
  - *Pre-flight Audit (finding F2):* Stationary bench telemetry showed shadow EKF velocity `+57.7 m/s`. When scaled by `1000.0f` into `int16_t` in `send_data.c`, `57714` overflowed to `-7822`, wrapping signed telemetry and corrupting ground station records.
  - *Operator Feedback Batch (finding I1):* Motor Bench panel slider assumed RPM (1–1000), but firmware ESC mixer inputs operate on PWM pulse widths (2000–4000 microseconds).
- **Smallest change to close:**
  Enrich `manifest.telemetry` in `ground_station/platform/capability_manifest.py` with a `key_metadata` mapping specifying `type` (e.g. `float32`, `int16`), `unit` (e.g. `m`, `deg/s`, `V`, `PWM_us`), `scale_factor` (e.g. `0.001`), and `frame` (`body`, `ned`, `raw`), derived from `wifi_bridge.py` decoder unpack specifications and docstrings.

---

#### Rank 2: RTOS Scheduler Architecture, Task Cadences, and Pacing Semantics (Absolute vs Relative)
- **What an agent cannot answer:**
  - What execution frequency each firmware task runs at, and how it is scheduled under FreeRTOS.
  - Whether a task uses absolute periodic pacing (`vTaskDelayUntil(&PreviousWakeTime, pdMS_TO_TICKS(10))` at `USER/main.c:340`) or relative delay (`vTaskDelay(pdMS_TO_TICKS(5))` at `USER/main.c:305`).
  - Whether task execution contains DMA busy-waits or blocking calls that reduce effective throughput (e.g. `send_data.c:836` DMA busy wait stretching 5 ms relative delay to 12.47 ms / 80.2 Hz).
- **Evidence manifest and guide do not answer it:**
  The manifest contains no task inventory, thread rates, or scheduling metadata. Project sources and documentation contradicted each other three ways: `firmware_contract.py:103` stated 100 Hz "by design", `manifest_layer.py:293` calculated `200 // divider`, and `send_data.c:476` claimed 80.2 Hz.
- **Project failure anchor:**
  Workers repeatedly miscalculated slot bandwidth budgets, subscribe dividers, and timestamp deltas. It required hardware DWT cycle-counter profiling (`g_send_prof`) to establish that Send_Task runs at 100 Hz in normal flight but drops to relative ~80 Hz pacing when SysID or optical flow frames are active.
- **Smallest change to close:**
  Add a `TASK_SCHEDULE` table in `ground_station/platform/firmware_contract.py` defining task names (`Send_Task`, `StabilizerTask`, `RemoterTask`), nominal frequencies, pacing types (`absolute` vs `relative`), and entry functions, and include this table in `capability_manifest.json` under `rtos_tasks`.

---

#### Rank 3: Flight FSM State Machine, Mode Transitions, and Autonomous Safety Interlocks
- **What an agent cannot answer:**
  - What integer values in `status.flymode` correspond to which flight state (e.g. 0 = INIT/STOP, 1 = ATTITUDE, 2 = OF_HOLD/POSITION).
  - What triggers autonomous mode collapses or safety latches (e.g. `TASK/RemoterTask.c:145` fires `DANGEROUS_STOP` when `sbus_channel[9] <= 500`, latching EMERGENCY mode; `TASK/StabilizerTask.c:265` drops position hold when `of_quality < 50`).
  - Why firmware rejects an arming request or command dispatch.
- **Evidence manifest and guide do not answer it:**
  `manifest.commands` provides host-side preconditions (e.g. `requires_disarmed`), but the manifest defines no firmware FSM states, no enum mapping for `status.flymode`, and no safety interlock triggers. `AGENT_GUIDE.md` Section 6 documents only the host-side arm gate (`GroundStationService.arm_state()`).
- **Project failure anchor:**
  - *Pre-flight Audit (finding F3):* The drone refused to arm on the bench because `RemoterTask.c:145` latched EMERGENCY state when the RC transmitter was powered off, requiring an operator to raise channel 9 and issue `RECOVER_SDK`.
  - *Pre-flight Audit (finding F1):* Optical flow quality was 0 on the bench, silently preventing transition to position hold because the firmware threshold requires `>= 50`.
- **Smallest change to close:**
  Define `FLIGHT_MODES` and `SAFETY_INTERLOCKS` enum tables in `ground_station/platform/firmware_contract.py` (mapping integer IDs to mode labels and transition requirements) and export them into `capability_manifest.json` under `flight_fsm`.

---

#### Rank 4: Hardware Peripheral Topology, Serial Bus Routing, and Compile-Time Feature Flags
- **What an agent cannot answer:**
  - Which physical microcontroller USART/UART connects to which peripheral (USART3 = WiFi ESP module; USART2 = Optical Flow sensor; UART4 = Companion computer/T265; UART5 = Serial debug).
  - Which C preprocessor `#define` switches are active in the current build (e.g. `SUBSCRIBE_UART5_ENABLED` is undefined, rendering `Uart5_Subscribe_TxSend` a stub returning `E:UART5 disabled`).
  - Which timer capture/compare channels map to which motor ESC outputs (TIM3 CCR1–CCR4 to motors 0–3 via `BSP/pwm.c`).
- **Evidence manifest and guide do not answer it:**
  The manifest records the binary ELF SHA256 and size, but does not capture active `#define` options or peripheral routing tables. `AGENT_GUIDE.md` Section 8 mentions USART3 WiFi ownership and UART5 stubbing, but lacks a complete hardware bus map.
- **Project failure anchor:**
  Workers repeatedly attempted to use `stream_log --transport uart5` or serial COM6 for telemetry subscriptions, resulting in silent timeouts or rejection errors because UART5 subscribe is disabled at compile time. Another worker attempted companion computer diagnostics on the wrong serial port.
- **Smallest change to close:**
  Add a `HARDWARE_MAP` dictionary in `ground_station/platform/firmware_contract.py` specifying UART bus assignments, baud rates, connected devices, and active compile-time `#define` flags, and expose this in `capability_manifest.json` under `hardware_topology`.

---

#### Rank 5: DWARF Symbol Type Metadata: Struct Member Hierarchies, Member Offsets, and Array Bounds
- **What an agent cannot answer:**
  - Given a global symbol name, what are its internal struct member names, data types, and byte offsets (e.g. `mrac_state.Theta` vs `mrac_state.Whatf`).
  - Whether a symbol is a scalar primitive readable via 1/2/4-byte SWD word access or a composite struct (e.g. `DroneStatus` is a 12-byte struct).
  - What the dimensions and index mappings of state arrays are (e.g. `s_ekf.P` is a 9x9 covariance matrix; `s_ekf.x[0..8]` state order: roll, pitch, yaw, vx, vy, vz, ...).
- **Evidence manifest and guide do not answer it:**
  `manifest.firmware_symbols.names` contains a flat list of symbol identifier strings (`["DroneStatus", "mrac_state", "s_ekf", ...]`). It contains no type information, no struct field trees, and no byte sizes. `GET /api/symbols` returns names only.
- **Project failure anchor:**
  - Specs repeatedly cited `mrac_state.What` when the C struct member is `Theta` or `Whatf`.
  - Specs cited `rpm[4]` when the global array in C is `rpm_ch[]`.
  - Workers attempting to read `DroneStatus` via `livewatch read --transport swd` were rejected by probe safety checks because `DroneStatus` is a 12-byte composite struct rather than a 1/2/4-byte primitive.
- **Smallest change to close:**
  Extend `ground_station/livewatch/symbols.py::SymbolResolver` to extract DWARF type tags (`DW_TAG_structure_type`, `DW_TAG_member`, `DW_TAG_array_type`), byte sizes, and member offsets, exposing them via `GET /api/symbols?name=<sym>&details=1` and exporting high-interest telemetry structs into `capability_manifest.json`.

---

#### Rank 6: Firmware Source Tree Layout and Compilation Unit Mapping
- **What an agent cannot answer:**
  - Which source directory contains the implementation of a given module (e.g. `USER/fault_capture.c` vs `API/fault_capture.c`).
  - Which C source files in the repository are actually included in the Keil project build (`USER/JX_FLY.uvprojx`) versus uncompiled legacy or experimental files (e.g. `USER/demo_learning.c` or prototype stubs in `firmware/`).
- **Evidence manifest and guide do not answer it:**
  The manifest records `elf_identity.elf_path = "OBJ/JX_FLY.axf"`, but does not list the source compilation units that produced it. `AGENT_GUIDE.md` Section 7 ("Where the code is") lists only ground station Python and shell paths, omitting firmware directories entirely.
- **Project failure anchor:**
  Supervisor and worker specs misattributed `fault_capture.c` to `API/` when it resides in `USER/`. S16 review previously recorded finding D1 claiming firmware sources were missing from the repository because reviewers looked for `firmware/` instead of `API/`, `TASK/`, `BSP/`, `USER/`.
- **Smallest change to close:**
  Add a firmware directory overview table to `AGENT_GUIDE.md` Section 7, and extract DWARF `DW_TAG_compile_unit` file lists into `capability_manifest.json` under `compilation_units`.

---

#### Rank 7: Sensor / Estimator Validity Criteria and Fail-Safe Degradation Rules
- **What an agent cannot answer:**
  - What sensor health metrics gate estimator updates (e.g. optical flow `of_quality >= 50`).
  - How estimators degrade when a sensor stream degrades or times out (e.g. whether velocity coasts, freezes, or zeros).
  - Why the stationary shadow EKF velocity locked onto `+57.7 m/s` (firmware had no quality-collapse timeout reset in `Ekf9_Init`, leaving the velocity state frozen at the last pre-collapse sample).
- **Evidence manifest and guide do not answer it:**
  The manifest lists boolean status keys (`status.estimator_ready`, `status.of_hold`), but defines no numerical thresholds, timeout limits, or filter health gates.
- **Project failure anchor:**
  *Pre-flight Audit (findings F1 and F2):* Optical flow quality was 0 on the bench, causing shadow EKF velocity to freeze at an invalid +57 m/s reading without any ground-station warning flag.
- **Smallest change to close:**
  Document sensor health gating thresholds in `ground_station/platform/firmware_contract.py` (e.g. `SENSOR_HEALTH_GATES = {"optical_flow": {"min_quality": 50, "fallback_mode": "attitude"}}`) and export them in `capability_manifest.json`.

---

## 10. Environment traps that cost real work

Six environment traps on this workstation create plausible false failures and waste investigation time:

### 1. Clash proxy on port 7897 intercepts localhost (flat HTTP 502)
- **Symptom:** `curl http://127.0.0.1:8081/api/routes` (or calling via Windows tools like `curl.exe`) returns `HTTP/1.1 502 Bad Gateway` with `Content-Length: 0` and `Proxy-Connection: keep-alive`, mimicking a dead or broken service.
- **Cause:** A Clash-style proxy runs on port 7897 (accessible on Windows at `127.0.0.1:7897` and from WSL at `172.18.48.1:7897`). If `no_proxy` is unset or omitted in Windows tooling, requests to localhost get forwarded to the proxy, which fails to route to the dashboard service.
- **Fix:** Pass `--noproxy '*'` explicitly to curl commands, or ensure `NO_PROXY=127.0.0.1,localhost` is exported:
  ```bash
  curl --noproxy '*' http://127.0.0.1:8081/api/routes
  ```

### 2. UDP 14550 is single-bind ([WinError 10048])
- **Symptom:** Running `python -m ground_station.livewatch read --transport wifi ...` or `stream_log` fails immediately with:
  `usart3-read: cannot bind UDP 14550 ([WinError 10048] Only one usage of each socket address (protocol/network address/port) is normally permitted) -- is another process holding it?`
- **Cause:** UDP socket binding on port 14550 is exclusive under Windows. While the dashboard service is active, its `WifiBridge` owns UDP 14550. This is port contention between tools, not a hardware fault or dropped connection.
- **Fix:** Do not contend for UDP 14550 while the service is running. Query the running service's GET endpoints instead (`GET /api/view-model`, `GET /state`, `GET /sessions/<id>/records`), or use SWD debug probe reads (`--transport swd`).

### 3. `pgrep` without `-f` reports every live worker as dead
- **Symptom:** `pgrep run-worker` returns exit code 1 with no output, leading an agent to conclude that no workers are active even when multiple workers are running.
- **Cause:** Workers are invoked as `bash` scripts (e.g. `bash .agent-ops/run-worker.sh ...`). Without `-f`, `pgrep` matches only against the process comm name (`bash`), not the script path in command line arguments.
- **Fix:** Use `pgrep -cf run-worker` to match against the full command line and return a count. Avoid `pgrep -af` because `-af` dumps the entire command line—including each worker's prepended spec—which will flood your context window.
  ```bash
  pgrep -cf run-worker
  ```

### 4. Node on the WSL PATH is a broken npm shim
- **Symptom:** Invoking `node --version` fails with exit code 127:
  `/mnt/c/Users/Acer/AppData/Roaming/npm/node_modules/node/bin/node: 1: This: not found`
- **Cause:** The `node` entry in the default WSL PATH points to `/mnt/c/Users/Acer/AppData/Roaming/npm/node`, a Windows npm wrapper whose target file (`node_modules/node/bin/node`) contains the placeholder text `"This file intentionally left blank"`.
- **Fix:** Use the working Windows Node binary at `/mnt/c/Program Files/nodejs/node.exe`. When running directly from WSL bash, redirect stdin (e.g. `< /dev/null`) or run via `.agent-ops/win.sh` to avoid terminal handle hangs:
  ```bash
  "/mnt/c/Program Files/nodejs/node.exe" --version < /dev/null
  # Or via win.sh:
  .agent-ops/win.sh "& 'C:\Program Files\nodejs\node.exe' --version"
  ```


### 5. `/tmp` means two different directories on this box

- **Symptom:** A file written from Git Bash to `/tmp/x.md` is confirmed present by
  `ls -la /tmp/x.md`, and a Windows Python process reading `/tmp/x.md` in the very
  next command raises `FileNotFoundError`.
- **Cause:** Git Bash maps `/tmp` to `C:\Users\<user>\AppData\Local\Temp`. Windows
  Python has no such mapping and resolves `/tmp` literally, to `C:\tmp`. Both paths
  exist, so neither side reports anything wrong — the file is simply somewhere the
  reader is not looking.
- **Fix:** Never hand a POSIX temp path across the boundary. Translate it:
  ```bash
  F=$(cygpath -w /tmp/x.md)   # then read os.environ['F'] on the Python side
  ```
  Better, use the session scratchpad directory, which is a Windows path already.

### 6. WSL git on this repo takes ~4 minutes, which hangs any WSL agent at startup

- **Symptom:** A Claude Code worker launched inside WSL with its cwd anywhere in
  this repository produces **zero bytes** on stdout and stderr and is eventually
  killed by its wrapper timeout. It looks exactly like a quota exhaustion or an
  auth failure. The same binary, same account, answers normally one directory
  above the repo.
- **Cause:** Claude Code refreshes the git index at startup. Through the 9p
  `/mnt/c` mount, git cannot trust the stat data the Windows-side git wrote
  (mtime granularity differs), so it re-reads all 866 tracked files — about
  150 MB, 111 MB of it dirty `OBJ/` build output — on *every* command. Measured:
  `git status --porcelain -uno` takes 237 s from WSL and 2 s from Windows.
  Warming the index does not help; the next WSL command pays the cost again.
  `core.checkStat minimal`, `core.trustctime false` and `core.preloadIndex true`
  are set on the repo and do **not** fix it.
- **Fix:** do not give a WSL agent a cwd inside the repo. Start it in a scratch
  directory that is not a git repo and pass the project with `--add-dir`:
  ```bash
  mkdir -p /tmp/arkwork && cd /tmp/arkwork
  claude --add-dir /mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex -p "..."
  ```
  Verified: 59 s and a correct answer, against 301 s and silence in-repo.
  A worker started this way must use absolute paths, and must run `git` through
  `.agent-ops/win.sh` on the Windows side rather than from WSL.
