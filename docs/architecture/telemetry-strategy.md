# Telemetry pipeline strategy (2026-09-19)

Synthesis of two independent static audits (`telemetry-audit-agy.md`,
`telemetry-audit-opencode.md`), spot-checked against the code. This is the
target architecture for the dashboard and for agent access; the WP plan is
reordered to match.

## Goal

The drone is operated through one interface for years while the firmware
keeps changing (new controllers, variables, features). So:

1. **Self-describing firmware.** Adding a variable in C makes it readable,
   streamable, loggable and plottable with no hand-written ground code.
2. **Self-evolving ground side.** The ground station is generic: it learns
   names, types and addresses from the running build, never from a
   hand-maintained list.
3. **Agent-equal.** Everything the dashboard can do, an agent can do through
   the same JSON API/CLI; the dashboard is just one client of it.
4. **Verified control.** A command or parameter write is only "applied" once
   its effect is read back.

## Target architecture: four planes

| Plane | Carries | Link |
| --- | --- | --- |
| Identity | build_id handshake, ELF/DWARF lookup by build_id | both |
| Data | subscribe streams (multi-rate slots, DWARF address ranges) | WiFi/USART3 primary, UART5 fallback |
| Deep | memory read/write, params, flash, RTT high-rate logs, SWO | SWD (wireless CMSIS-DAP) |
| Control | commands + params with read-back, arm gate, EKF shadow gate | WiFi, verified via either link |

Agent plane on top: one catalog/capability API (`variables`, `streams`,
`commands`, `build`), versioned, JSON everywhere. Dashboard panels are data
(manifests naming DWARF variables), so a new controller gets a panel by
adding a manifest, which an agent can write.

**Link split.** WiFi carries continuous streaming and commands (USART3 has
large wire headroom). SWD carries what WiFi can't: arbitrary memory access,
flashing, and RTT for kHz-rate controller logs that exceed the WiFi budget.
Today RTT is not used at all; that is the biggest capability left on the
table for controller development.

## Verified gaps (checked in code)

| # | Gap | Evidence | Why it matters |
| --- | --- | --- | --- |
| G1 | Subscribe RX buffer is 256 B; larger requests are dropped | `BSP/usart5.h:12`, `BSP/usart5.c:240` | Caps how many variables one slot can stream; host slot-0 request is bigger than this |
| G2 | Host sends several batches per slot, firmware replaces the slot on each | `wifi_bridge.py:3405`, `API/subscribe.c:846` | Only the last batch streams; the rest silently vanish |
| G3 | No build identity check: host uses local ELF/DWARF without matching the flashed build | no `build_id` in `wifi_bridge.py` | Stale ELF = wrong addresses = wrong data that looks valid. Blocks self-evolution |
| G4 | Telemetry TX busy-waits on DMA | `TASK/send_data.c:666,1260` | Send_Task throttled (worker estimate: 200 Hz to about 80 Hz) |
| G5 | No RTT | no SEGGER_RTT in firmware | No high-rate log path |

Worker-reported, not yet verified: param writes report APPLIED on dispatch
without read-back; `s_ekf` DWARF vs map offset drift (12 B); command-result
history of 10; no JSON variable-catalog endpoint; `DASHBOARD_FRAME_A_VARS`
hand-maintained; EKF channels labelled position but estimate velocity.

Rejected: "0x08 schema reply not wired on USART3" (opencode #2) is stale;
reply routing was fixed on 2026-09-08 (`API/subscribe.c:336`).

## Reordered plan

**Phase 0: foundations (firmware + host, one flash)**
1. G3 build identity: firmware exposes a build_id; host refuses or reloads
   on mismatch; flashtool archives every flashed ELF by build_id.
2. G1 + G2: size RX staging to the largest legal request; make the host
   send one request per slot (or add an explicit append mode). Replaces the
   old "raise range limit to 62" bug fix.
3. G4: non-blocking double-buffered DMA TX.
4. Old bug #1 (`_pending_schema_ranges` leak).
5. Param/command read-back verification.

**Phase 1: self-description (was WP7)**
Variable/capability catalog generated from DWARF for the flashed build_id,
served as JSON; dashboard panels become manifests over the catalog.

**Phase 2: capture (was WP5)**
Byte-budget rate planner per link, raw frames stored with build_id + schema.

**Phase 3: deep channel**
RTT log channel for high-rate controller data, same catalog names.

**Phase 4: verification (was WP6 + WP8)**
Headless browser harness, then the 7 journeys, run by agents.
