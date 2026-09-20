# Telemetry Pipeline Architecture Audit — 2026-09-19

**Scope:** Static analysis only. No hardware touched. Both links (WiFi USART3 + UART5 SWD) traced end-to-end.

---

## 1. Current Architecture End-to-End

### WiFi Link (USART3 → MicoAir → UDP 14550)

**Firmware producers:** `TASK/send_data.c` generates legacy Frame A (0x01, 100 Hz), Frame B (0x02, 20 Hz), SysID (0x03), OFCal (0x05). `API/subscribe.c` handles the typed subscribe protocol: 0x21 request → 0x08 schema → 0x09+slot data frames. `BSP/usart3.c` drives USART3 TX via DMA1_Stream3 ring (4096 B) at 921600 baud (BRR=0x2E → 913043 baud actual).

**Framing:** Every frame starts `0xAA 0xBB`, frame_type byte, LEN_HI/LEN_lo big-endian, payload, CRC8_XOR (xor over bytes 2..end). Subscribe data frames add a 4-byte source timestamp and CRC16-CCITT: 12-byte overhead total (`stream.py:72`, `API/subscribe.h`).

**Schema/registry discovery:** `ground_station/livewatch/symbols.py` (`SymbolResolver`, line 63) walks the ELF DWARF info — `DW_TAG_variable` entries with `DW_OP_addr` location. Resolves dotted paths like `mrac_state.roll.What[0]` to `(address, size, fmt)`. The host-side `catalog.py` (`SymbolCatalog.fetch_or_load`, line 233) sends a 0x25 GET_SYMBOL_TABLE request over UDP and compares the returned `firmware_id` (SHA-256 of ELF, `gen_symbol_table.py:314`) against the cached JSON. A mismatch triggers auto-refresh.

**How ground side learns names/types/addresses:** Three mechanisms:
1. **DWARF offline** (`symbols.py`): Full type resolution including struct offsets, array indices, typedef stripping. Used by `stream_log`, `watch`, `read`.
2. **0x25 live catalog** (`catalog.py`): Firmware emits a compact 12-byte-per-entry table (hash + name_offset + type + count + address). The host caches this; firmware_id detects reflashes.
3. **Manifests YAML** (`manifests.yaml`, `manifest_layout.yaml`): Hand-maintained slot variable lists with `dwarf` paths and `key` spec-keys. The `SchemaRegistry` (`service/schema_registry.py:56`) loads these and provides DWARF→spec-key mapping for the dashboard.

**Build-ID/ELF matching:** `gen_symbol_table.py:314` computes SHA-256 of the ELF → `firmware_id`. `catalog.py` verifies this against the live 0x25 reply. `rebuild_and_flash.py` snapshots `OBJ/JX_FLY.{axf,hex,map}` to `.prev-flashed/` before flashing so livewatch always resolves against the last-known-good build (`cli.py:133`). `verify.py` compares flash segments from the ELF against SWD-read bytes (20 chunks default).

**Command path (writes/params):** `0xCC 0xDD [CMD][IDX][float32 LE][CRC8]` over UART5 (or USART3 WiFi). 30 commands defined (`serial_bridge.py:27-57`, `COMMAND_SPEC.md`). Commands go to `TASK/send_data.c` handlers at lines ~1405 (ACK) and ~1645 (0x0F flags). `ground_station/platform/transactions.py` provides the versioned `0xCC 0xDF` envelope with `submitted/acknowledged/applied/rejected/timed_out/observed` lifecycle.

**Dashboard service:** `ground_station/service/core.py` (`GroundStationService`, line 183) owns `streams[]` dict. `TelemetryAdapter` (`service/telemetry_adapter.py`) merges typed-subscribe streams, legacy sidebar path, and external injection. `api.py` serves `GET /state` JSON at port 8081 every 500 ms poll. `CommandGateway` handles command submission and result polling at 20 Hz.

**Plugins:** `docs/dashboard-platform/shell/plugins/` — 16 JS files implementing Shell API v2 (`getState`, `subscribe`, `submitCommand`, `subscribeSlot`, `registerPanel`). `PLUGIN_DEVELOPER_GUIDE.md` defines the lifecycle. `STATE.md` confirms schema frozen at `r1-s1-9F32E2EA`, 389 tests pass, no candidate/active plugin on drone.

### UART5 Link (CMSIS-DAP SWD + VCP)

**Firmware producers:** Same subscribe handlers, but USART5 RX DMA receives 0xCC 0xDE (subscribe) and 0xCC 0xDD (command) frames. `BSP/usart5.c` routes these to `Handle_USART3_GroundStation_Command()` in `TASK/send_data.c`.

**Capabilities:** Full pyOCD layer via `ground_station/livewatch/probe.py` (`ProbeSession`, line 153): memory read/write, registers, flash, halt/step/reset, breakpoints, watchpoints, RTT, SWO trace, GDB server. `SwdCmsisDap` transport (`transport.py:167`) reads RAM by address for cold-read without hardware dependency. `Uart5LongRange` (`transport.py:279`) provides request-then-reply 0x07 frames for individual scalar reads at 115200 baud.

---

## 2. Hard Constraints with Numbers

| Parameter | Value | Source |
|-----------|-------|--------|
| USART3 baud | 913043 (BRR=0x2E) | `docs/telemetry-protocol.md:234` |
| Wire capacity | 91304 B/s | same |
| Send_Tick | 200 Hz nominal / 80 Hz MIXED | `stream.py:97-98` |
| Budget per tick | 456.5 B | `docs/telemetry-protocol.md:242` |
| WiFi UDP payload | ~90363 B/s | same |
| Efficiency | 98.8% of wire | `docs/telemetry-protocol.md:68` |
| Loss | 0.00% | `scratchpad/micoair_ladder.py` |
| UART5 CMSIS-DAP ceiling | ~1600 B/s real | `docs/telemetry-protocol.md:72` |
| Subscribe stream measured | 258.7 Hz (divider=1) | `docs/telemetry-protocol.md:56` |
| MicoAir uplink ceiling | ~1100 Hz | same |
| Max slots | 4 | `stream.py:60` |
| Max ranges/request | 62 (raised from 24) | `stream.py:50` |
| Max stream bytes | 1024 | `stream.py:51` |
| Frame overhead | 12 B (subscribe), 8 B (legacy) | `stream.py:72` |
| Firmware staging | USART3 mailbox 512 B; USART5 256 B | `BSP/usart3.h:38`, `BSP/usart5.h:12` |
| SWD bandwidth vs WiFi | WiFi is 55× CMSIS-DAP | `docs/telemetry-protocol.md:72` |
| Host safe wire pct | 80% | `manifest.py` |
| Firmware wire guard | 95% | `stream.py:111` |
| COMMAND_RESULTS_HISTORY | 10 (too small) | `core.py:44` |
| Slot freshness TTL | 30 s default | `core.py:111` |

**Critical bottleneck:** Slot-0 host request resolves 54 names → 54 one-element ranges → 442 bytes sent (`wifi_bridge.py:528-586`), but `BSP/usart5.h:12` allocates only 256 B staging buffer. Frame above 256 B is rejected by firmware (`BSP/usart5.c:241,273`). This is a confirmed P0 (`REVIEW_2026-09-18.md`).

**Contention:** SWD reads and UART5 VCP share the same ST-Link probe (`telemetry-protocol.md:364`). Cannot do SWD read + stream_log simultaneously. `preflight.py:152` checks for CMSIS-DAP holder.

---

## 3. Evolvability Analysis

### What is automatic when adding a new variable

1. **DWARF resolution** (`symbols.py:63`): Any file-scope global variable in the firmware is automatically discoverable by name. No address table maintenance needed. `SymbolResolver.resolve("new_controller.var")` works after rebuild without any ground-side code change.
2. **0x25 live catalog** (`catalog.py:233`): Firmware generates a symbol table at build time via `gen_symbol_table.py`. If the catalog JSON exists and `firmware_id` matches, it's used directly. If the firmware changed (new build), the catalog is auto-refreshed.
3. **Range packing** (`manifest_layer.py:191`): Adjacent same-size scalars within 4 bytes are automatically merged into one `StreamRange` entry, minimizing wire overhead.

### What requires manual edits

| Edit | File | Why |
|------|------|-----|
| Add to slot manifest | `manifests.yaml` | Slot variable list, divider, transport |
| Add dashboard sidebar vars | `manifest_layout.yaml` / `boot_default_layout.py` | `DASHBOARD_FRAME_A_VARS` list |
| Add command handler | `TASK/send_data.c` + `COMMAND_SPEC.md` | New CMD ID and semantics |
| Add VoFA+ channel map | `manifest_layer.py:349` | Auto-generated from manifest |
| Update tests | `tests/test_protocol_schema.py`, `tests/test_stream.py` | Cross-check against firmware headers |
| Plugin UI panel | `docs/dashboard-platform/shell/plugins/*.js` | New variables need dashboard visibility |

### What breaks on firmware change

1. **Stale ELF** (`verify.py`): `OBJ/JX_FLY.axf` addresses shift on every rebuild. Without `verify` before SWD reads, DWARF-resolved addresses point to wrong memory. Detected by SHA-256 mismatch in `catalog.py` or byte-compare in `verify.py`.
2. **Schema drift**: The `schema_id` (`r1-s1-9F32E2EA`) is frozen. A firmware rebuild that changes the symbol table changes the schema ID. `Health` endpoint reports `telemetry_schema_id=null` (`REVIEW_2026-09-18.md` P1) — the runtime device identity is not strongly established.
3. **Stale dashboard mapping**: `DASHBOARD_FRAME_A_VARS` in `boot_default_layout.py` is hand-maintained. If firmware adds new Frame A fields, the dashboard sidebar breaks until this list is updated.
4. **DWARF drift**: `s_ekf` resolved to 0x20004F14 via DWARF vs 0x20004F08 in linker map (`DASHBOARD_ANALYSIS_FOR_PLANNING.md:278`). A rebuild without `verify` produces garbage reads.

### Detection mechanism

- `python -m ground_station.livewatch verify` — SWD byte-compare, 20 chunks/segment
- `catalog.py:fetch_or_load(force_refresh=True)` — firmware_id mismatch triggers refresh
- `rebuild_and_flash.py:snapshot_artifacts()` — preserves `.prev-flashed/` triple
- `test_stream_firmware_parity.py` — greps `API/subscribe.h` to cross-check `stream.py` constants

---

## 4. Agent Compatibility Assessment

### CLI/API with JSON output

| Capability | Reachable via CLI? | JSON output? |
|------------|-------------------|-------------|
| List variables | `python -m ground_station.livewatch names --filter ...` | Yes (text, parseable) |
| Read by DWARF name | `python -m ground_station.livewatch read mrac_state.roll.What[0]` | Partial (formatted text) |
| Multi-slot capture | `python -m ground_station.livewatch.capture_preset flight_comprehensive --secs 10` | CSV output |
| Stream logging | `python -m ground_station.livewatch.stream_log --seconds 30 --out logs/run.csv` | CSV |
| Verify ELF | `python -m ground_station.livewatch verify` | Text + exit code |
| SWD memory read/write | `python -m ground_station.livewatch peek/poke/dump` | Partial |
| RTT/SWO | `python -m ground_station.livewatch rtt-read/swo-read` | Text |
| GDB server | `python -m ground_station.livewatch gdbserver` | Port-based |
| Diagnostics bundle | `python -m ground_station.service.api` `/api/diagnostics/bundle` | JSON |
| View model | `GET /api/view-model` | JSON |
| Events/faults/actions | `GET /api/events`, `/api/faults`, `/api/actions` | JSON |
| Command submission | `POST /api/commands` + `GET /state` for feedback | JSON lifecycle |

### Discoverability

- **Variable list**: `livewatch names` + `SymbolResolver` walks all DWARF variables.
- **Capabilities**: `livewatch probes`, `livewatch rtt-list`, `livewatch wp-list` enumerate probe capabilities.
- **Presets**: `multi_slot_presets.yaml` defines capture presets.
- **Commands**: `COMMAND_SPEC.md` documents 30 command IDs.

### Versioning

- `ADAPTER_PROTOCOL_VERSION = "v2"` (`core.py:62`)
- `schema_id` frozen at `r1-s1-9F32E2EA` (`STATE.md`)
- `firmware_id` from ELF SHA-256 (`gen_symbol_table.py:314`)
- `PLUGIN_DEVELOPER_GUIDE.md` defines Shell API v2

### Safety gates

- **Arm gate**: `rebuild_and_flash.py` refuses flash when armed (`AGENTS.md:87`). `arm_status_from_telemetry()` reads Frame A offset 32.
- **EKF shadow**: Explicitly documented. `s_ekf` is shadow-only; do not wire into control paths without approval.
- **Command preconditions**: `FirmwareContract` (`platform/firmware_contract.py:528`) defines dangerous-action policy. WP6 journey 6 tests unsafe command rejection.
- **Flash blocked when armed**: Hard constraint.

### Gaps

- **No unified JSON list of all variables**: `names` CLI outputs text; there's no `/api/variables` endpoint returning structured JSON with types/units/ranges.
- **No machine-readable capability manifest**: The 0x25 catalog is binary wire protocol; there's no REST endpoint returning the full catalog as JSON.
- **COMMAND_RESULTS_HISTORY = 10** (`core.py:44`): Agents polling for command feedback can lose results.
- **`/health` has `telemetry_schema_id=null`** (`REVIEW_2026-09-18.md`): Schema identity not established at runtime.

---

## 5. Division of Labour Between Links

### Current allocation

| Link | Current role | Bandwidth |
|------|-------------|-----------|
| WiFi (USART3→MicoAir→UDP) | Streaming telemetry + commands | 91304 B/s wire, ~90363 B/s payload |
| UART5 (CMSIS-DAP) | Subscribe control plane + commands | ~1600 B/s real ceiling |
| UART5 (SWD) | Deep inspection, flash, RTT, SWO | ~1600 B/s (shared probe) |

### What works

- WiFi streaming at 258.7 Hz single-variable, 22% wire util — excellent
- Commands over WiFi at ~1050 Hz — never saturates
- SWD for cold-read, verify, flash — necessary for hardware access
- RTT/SWO over SWD — high-rate debug logging without WiFi

### What's suboptimal

1. **Subscribe control plane on UART5 only**: `stream.py:20-22` confirms `0xCC 0xDE 0x21` requests are accepted on UART5 only (no reply DMA on USART3). The command request goes over WiFi (USART3), but the subscribe request goes over UART5. This creates a **split-path requirement**: you need both links connected to use the subscribe feature. A single-link failure breaks typed telemetry.
2. **UART5 at 115200 is a bottleneck**: The control plane runs at 115200 baud (~11520 B/s), which is 8× slower than the WiFi link. The `subscribe_presets.py` and `manifest.py` budget checks are calibrated for this, but it means the control plane cannot carry high-rate telemetry back.
3. **SWD and UART5 share the probe**: Cannot do SWD reads and UART5 streaming simultaneously. The `preflight.py` CMSIS-DAP holder check prevents conflicts but doesn't solve the fundamental contention.
4. **WiFi is underutilized for commands**: At ~1050 Hz uplink ceiling, WiFi has 30× headroom. Could carry more telemetry in the return path if firmware supported full-duplex.
5. **RTT/SWO is a hidden high-rate channel**: RTT can stream at much higher rates than WiFi for debug logging, but it's not integrated into the telemetry pipeline — it's a separate tool, not a data source for the dashboard.

### What's left on the table

- **No full-duplex WiFi**: The MicoAir routes downlink to the source of the most recent uplink. The firmware cannot proactively push telemetry to the PC without the PC first sending. This limits the ability to use WiFi as a symmetric data link.
- **No link aggregation**: The two links operate independently with no bonding or failover.
- **No WiFi-to-SWD tunneling**: SWD operations require the physical dongle; there's no way to do SWD reads over WiFi through the CMSIS-DAP.

---

## 6. Ranked Roadmap

### Top 10 findings

| # | Finding | Impact | Effort | Risk | Priority |
|---|---------|--------|--------|------|----------|
| 1 | **Slot-0 request exceeds 256 B firmware staging** (`wifi_bridge.py:528-586` → `BSP/usart5.h:12`). 54 names → 54 ranges → 442 B, firmware rejects. Blocks ALL telemetry panels showing data. | P0: Every panel shows "—" | Medium: Either reduce slot-0 vars or increase USART5 staging buffer | High: Firmware change required | **Critical** |
| 2 | **Subscribe 0x08 schema reply not wired on USART3** (`DASHBOARD_ANALYSIS_FOR_PLANNING.md:201`). Frame arrives but no reply → `MultiStreamDecoder` can't decode → `streams["0"]` empty. | P0: Typed subscribe completely broken | Medium: `API/subscribe.c` / `BSP/usart3.c` fix | High: Firmware rebuild + flash | **Critical** |
| 3 | **DWARF drift** (`s_ekf` off by 12 B). `DASHBOARD_ANALYSIS_FOR_PLANNING.md:278`. Subscribe data and SWD reads could be garbage. | P1: Data integrity uncertain | Low: Rebuild + verify | Medium: Rebuild required | **High** |
| 4 | **`/health` reports `telemetry_schema_id=null`** (`REVIEW_2026-09-18.md`). Runtime schema identity not established. Agents cannot verify schema freshness. | P1: Agents can't detect schema drift | Low: Wire `schema_id` into health endpoint | Low: Host-side fix | **High** |
| 5 | **`COMMAND_RESULTS_HISTORY = 10`** (`core.py:44`). Agents polling command feedback lose results in rapid sessions. | P1: Agent command lifecycle unreliable | Low: Change to 100 | Low: One-line host fix | **High** |
| 6 | **Subscribe control plane on UART5 only** (`stream.py:20-22`). Split-path requirement: need both links for typed telemetry. Single-link failure breaks subscribe. | P1: Architecture fragility | Medium: Add USART3 reply DMA or unify transport | Medium: Firmware change | **Medium** |
| 7 | **No unified JSON variable/capability manifest**. `names` CLI outputs text; no `/api/variables` or `/api/catalog` REST endpoint. | P2: Agent discoverability incomplete | Medium: Add `GET /api/catalog` returning full symbol table | Low: Host-side | **Medium** |
| 8 | **WiFi full-duplex blocked by MicoAir routing** (`telemetry-protocol.md:24`). Module routes downlink to most recent uplink source. Firmware cannot push proactively. | P2: Limits bidirectional telemetry | Medium: Firmware-side UDP echo or different MicoAir mode | Medium: Hardware/protocol constraint | **Medium** |
| 9 | **RTT/SWO not integrated into telemetry pipeline**. High-rate debug logging exists but feeds neither dashboard nor agent tooling. | P3: Wasted high-rate channel | Low: Add RTT reader to `TelemetryAdapter` | Low: Host-side | **Low** |
| 10 | **`DASHBOARD_FRAME_A_VARS` hand-maintained** (`boot_default_layout.py`). New firmware Frame A fields not reflected until manual update. | P3: Dashboard drift risk | Low: Auto-generate from DWARF | Low: Generator script | **Low** |

### Roadmap relative to WP5/WP6/WP7/WP8

- **WP5 (Capture/analysis)**: Blocked by findings #1 and #2. The capture system cannot work without a working subscribe path. WP5 rate planner depends on slot-0 being functional.
- **WP6 (Headless browser harness)**: Blocked by findings #4 (schema identity) and #5 (command history). The agent needs `/api/state` to have correct `telemetry_schema_id` and `COMMAND_RESULTS_HISTORY ≥ 100` to reliably poll.
- **WP7 (Firmware resource map)**: Blocked by finding #3 (DWARF drift). Any resource map built on stale ELF addresses is wrong.
- **WP8 (7 journeys)**: Journey 1 (connect, negotiate, show live) is blocked by #1/#2. Journey 3 (toggle MRAC flag, prove variable changed) is blocked by #3. Journey 6 (unsafe command rejection) needs `COMMAND_RESULTS_HISTORY` fix (#5).

### Recommended reordering

1. **Before WP5/WP6/WP7/WP8**: Fix findings #1, #2, #3 (firmware rebuilds required). These are hardware-dependent and cannot be validated in host-only testing.
2. **Concurrent with WP6**: Fix findings #4, #5 (host-side, no hardware needed).
3. **During WP5**: Address finding #6 (transport unification) as part of the rate planner work.
4. **Post-WP7**: Address findings #7, #10 (generation work) once DWARF is verified.

---

*Audit complete. Static analysis only — no hardware touched.*
