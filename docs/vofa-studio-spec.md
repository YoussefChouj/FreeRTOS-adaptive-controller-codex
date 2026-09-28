# VOFA Studio — spec (2026-09-28)

A small local web app that replaces `ground_station/livewatch/flight_stream.ps1`:
pick or edit a variable preset, see the link budget, stream to CSV and to VOFA+
with named channels, then stop by timer or keep a rolling window.

## Decision

Local web app: aiohttp backend (already installed; FastAPI/Flask are not) plus one
static HTML/JS page. Run `python -m ground_station.vofa_studio` to open
http://127.0.0.1:8090. Do not use 8081, which belongs to the dashboard service.
No new dependencies. One stream at a time.

## Existing pipeline to reuse (do not duplicate)

- `ground_station/livewatch/stream_log.py`:
  - `run_groups` (l.466) and `_run_groups_usart3` (l.376-465): subscribe, decode, write CSV, forward to VOFA.
  - Helpers: `resolve_ranges` (l.71), `columns_for` (l.119), `_slot_path` (l.575), `parse_group` (l.308).
- Refactor `_run_groups_usart3` into a runner that takes a `stop_event` and an `on_row(slot, seq, t_ms, flat)` callback. The CLI path must keep its current behaviour: `flight_stream.ps1` still works, and a stop request is still sent for every slot in `finally`.
- `ground_station/livewatch/stream.py`: `build_stream_request`, `stream_bps` (l.315), `MultiStreamDecoder`, and the constants below.
- `ground_station/livewatch/symbols.py`: `SymbolResolver(elf).resolve(path)`, `.names()`, `.fields_of(path)`. Use these for autocomplete and validation. ELF is `OBJ/JX_FLY.axf`.
- WiFi transport: `Usart3WifiSubscribeTransport` (drone AP 192.168.4.1).

## Link budget (mirror the firmware; values from stream.py)

- `divider = clamp(round(100 / rate), 1, 255)`. The actual rate is `100 / divider`: a requested 30 Hz gives divider 3, so 33.3 Hz. Show the actual rate.
- Per slot: `B/s = (FRAME_OVERHEAD 12 + payload_bytes) * 100 // divider`.
- Total budget across all slots: `921600 // 10 * 95 // 100 = 87552 B/s` (USART3, `BUDGET_PCT` 95).
- Hard limits:
  - 4 slots (`MAX_SLOTS`)
  - 62 ranges per slot (`MAX_STREAM_RANGES`)
  - 1024 B per frame (`STREAM_MAX_BYTES`)
  - element size 1, 2 or 4 bytes
  - `SUBSCRIBE_MAX_FRAMES_PER_TICK` 4
- UI: a per-slot bar and a total bar showing remaining B/s. Block Start when over budget.

## VOFA+ facts (measured on this PC)

- Data is FireWater text over UDP to 127.0.0.1:1347: `"v0,v1,...\n"`. Channel order is the order we send.
- Channel names are in `%LOCALAPPDATA%\vofa+\100\context\vofa+.config.json`:
  - Key `settingsPanel.ctx["."].settings_ctx[i]`, entries shaped `{is_draw,color,scale,yoffset,xoffset,decimal,value,name}`, where `name` defaults to `"I0"`, `"I1"`, …
  - It is nested deep, so find it by recursive key search.
  - Append entries when there are more channels than existing entries.
- VOFA+ rewrites that file when it exits, so only patch it while VOFA+ is closed. Sequence:
  1. Close VOFA+ gracefully (it saves).
  2. Wait for the process to exit.
  3. Back up the config.
  4. Patch the names.
  5. Relaunch VOFA+ from the target of `C:\ProgramData\Microsoft\Windows\Start Menu\Programs\VOFA+\x64\vofa+.lnk`.
- Prior art worth reading for pitfalls: `../FreeRTOS---Six_Degrees_of_Freedom _Adaptive_controller/ground_station/gui/vofa_manager.py` and `docs/vofa_manual_setup_checklist.md` in that repo.

## Features

1. **Presets:** stored as `ground_station/vofa_studio/presets/<name>.json`, containing `{name, notes, slots:[{rate, vars:[...]}], vofa:[var,...]}`. Ship `flight_default` with exactly the three groups from `flight_stream.ps1`.
2. **Preset editor:**
   - Add variables with ELF autocomplete.
   - Set the rate per slot.
   - Move a variable between slots.
   - Resolve each entry; show its size and an error inline.
   - Save or save-as.
3. **Budget panel:** as described in the Link budget section.
4. **Log modes:**
   - **Timed:** stop after N s.
   - **Rolling window:** keep only the last N s. Write 10 s segment files per slot, delete segments older than the window, and merge them into `<name>.slotN.csv` on stop. This survives a crash, which matters because this laptop loses power.
   - **Unlimited.**
5. **VOFA+ channels:**
   - Choose any variables from any slot.
   - Emit with sample-and-hold at the rate of the fastest selected slot.
   - Offer an "Apply names + restart VOFA+" button.
6. **Live status:**
   - Rows/s, loss %, dropped and CRC errors per slot.
   - Elapsed and remaining time.
   - File sizes and link state.
   - Latest values of `DroneStatus.ARM_Status`, `real_voltage` and `flight_phase` when they are streamed.
7. **Event marker:** a button and the `M` hotkey write `t_host,t_src_ms,note` to `<name>.events.csv`.
8. **Session files:**
   - `<name>.meta.json` holds a snapshot of the preset, the git hash, the ELF mtime, notes, start and end times, and per-slot stats.
   - Output goes to `logs/vofa/` as before.
   - Name auto-increments (flight8 → flight9).
9. **Later (phase 2):** a list of past sessions with a quick-look plot.

## Tests (scoped)

Cover each of these:
- budget maths
- preset load, save and validate
- VOFA config patch, on a fixture copy and never the real file
- rolling-segment pruning and merging
- the runner loop, with a fake transport and `stop_event`

## Safety

- The app never arms or spins motors.
- It never POSTs to 8081.
- Stop requests are always sent for every subscribed slot.
