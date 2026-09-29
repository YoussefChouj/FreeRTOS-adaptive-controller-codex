# 3D panel flight UX + Streams panel — spec (2026-09-29)

Decisions from the operator grilling (Q1–Q21). Target: `docs/dashboard-platform/shell/plugins/path-panel.js`
(served copy `.agent-ops/served/path-panel.js`). No numbers here are measured unless marked.

## Build order

**Status (2026-09-29): items 1-3 implemented** (2203d9c, 18196b9, a87ad8f/bd474b7/16b7a91). Not yet exercised on live hardware.

1. **Fly mode** (for the flight tests): layout, threshold trail, event markers, metrics strip, shortcuts.
2. **Multi-log overlay.**
3. **Streams panel**, which brings VOFA Studio into the dashboard.

Until item 3 lands, do high-rate logging with an existing dashboard preset plus REC.
`storage.py` `CsvRecorder` already records every slot and every frame.

## 1. Fly mode

- **Modes:** Plan / Fly / Review replace the single long sidebar.
  In Fly mode the 3D view fills the panel; the Plan tools are hidden.
- **Drone marker:** a point only. No body, no motor bars.
- **Camera:** a fixed room view that auto-fits. It does not follow the drone.
- **Trail:**
  - Colour: green below the error threshold, red above it.
  - Threshold: slider. Default 0.10 m, which is a starting value and not measured.
  - Metric selector: 3D or xy. Default 3D.
  - Error = actual position minus the firmware position PID `.Des` (`path-panel.js:309`), so the trail works in hold as well as in path mode.
  - Length switch: Whole flight / Fading. **Default: Whole flight.**
- **Event markers:**
  - adaptation on/off
  - mode changes
  - path execute/stop
  - REC notes
  - agent findings
- **Metrics strip:**
  - current error
  - run RMS
  - % of time above the threshold
  - flight mode
  - adaptation on/off
  - Vbat
  - run time
- **Keys** (UI only, never a flight command):

  | Key | Action |
  | --- | --- |
  | `R` | REC |
  | `N` | note |
  | `F` | fit |
  | `T` | trail mode |
  | `1`–`4` | camera views |

- **Data for the trail and strip:** comes from slot 0 only. Slot 0 keeps its current contents and gains position xyz, position `.Des` xyz, mode, the adaptation flag and Vbat if any are missing. Check the budget before adding.

## 2. Multi-log overlay

- **Log picker:** an auto-listed dropdown of named saved REC sessions. Any number can be selected (N, not 2).
- **Colours:** one colour per log, with a per-log "colour by error" toggle.
- **Legend table:** RMS, max error and % above the threshold for each log.
- **Shared scrubber:** t = 0 at path execute; falls back to REC start.

## 3. Streams panel

- **One link owner: the 8081 dashboard.** VOFA Studio (`ground_station/vofa_studio`, :8090) becomes optional and refuses to start while 8081 owns the link.
- **Slot assignment:**
  - Slot 0 is fixed.
  - Slots 1–3 start with the dashboard layout's groups. Each can be swapped for a preset or custom variables.
  - A tab that loses its data shows "not streamed: slot N holds X" with a one-click **Restore**.
  - A tab also uses its variables when a custom preset carries them.
  - Before building, map which tab reads which slot. Do not guess.
- **Features to port from VOFA Studio:**
  - ELF autocomplete
  - per-slot and total budget bars
  - timed / rolling / unlimited logging
  - a "forward to VOFA+" toggle (FireWater to 127.0.0.1:1347)
- **Preset store:** the VOFA Studio JSON per-slot presets (`vofa_studio/presets/`) become the single store for slots 1–3. The YAML full layouts (`livewatch/multi_slot_presets.yaml`) stay read-only until retired.
- **Slot swaps:** disarmed only. Swapping while armed waits for a bench measurement showing that re-subscribing does not disturb loop timing.

### Phase 3 as built

- Backend `ground_station/service/streams.py`, routes in `api.py`: `GET /api/streams`, `/api/streams/presets[/<name>]`;
  `POST /api/streams/plan | apply | restore | presets | presets/delete | log/start | log/stop | forward`.
  `apply` answers 202 (accepted), 409 (armed, active preset, or busy) or 400 (over budget / unresolved variable).
- Frontend `shell/plugins/streams-panel.js` (panel "Streams", telemetry workspace).
- `python -m ground_station.vofa_studio` exits with code 2 while 8081 owns the link (`--dashboard-port` to override).
- Tests: `test_streams.py` (33), `test_streams_panel.py` + `streams_panel_harness.js` (16 checks).
- Slot 0 now carries 61 of 62 ranges (headroom 1) after the Fly-mode variables.
- Not done: slot swaps while armed; `/subscribe` has no arm gate of its own (the gate is in the streams service).

## Later

- Agent MCP tools for camera, markers and overlays. No flight commands.
