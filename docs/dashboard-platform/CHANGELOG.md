# Changelog — Dashboard Platform Documentation

All notable documentation changes for `docs/dashboard-platform/`. This log tracks only doc files — it does not cover code changes to `ground_station/`, `firmware/`, or plugin `.js` implementations except where a doc file is created or rewritten.

---

## [Unreleased] — post-S14

### Added (2026-09-20)

- **AGENT_GUIDE.md** — agent entry point: `GET /api/routes`, UI `data-testid` scheme, `ground_station.service.browser_smoke`, live-service safety rules, arm gate / command timeout / slot freshness / `subscribe_slot` / replay-to-live semantics
- `GET /api/routes` endpoint (code) — machine-readable route + selector map
- Replay panel "Play to Live View" button (`data-testid="replay-play"`, `POST /replay/<id>/play`)

### Changed

- Default service port corrected to **8081** in ARCHITECTURE, COMMAND_SPEC, PLUGIN_DEVELOPER_GUIDE
- ARCHITECTURE Agent API reference points to `/api/routes` as source of truth
- reports/MORNING-2026-09-20.md: test counts and `subscribe_slot` status updated
- **AGENT_GUIDE.md** extended: record-route paging, the verified 10-tab/panel inventory, how to bring up an in-process `ApiServer` with `static_root`, and §7 on why stream-log cannot run beside the service
- **reports/FW-HUNT3.md** gained `## 4. Outcomes` — a per-finding verdict for all 14 findings (38–51), the build/flash/`livewatch verify` evidence, and the one divergence from the report (implemented string is `E:UART5 disabled`, not `E:UART5 transport disabled`, because the error buffer is tight)
- `shell/plugins/resource-map-panel.js`: stale header comment replaced — the S7 map is static from the firmware sources, live per-slot rates come from Refresh (`GET /api/view-model?stats=1`), RTOS stacks/heap belong to the RTOS Resources panel, and raw memory regions need a probe read

### Code changes worth a doc note (2026-09-20)

Outside this log's doc-only scope, but the docs above describe them, so they are recorded here for traceability:

- **Record routes page in SQL.** `GET /sessions/<id>/records` and `GET /replay/<id>` take `?limit=N&offset=N`, default 1000 rows, `limit=0` = unlimited, and answer with `count`/`offset`/`limit`/`truncated`. Before this, a live flight session answered `?limit=5` with **181 MB in 6.9 s** — the `limit` was being parsed by the shell, never by the server. `storage.iter_records(session_id, limit=None, offset=0)` now pushes it into SQL; `replay-panel.js` pages at 2000 rows. Three regression tests added.
- **`/api/view-model` is cheap again.** The full-session `session_stats` scan measured **24.0 s** on the live service; it now runs only under `?stats=1`, which just the Firmware Resource Map Refresh button asks for.
- **Stats render zero as zero.** Five view-model fields switched to `x if x is not None else None` instead of a truthiness test, so a legitimate `0` no longer renders as `—`.
- **Action journal ordering** is newest-first in `/api/view-model` and oldest-first at `GET /api/actions`, and that split is now documented rather than incidental.
- **`ground_station/service/browser_smoke.py`** — read-only Playwright walk of every tab (screenshots, console errors, HTTP >= 400, NaN/undefined text). It clicks only tabs and a replay session row, never a command, export or play button, so it is safe against a drone-connected service. Last run: 10 tabs, 0 errors, 0 bad responses.
- **Firmware FW-HUNT3 round flashed** (`Code=91060 RO-data=4304 RW-data=2544 ZI-data=121432`, 0 errors, `livewatch verify` 0 mismatches). Dashboard-visible consequence: a UART5 subscribe is now rejected with `E:UART5 disabled` instead of being ACKed into a silently-dropping slot.

---

## [S14] — September 17, 2026

### Added

- **README.md** — Top-level entry point with quick start, architecture overview, plugin list table, HTTP API summary, schema ID, file structure tree, adding-a-plugin guide, and reading order
- **INDEX.md** — Navigation index with all docs organized by category and session order
- **CHANGELOG.md** — This file
- **sessions/S13-documentation.md** — S13 session brief (was missing)
- **sessions/S14-implementation.md** — S14 session brief
- **reports/S13-documentation.md** — S13 detailed report with cross-reference graph, architecture diagrams, known limitations, and rollback procedure
- **reports/S14-implementation.md** — S14 detailed report with S13 gap resolution table, complete 14-plugin registry, cross-reference, rollback procedure

### Changed

- **STATE.md** — Status line updated to "All 14 sessions complete"; session summary table extended to S14; plugin registry expanded from 6 to 14 plugins; S14 gate result section added; known gaps section updated with S14 resolutions
- **sessions/S1-baseline.md** through **sessions/S12-integration.md** — Cross-reference "See also" links added pointing to reference docs (ARCHITECTURE, TELEMETRY_SPEC, COMMAND_SPEC, etc.)
- **shell/plugin-api.md** — Complete rewrite; promoted to canonical Shell API reference with full method documentation, TypeScript-like interface, ServiceState/StreamState JSON examples, error handling patterns (command rejection vs exception), versioning notes, and constraints

---

## [S13] — September 17, 2026

### Added

- **ARCHITECTURE.md** — System architecture document with: top-level block diagram, component responsibility table, full data flow (telemetry ingestion, command submission, state publication), ServiceState/StreamState schemas, plugin lifecycle states, session management with SQLite schema, experiment runtime state machine, complete Agent API reference (all REST endpoints + Python analysis module examples)
- **PLUGIN_DEVELOPER_GUIDE.md** — Plugin authoring guide with: minimal plugin structure, adding/removing plugins, lifecycle diagram, full Shell API reference, 3 worked examples (basic status, step buttons, SVG line chart), state subscription patterns (debounce, filter, loss, stale), canvas charting best practices, testing strategies with mock shellApi
- **TELEMETRY_SPEC.md** — Telemetry reference with: stream/slot mapping table (legacy + typed), channel index to physical meaning table for all 4 slots from live drone data, variable naming conventions table, update rate table with measured Hz, loss handling with thresholds/alarms, schema reference with full descriptor breakdown
- **COMMAND_SPEC.md** — Command reference with: full wire envelope (0xCC 0xDF), transaction lifecycle, all 30 commands (0x00–0x1E) with index tables, result code schemas (ACK/REJECTED/APPLIED), safety restrictions table, usage examples in JS/Python/bash

### Changed

- **STATE.md** — S13 gate result added; plugin registry section added with 6 plugins; cross-references to new docs
- **sessions/S9-dashboard-shell.md** — "See also" links added to new reference docs
- **sessions/S10-operational-plugins.md** — "See also" links added
- **sessions/S12-integration.md** — "Related sessions" updated to include S13; "What's next" updated

---

## Prior to S13

Documentation existed as session briefs and reports only. STATE.md and session report "See also" sections provided minimal cross-references.

Key files:
- `sessions/S1-baseline.md` through `sessions/S12-integration.md` — Session briefs
- `reports/S1-baseline.md` through `reports/S12-integration.md` — Detailed reports
- `REPORT_S13-commands-motors.md`, `REPORT_S13-plots-bandwidth.md`, `REPORT_S13-replay-path.md` — S13 panel implementation reports (at repo root; INDEX/CHANGELOG references here for historical reasons)
- `STATE.md` — Minimal state tracking
- `DASHBOARD_COMMANDS.md` — Legacy command panel UI notes
- `IMPLEMENTATION_PLAN.md` — Original 12-session plan
