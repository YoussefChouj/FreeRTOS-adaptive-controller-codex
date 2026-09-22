# Task: Agent Map step 1 — generator + explain CLI

Read first: `docs/dashboard-platform/AGENT_MAP_SPEC.md` (section "Step 1") and the
"Code navigation protocol" in STANDING-RULES. Build exactly step 1, nothing from steps 2-4.

Write access: `ground_station/agent_map/` (new package), its tests under
`ground_station/agent_map/tests/`, `docs/agent-map/modules.yaml` (DRAFT header, copy the draft
tier table from the spec; mark `confirmed: false`), and one line in `.gitignore` for
`docs/agent-map/agent_map.json`. Everything else is read-only. Firmware is read-only.

Starting points (read, do not modify):
- DWARF: `ground_station/livewatch/symbols.py` (existing resolver over `OBJ/JX_FLY.axf`).
- Streamable vars: `ground_station/livewatch/multi_slot_presets.yaml`.
- Panels/telemetry manifest: `ground_station/service/core.py` `_manifest_context` and the files
  it reads — load those FILES directly. Never start or call the service on 8081.
- Glossary: `docs/glossary.md`.

Requirements:
- `python -m ground_station.agent_map build` writes `docs/agent-map/agent_map.json`, prints
  `N symbols, D with dwarf, S streamable, P with panels`.
- `python -m ground_station.agent_map explain <name>` prints <= 25 lines: definition file:line,
  module, tier, dwarf type/address, writers (file:line list, regex over firmware C for
  `<name>\s*(\[.*\])?(\.\w+)*\s*[+\-*/|&]?=`), streamable preset, panels, glossary line.
  Unknown name -> nearest 5 names by difflib, exit 1.
- Firmware symbol list: functions and globals from firmware C (regex/ctags-style is fine),
  joined to DWARF by name. Exclude stm32_lib/ and FreeRTOS/.
- Stdlib + deps already in the repo only. Python 3.11.
- Tests: synthetic fixture tests + one real-ELF test skipped when `OBJ/JX_FLY.axf` is absent.

Verify and paste raw output in the summary: pytest result line; the `build` line; and
`explain` for `gyroxPID`, `s_ekf`, and one misspelled name. Do not commit.
