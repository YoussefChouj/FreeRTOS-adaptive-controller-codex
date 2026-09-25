# Task <id>: <imperative title>

<!-- Model routing:
- agy:gemini-3.1-pro-high for design/hard debugging;
- agy:gemini-3.8-flash-high default implementation;
- agy:gemini-3.8-flash-low lookups;
- qwen mechanical edits; free last resort;
- typical fallback chain `agy:gemini-3.8-flash-high,qwen`.
-->

## Goal
<One sentence stating an observable done-condition: what must be true when complete.>

## Context pointers
- Target files/symbols: `<path/to/file>:<symbol_or_line>` (re-read before editing)
- Firmware symbols: run `python -m ground_station.agent_map explain <symbol>` first
- Docs/references: `<path/to/spec_or_log>`

## Constraints
- Safety tier: <Tier 0 (flight-critical, requires permission) | Tier 1 | Tier 2>
- Do not touch: firmware (`API/`, `TASK/`, `BSP/`, `USER/`, `Global_file/`), `OBJ/`
- Hardware: no flashing, no probe/reset/halt, do not touch UDP 14550 or port 8081
- Execution: foreground commands only (never background); do not commit

## Deliverables
1. <Code changes in target files>
2. <Offline tests covering happy and failure paths with fake sockets/fixtures>
3. `.agent-ops/out/<id>.md` (digest <= 30 lines: STATUS, files, verify, risks)

## Verification
- Static check: `<command>` (e.g. `python -m py_compile ...` or `bash -n ...`)
- Unit tests: `python -m pytest <test_path> -q` -> expected: <pass count, 0 fail>
- Grep check: `grep -n "<pattern>" <file>` -> expected: <target lines>

## Out of scope
- Everything else, unlisted files, and live hardware/probe interactions.
