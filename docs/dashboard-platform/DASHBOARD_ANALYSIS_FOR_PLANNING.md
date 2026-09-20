# UAV Ground Station Dashboard — Engineering Analysis for ASTRA 6 Codex Planning

> **Purpose**: Structured description of dashboard gaps, architectural issues, and user needs for an autonomous planning model (ASTRA 6 in Codex) to analyze the codebase and produce actionable fixes.
>
> **User**: jiang — Control Science & Engineering master's student working on a SDM324 quadrotor running FreeRTOS adaptive controller (MRAC).
>
> **Hardware**: MicoAir 2.4 GHz WiFi module, CMSIS-DAP SWD debugger, STM32F405 MCU.
>
> **Generated**: 2026-09-18 06:34 UTC+8 — based on live dashboard screenshots, terminal state, and code analysis.

---

## 0. What the User Wants

The dashboard should be **the single interface** for:

| Capability | Why it matters for jiang |
|---|---|
| Read live firmware state | Tune MRAC weights, observe EKF estimates, check arm safety |
| Write parameters at runtime | Adjust MRAC gamma, PID gains, bias mode, safety limits — no reflash |
| Choose telemetry layout | WiFi bandwidth is limited (~1600 B/s clean); must budget 4 slots across IMU/MRAC/EKF/RPM |
| Switch bias estimator mode | FIXED/EMA/EKF for optical flow — flight-test different estimators |
| Arm/disarm safely | From bench to flight without touching RC |
| Visualize adaptation | MRAC Theta weights evolving in real time |
| Replay sessions | Analyze flight logs offline |
| Agent-friendly for future work | Easy for future agents (Claude/Cursor) to read state, inject commands, add panels |

**The gap**: today the dashboard shows mostly "—", "Awaiting first parameter readout", and "Waiting for telemetry..." — even though the firmware is live, the SWD probe reads all variables correctly, and commands flow and are APPLIED.

---

## 1. What the Dashboard Currently Shows (UI Gaps)

### 1.1 SESSION Sidebar Card
```
Schema ID: r1-s1-9F32E2EA  ← OK (schema ID visible)
Session ID: 0058cbf5...      ← OK
Samples: 0                    ← PROBLEM: samples=0 despite service running
Last Update: —                ← PROBLEM: no last_update_ns
```

**Root cause**: The subscribe path (slot 0 WiFi → 0x08 schema → streams["0"]) is not receiving the firmware's 0x08 schema reply. The service is running, commands work, but the typed-subscribe stream data never populates `streams["0"]`.

### 1.2 FLIGHT STATUS Panel
```
ARM Status: DISARMED                        ← OK (value exists in firmware)
Source: no telemetry                       ← PROBLEM: indicates no stream data
Authority Flags:
  SDK: ?     ← PROBLEM: "?" indicates unreadable
  TWC: ?     ← PROBLEM
  RC: ?       ← PROBLEM
State Flags:
  ARM: ?      ← PROBLEM
  FlyMode: ?  ← PROBLEM
  OF Hold: ?  ← PROBLEM
  Estimator: ?← PROBLEM
Last Command: Cmd 18 applied                ← OK (command feedback works)
```

**Root cause**: `DroneStatus.ARM_Status`, `DroneStatus.FlyMode`, `TWC.execute`, `s_authority`, `g_of_hold_active`, `g_estimator_ready` are all present in firmware RAM and readable via SWD, but they are not flowing into the dashboard. The sidebar mapping drops them.

### 1.3 MRAC CONTROLLER Panel
Shows pitch axis tracking error with a chart — but likely stale or missing data for roll/yaw/z_rate axes. The `mrac_state.*.e` (tracking error) and `mrac_state.*.u_ad` (adaptive output) variables exist in firmware but the full Theta weight vectors (24 values: 4 axes × 6 basis functions) are not visible.

### 1.4 EKF ESTIMATOR Panel
```
Filter Status: —              ← PROBLEM: no filter status variable in streams
RAW IMU:
  Gyro X/Y/Z: — — —          ← PROBLEM: no raw IMU in sidebar stream
  Accel X/Y/Z: — — —         ← PROBLEM
Covariance Matrix: all —      ← PROBLEM: no covariance in sidebar stream
EKF States:
  pos_x/y/z: — — —           ← PROBLEM: EKF state vector not visible
  vel_x/y/z: — — —            ← PROBLEM
```

**Note**: The EKF in this firmware estimates velocities and IMU biases, NOT positions. `s_ekf.x[0..2]` = body velocities. The panel label "pos_x/y/z" is misleading — it should be "vel_x/y/z". This is a documentation/plumbing gap.

### 1.5 RTOS RESOURCES Panel
```
scheduler_tick_count: "rtos" ← PROBLEM: placeholder text, no real value
heap_free_bytes: "rtos"       ← PROBLEM
queue_depth: "rtos"          ← PROBLEM
dma_busy: "rtos"             ← PROBLEM
send_ticks: "rtos"          ← PROBLEM
```

**Root cause**: The RTOS bridge via `inject_external_stream` is a separate path that injects RTOS metrics into `streams["rtos"]`. The panel is reading the wrong slot — it should read `streams["rtos"]` but the fallback code shows "rtos" placeholder text.

### 1.6 Safety Interlocks Panel
```
Status: "Awaiting first parameter readout from firmware" ← PROBLEM
Max H-Speed: — (Current: ?) ← PROBLEM
Max V-Speed: — (Current: ?) ← PROBLEM
Max Pitch: — (Current: ?)   ← PROBLEM
Max Roll: — (Current: ?)   ← PROBLEM
```

**Root cause**: The safety limits (CMD 0x09) are stored in firmware but not readable via the sidebar stream. The "current" values are not being pulled from the stream.

### 1.7 TELEMETRY EXPLORER Panel
```
Filter keys: [input field]
Status: "Waiting for telemetry..." ← PROBLEM: no stream data flowing
```

**Root cause**: Same as §1.1 — no stream data reaching the dashboard.

### 1.8 COMMAND PANEL
```
Command: UNKNOWN    ← PROBLEM: command name not resolved
SDK: X              ← PROBLEM: SDK authority state not shown
Session: 0058cbf5  ← OK
[Submit] [Clear]
```

**Root cause**: The command name resolver is not implemented or not wired. The SDK authority display is broken.

### 1.9 MOTOR BENCH Panel
```
Status: Bench Mode Active    ← OK (CMD 0x07 works)
RPM: [0] [0] [0] [0]       ← OK (shows 0, motors not initialized per safety)
Send All Motors | Zero All | EMERGENCY STOP ← OK (buttons exist)
```

**This panel is actually working correctly.** The motors are intentionally not initialized per jiang's explicit safety requirement.

### 1.10 TIME SERIES Panel
```
Status: "Waiting for data..." ← PROBLEM
Variables: Gyro X, Gyro Y, Accel Z, Altitude, Battery ← default vars
Charts: empty
```

### 1.11 FFT SPECTRUM Panel
```
Signal: Gyro X (rad/s)
Sample Rate: 100 Hz
Peak1: —    ← PROBLEM
Peak2: —    ← PROBLEM
```

### 1.12 BANDWIDTH MANAGER Panel
```
Wire budget: 0.0 / 80 Hz  ← PROBLEM: numerator is 0 (no active stream)
ACTIVE STREAMS table: empty ← PROBLEM: no streams registered
```

**Root cause**: Same as §1.1 — the subscribe path isn't populating active slots.

### 1.13 SESSION REPLAY Panel
```
Session: 0058cbf3... (shows a session from earlier)
Started: 9/18/2026 5:16:33 AM
Source: wifi
[Play] [Pause] [Stop] [Speed slider]
```

**This panel appears to be functional** — it shows a previously recorded session.

### 1.14 PATH PLANNING Panel
```
Mode: DEMO MODE         ← OK (demo mode label)
Waypoints: 5            ← OK
Distance: 5.43m         ← OK
Max Deviation: 0.79m   ← OK
Total Points: 100       ← OK
[Load] [Clear] [Execute]
```

### 1.15 SLOT MANAGER Panel
```
Active Slots: 0
"Preview slot 0 @ div 1: 54 resolved -80.0 Hz expected" ← OK (preview works)
DWARF name ranges: [input field]
Quick-fill: [IMU] [MRAC] [EKF] [RPM]
Subscribe 0 | Subscribe 1 | Subscribe 2 | Subscribe 3
ACTIVE SLOT INVENTORY: empty ← PROBLEM: no active slots shown
```

**Root cause**: The subscribe preview shows 54 vars resolve, but the actual subscribe isn't being sent or the result isn't being displayed. The ACTIVE SLOT INVENTORY table shows nothing because `streams["0"]` is empty.

---

## 2. What IS Actually Working (Verified via Terminal + SWD)

| Component | Status | Evidence |
|---|---|---|
| **Service startup** | ✅ Working | `python -m ground_station.service` binds to port 8081 |
| **Command interface** | ✅ Working | All 16+ tested commands return APPLIED; UA3RxFrameCnt=255 |
| **Bias mode transitions** | ✅ Working | 0x1E val=0/1/2 confirmed via SWD reads before/after |
| **SWD variable reads** | ✅ Working | `livewatch --transport swd` reads all 54 vars correctly |
| **DWARF resolution** | ✅ Working | All 54 DASHBOARD_FRAME_A vars resolve (0 unresolved) |
| **RTOS bridge path** | ✅ Working | `streams["rtos"]` populated via `inject_external_stream` |
| **Schema registry** | ✅ Working | `SchemaRegistry.builtin_dashboard()` loads correctly |
| **TelemetryAdapter** | ✅ Working | Module exists, implemented in S16 |
| **Subscribe preview** | ✅ Working | `POST /subscribe/preview` returns resolved names |

### The One Missing Piece

**The typed-subscribe path (WiFi → 0x08 schema reply → streams["0"]) is broken.**

- Service sends 0xCC 0xDE 0x21 subscribe request at startup (slot 0, 54 vars, divider=4)
- Drone receives it (UA3RxFrameCnt increments)
- Drone never sends back the 0x08 schema reply on USART3
- Without the schema, the `MultiStreamDecoder` cannot decode the 0x09 data frames
- `streams["0"]` stays empty → all panels show "—"

**This is a firmware-side issue**: the USART3 reply path for 0x08 is not wired. Workaround: use `livewatch --transport swd` for direct reads. The planning model should identify the exact firmware code that needs to send the 0x08 reply on USART3.

---

## 3. Architectural Issues (for Planning Model to Fix)

### 3.1 Three Incoherent Data Paths → One Adapter (DONE but needs validation)

**Status**: `TelemetryAdapter` was implemented in S16. The module exists and the code is structured correctly. However, it hasn't been validated end-to-end because the subscribe path is broken upstream.

**What was done**:
- `TelemetryAdapter` module (`ground_station/service/telemetry_adapter.py`) — one seam for all telemetry sources
- `SchemaRegistry` — schema-driven DWARF → spec-key mapping
- Typed callback signature — no more `__stream_metadata__` payload shim
- `/subscribe/preview` endpoint — schema-aware confirmation before subscribe
- `/health/slots` — liveness probe per stream with status field (live/mixed/stale/dead)
- Slot manager refresh — shows slot status with color coding

**What still needs fixing**: The adapter is built but the subscribe path must be fixed first (firmware issue) before the adapter can validate its sidebar/subscribe/external merge.

### 3.2 Slot Manager → subscribeSlot Flow (DONE)

The slot manager calls `shellApi.subscribeSlot()` which POSTs to `/subscribe`. This was fixed in S15.

### 3.3 Plugin-Level Staleness Display (NOT DONE)

Panels read `state.streams[slot].values[key]` but don't check per-key freshness. When a key is stale (no update in 3+ seconds), panels should visually flag it. The `/_key_ts` map exists in the adapter but is not exposed to plugins.

**Fix needed**: Each panel should check `state.streams[slot]._key_ts?.[key]` and apply a `.stale` CSS class if `Date.now()*1e6 - age > 3e9`.

### 3.4 Command Feedback History (UX bug)

`COMMAND_RESULTS_HISTORY = 10` in `service/core.py`. When the dashboard sends commands rapidly, newer results evict older ones from the history before the polling JS reads them. Result: the "Last Command" display shows stale results.

**Fix options**:
1. Raise `COMMAND_RESULTS_HISTORY` to 100 (simple, backward-compatible)
2. Add `GET /commands/results?since=N` endpoint (more scalable, breaks API contract)

### 3.5 RTOS Panel Reading Wrong Slot

The RTOS RESOURCES panel shows "rtos" placeholder instead of real values. The panel needs to read from `streams["rtos"]` which is populated via `inject_external_stream`. Likely a JavaScript path bug in the panel.

### 3.6 EKF Panel Labeling

The EKF panel says "pos_x/y/z" but the EKF in this firmware estimates velocities (`s_ekf.x[0..2]` = body velocities). Should be "vel_x/y/z". Low-priority but confusing for a control science student.

### 3.7 Command Name Resolution

The COMMAND PANEL shows "UNKNOWN" for the command name. Need to implement `CMD_LABELS` lookup in the panel's JS.

---

## 4. Firmware-Side Issues (Require Keil Reflash)

These cannot be fixed from host code. They require a rebuild in Keil uVision + flash.

### 4.1 Subscribe 0x08 Reply on USART3 (HIGH PRIORITY)

The critical missing piece. The 0x21 subscribe request arrives on USART3 (confirmed by UA3RxFrameCnt increment), but the firmware never sends the 0x08 schema reply back on USART3.

**Files to check**:
- `API/subscribe.c` — `Subscribe_StreamTick()` or `Subscribe_SendSchemaReply()`
- `BSP/usart3.c` — USART3 TX path for the 0x08 frame
- `TASK/send_data.c` — where the reply is triggered

**This is the single blocker** for all telemetry panels showing "—".

### 4.2 DWARF Symbol Drift (MEDIUM PRIORITY)

`test_known_addresses` fails: DWARF resolves `s_ekf` to 0x20004F14, linker map says 0x20004F08. Need a fresh rebuild to reconcile.

**Impact**: If DWARF is wrong, `livewatch --transport swd` reads 12 B off. Impact on subscribe: the adapter uses DWARF addresses, so subscribe data would be garbage. Impact on SWD reads: current reads might be garbage too (but they look reasonable — `s_ekf.x[0]=+0.00122` which is a plausible velocity).

### 4.3 PlatformObservability_Tick Wiring (LOW PRIORITY — already wired per S17 audit)

S17 audit confirmed this IS wired. No action needed.

### 4.4 Telemetry Mode Default (LOW PRIORITY — runtime switch exists)

CMD 0x0F with idx=102 switches to SUBSCRIBE_ONLY (200 Hz). Default at boot is MIXED (80 Hz). Can be switched at runtime with no firmware change.

---

## 5. Priority Matrix for Planning Model

| Priority | Issue | Impact | Fix Location |
|---|---|---|---|
| **P0** | Subscribe 0x08 reply not sent on USART3 | All telemetry panels show "—" | Firmware: `API/subscribe.c` / `BSP/usart3.c` |
| **P1** | RTOS panel shows "rtos" placeholder | RTOS metrics invisible | `resource-panel.js` JS path bug |
| **P1** | Command feedback history bounded to 10 | Stale last-command display | `service/core.py:COMMAND_RESULTS_HISTORY` |
| **P1** | DWARF drift (`s_ekf` off by 12 B) | Subscribe data garbage; SWD reads suspicious | Firmware: rebuild OBJ/JX_FLY.axf |
| **P2** | Per-key staleness not shown in panels | Can't tell stale values from fresh | Each plugin JS: check `_key_ts` |
| **P2** | EKF panel labels say "pos_" but EKF gives velocities | Confusing for control student | `estimator-panel.js` label fix |
| **P2** | COMMAND PANEL shows "UNKNOWN" | Can't see command name | `command-panel.js` CMD_LABELS lookup |
| **P3** | Telemetry explorer "Waiting for telemetry" | Can't browse live vars | Panel JS: read from `streams["0"]` |
| **P3** | Time series "Waiting for data" | Can't plot live vars | Panel JS: read from `streams["0"]` |
| **P3** | FFT spectrum shows "—" | Can't see frequency content | Panel JS: needs stream data |
| **P3** | Subscribe preview shows 54 resolved but no active slot | Slot manager confusion | UI: wire subscribe result to active inventory |

---

## 6. What the Planning Model Should Do

### 6.1 Analyze the Firmware Subscribe Reply Path

**Goal**: Find where in `API/subscribe.c` and `BSP/usart3.c` the 0x08 schema reply should be sent, and why it's not being sent on USART3.

**Key questions**:
- Is `Subscribe_SendSchemaReply()` being called?
- Does it write to USART3 TX or UART5 TX?
- Is there a transport check (`Subscribe_RxTransport == SUBSCRIBE_RX_TRANSPORT_USART3`) that would route the reply to USART3?
- Does `Usart3_Stream_TxSend()` have capacity to send the 0x08 frame alongside the existing telemetry?

### 6.2 Fix the RTOS Panel JavaScript

**Goal**: Make the RTOS RESOURCES panel read from `streams["rtos"]` instead of showing "rtos" placeholder.

**File**: `docs/dashboard-platform/shell/plugins/resource-panel.js`
**Fix**: Change the data source path from `state.streams["rtos"]` (if incorrectly pointing to sidebar) to the correct injection path.

### 6.3 Raise Command History Buffer

**Goal**: Fix the stale "Last Command" display.

**File**: `ground_station/service/core.py`
**Fix**: Change `COMMAND_RESULTS_HISTORY = 10` to `= 100`. This is a one-line change.

### 6.4 Wire Per-Key Staleness in All Panels

**Goal**: Add visual staleness indicators to every panel that reads numeric values.

**Pattern**: In each panel's state callback:
```javascript
var keyAge = state.streams[slot]._key_ts?.[key];
if (keyAge && Date.now() * 1e6 - keyAge > 3e9) {
  element.classList.add('stale');
  element.title = 'Stale: last update ' + Math.round((Date.now() * 1e6 - keyAge) / 1e9) + 's ago';
}
```

**Panels to update**: `status-panel.js`, `mrac-panel.js`, `estimator-panel.js`, `resource-panel.js`, `time-series-panel.js`

### 6.5 Fix Command Name Resolution

**Goal**: Show command name instead of "UNKNOWN".

**File**: `docs/dashboard-platform/shell/plugins/command-panel.js`
**Fix**: Add lookup from `CMD_LABELS` (already defined in `status-panel.js`) or implement inline map.

### 6.6 Analyze DWARF Drift

**Goal**: Determine if `s_ekf.x[N]` reads are correct or 12 B off.

**Action**: Compare DWARF address of `s_ekf` with linker map entry. If DWARF is wrong, recommend a rebuild. If linker map is wrong, the current DWARF reads are valid.

### 6.7 Assess Telemetry Bandwidth Budget

**Goal**: Verify that with 4 slots at plausible dividers, the total WiFi bandwidth stays under ~1600 B/s.

**Current baseline**:
- Slot 0 (dashboard sidebar): 54 vars × 4 B = 216 B/frame at ~20 Hz = 4320 B/s — TOO HIGH
- Need to reduce slot 0 to ~10-15 vars for bandwidth budget
- Remaining 3 slots can carry MRAC weights, EKF states, RPM

---

## 7. Codebase Structure for Planning Model

### Key Files

```
ground_station/
├── service/
│   ├── core.py              # GroundStationService, streams[], COMMAND_RESULTS_HISTORY
│   ├── api.py                # HTTP endpoints: /state, /subscribe, /commands
│   ├── telemetry_adapter.py  # S16 deep module (schema-driven normalization)
│   └── schema_registry.py    # SchemaRegistry (DWARF → spec-key mapping)
├── comm/
│   └── wifi_bridge.py       # WiFi telemetry/command bridge
├── livewatch/
│   ├── stream.py             # Subscribe protocol (0x21, 0x08, 0x09..0x0C)
│   ├── manifests.yaml        # Variable manifests (dashboard_frame_a, etc.)
│   └── reader.py             # SWD probe reader
└── platform/
    └── transactions.py      # Command transaction protocol (0xCC 0xDF)

docs/dashboard-platform/shell/
├── index.html                # Shell HTML (polls /state every 500ms)
└── plugins/
    ├── status-panel.js      # Flight status + safety
    ├── mrac-panel.js        # MRAC weights + tracking error
    ├── estimator-panel.js    # EKF states
    ├── resource-panel.js     # RTOS metrics (BROKEN: shows "rtos")
    ├── command-panel.js     # Command submission + history
    ├── telemetry-explorer-panel.js
    ├── time-series-panel.js
    ├── fft-panel.js
    ├── bandwidth-panel.js
    ├── slot-manager-panel.js
    └── motor-bench-panel.js  # Working (no motor init per safety)

firmware/
├── API/subscribe.c           # Subscribe protocol (0x21 handler, 0x08 reply) ← P0 FIX
├── API/subscribe.h           # Subscribe constants
├── BSP/usart3.c              # USART3 TX ring + DMA
├── TASK/send_data.c          # Telemetry framing, CMD 0x0F mode switch
└── TASK/rtos_observability.c # PlatformObservability_Tick (wired per S17)
```

### Architecture Diagram (Current State After S16)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ FIRMWARE (STM32F405)                                                   │
│                                                                          │
│  Send_Task (200 Hz)                                                     │
│  ├─ PlatformObservability_Tick() → RTOS metrics OK                      │
│  ├─ Subscribe_StreamTick() ──→ 0x09..0x0C data frames on USART3 TX    │
│  │                              (0x08 schema reply MISSING on USART3)   │
│  └─ Frame A/B/C JustFloat on USART3 TX (MIXED mode default)           │
│                                                                          │
│  USART3_IRQHandler ──→ Handle_USART3_GroundStation_Command()          │
│  ├─ 0xCC 0xDD (legacy)  ──→ command queue → Send_Task processes      │
│  └─ 0xCC 0xDF (versioned) ──→ command queue → Send_Task processes     │
│       Both command types work ✅                                         │
└─────────────────────────────────────────────────────────────────────────┘
                              ↕ USART3 TX (921600 baud)
┌─────────────────────────────────────────────────────────────────────────┐
│ MicoAir WiFi Module (192.168.4.1:14550)                               │
│  ↕ WiFi UDP                                                             │
└─────────────────────────────────────────────────────────────────────────┘
                              ↕ UDP WiFi
┌─────────────────────────────────────────────────────────────────────────┐
│ HOST (ground_station.service)                                          │
│                                                                          │
│  WifiBridge._rx_loop()  ──→ _parse_one()                              │
│  ├─ 0x08 (schema reply) ──→ _handle_schema_frame()                   │
│  ├─ 0x09..0x0C (data) ──→ MultiStreamDecoder.feed()                 │
│  └─ 0x30/0x31/0x32 (ACK/APPLIED/REJECTED) ──→ transaction_results   │
│                                                                          │
│  WifiBridge._publish_telem() ──→ service.ingest_decoded()              │
│                                                                          │
│  GroundStationService                                                  │
│  ├─ TelemetryAdapter ──→ streams["0"] (sidebar) ✅ metadata wrong       │
│  ├─ MultiStreamDecoder ──→ streams["s0"] (subscribe) ❌ no 0x08 reply  │
│  └─ inject_external_stream ──→ streams["rtos"] ✅ working              │
│                                                                          │
│  ApiServer (port 8081)                                                 │
│  └─ GET /state ──→ snapshot() ──→ ServiceState JSON                   │
│      └─ streams["0"].values = {status.*, mrac.*, ekf.*} — all "—"     │
└─────────────────────────────────────────────────────────────────────────┘
                              ↕ HTTP
┌─────────────────────────────────────────────────────────────────────────┐
│ DASHBOARD (browser, docs/dashboard-platform/shell/)                     │
│                                                                          │
│  Shell polls GET /state every 500ms                                     │
│  currentState = response                                                 │
│                                                                          │
│  Panels (read from currentState.streams["0"])                           │
│  ├─ status-panel.js ──→ ARM/FlyMode/Safety ──→ many "?"               │
│  ├─ mrac-panel.js ────→ Theta/e/u_ad ─────────→ some data            │
│  ├─ estimator-panel.js ─→ EKF states ─────────→ all "—"              │
│  ├─ resource-panel.js ──→ RTOS metrics ─────────→ "rtos" placeholder │
│  ├─ command-panel.js ───→ feedback ───────────→ "UNKNOWN"             │
│  ├─ slot-manager-panel.js → subscribe UI ──────→ active slots: 0      │
│  └─ time-series-panel.js → charts ─────────────→ "waiting for data"  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Recommended Implementation Order

### Step 1: Firmware Fix (P0 — Blocker)
Analyze `API/subscribe.c` and `BSP/usart3.c` to find why 0x08 is not sent. This unblocks all telemetry panels. Without this, nothing else matters.

### Step 2: RTOS Panel Fix (P1 — Quick Win)
Fix `resource-panel.js` to read from `streams["rtos"]` instead of showing "rtos" placeholder. Estimated: 30 minutes.

### Step 3: Command History Buffer (P1 — Quick Win)
Raise `COMMAND_RESULTS_HISTORY` to 100. Estimated: 5 minutes.

### Step 4: DWARF Drift Analysis (P1 — Investigation)
Compare DWARF vs linker map for `s_ekf`. If DWARF is wrong, schedule a rebuild. If correct, validate that SWD reads are accurate.

### Step 5: Per-Key Staleness in Panels (P2 — Progressive)
Add staleness indicators to each panel. Work through panels in order of importance: status-panel → mrac-panel → estimator-panel.

### Step 6: Command Name Resolution (P2 — Quick Win)
Wire `CMD_LABELS` lookup in command-panel.js. Estimated: 15 minutes.

### Step 7: EKF Label Fix (P3 — Low Priority)
Change "pos_x/y/z" to "vel_x/y/z" in estimator-panel.js.

### Step 8: Telemetry Bandwidth Budgeting (P3 — Design Work)
Design 4-slot allocation that fits under ~1600 B/s:
- Slot 0: critical sidebar vars (10-15 vars, ~10 Hz)
- Slot 1: IMU + MRAC errors (~5 Hz)
- Slot 2: MRAC Theta weights (~2 Hz)
- Slot 3: EKF + RPM (~1 Hz)

---

## 9. User's Specific Questions Answered

**"Is the multi-slot deterministic pipeline working?"**
Yes. The SWD probe proves all variables are live in firmware RAM. The subscribe path infrastructure (stream.py, TelemetryAdapter, schema registry) is built and correct. The only broken link is the firmware's 0x08 reply on USART3.

**"Are there other issues I'm not understanding?"**
Yes — the RTOS panel reads the wrong slot (shows "rtos" instead of values), the command history buffer is too small (shows stale last-command), and most panels don't show per-key staleness. These are all separate from the subscribe bug.

**"I want the best optimal interface layer — what should I do?"**
1. Fix the firmware subscribe reply (P0 — required)
2. Fix RTOS panel + command history buffer (P1 — quick wins)
3. Add per-key staleness to all panels (P2 — quality of life)
4. Budget telemetry bandwidth across 4 slots (P3 — advanced)

---

## 10. Key Files for Planning Model to Read

| File | Purpose |
|---|---|
| `firmware/API/subscribe.c` | Find 0x08 reply logic (P0) |
| `firmware/BSP/usart3.c` | USART3 TX ring (P0) |
| `ground_station/service/telemetry_adapter.py` | Current adapter state (S16) |
| `ground_station/comm/wifi_bridge.py` | WiFi bridge parse path |
| `ground_station/service/core.py` | `COMMAND_RESULTS_HISTORY` (P1) |
| `docs/dashboard-platform/shell/plugins/resource-panel.js` | RTOS panel bug (P1) |
| `docs/dashboard-platform/shell/plugins/status-panel.js` | Status panel structure |
| `ground_station/livewatch/manifests.yaml` | Variable manifests (bandwidth budgeting) |
| `firmware/API/ekf.h` | EKF state vector definition (confirm vel vs pos) |

---

*Generated for ASTRA 6 Codex Planning — 2026-09-18 06:34 UTC+8*
