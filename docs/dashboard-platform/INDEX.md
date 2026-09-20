# Dashboard Platform — Documentation index

Navigation guide for `docs/dashboard-platform/`. Start at [README.md](README.md) if this is your first time in the repo.

---

## Start here

| File | Purpose |
|------|---------|
| [README.md](README.md) | Top-level entry point — quick start, architecture overview, plugin list, HTTP API summary, file structure, adding a plugin guide |
| [AGENT_GUIDE.md](AGENT_GUIDE.md) | **Agents:** `/api/routes` discovery, `data-testid` selectors, browser smoke harness, safety rules (no POST to live), arm gate / timeout / slot freshness semantics |

---

## Architecture and reference

| File | Purpose |
|------|---------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design: data flow, plugin architecture, session management, experiment runtime, Agent API reference (all REST endpoints + Python analysis modules) |
| [TELEMETRY_SPEC.md](TELEMETRY_SPEC.md) | Telemetry reference: stream/slot mapping, channel index to physical meaning, naming conventions, update rates, loss handling, schema reference |
| [COMMAND_SPEC.md](COMMAND_SPEC.md) | Command reference: all 30 command IDs, index semantics, value ranges, result codes, safety restrictions |
| [shell/plugin-api.md](shell/plugin-api.md) | Canonical Shell API reference: all 5 methods, ServiceState/StreamState schemas, error handling, versioning |

---

## Plugin development

| File | Purpose |
|------|---------|
| [PLUGIN_DEVELOPER_GUIDE.md](PLUGIN_DEVELOPER_GUIDE.md) | Plugin authoring guide: format, lifecycle, API, 3 worked examples, state subscription patterns, canvas charting, testing |
| [shell/plugin-api.md](shell/plugin-api.md) | Low-level API reference (linked above) |

---

## Project state

| File | Purpose |
|------|---------|
| [STATE.md](STATE.md) | Project state: gate results, plugin registry, API surface, known gaps, decision log, hardware evidence |
| [INDEX.md](INDEX.md) | This file |

---

## Shell and plugins

| File | Purpose |
|------|---------|
| [shell/index.html](shell/index.html) | Browser shell — self-contained HTML/CSS/JS, 500ms polling loop |
| [shell/plugins/](shell/plugins/) | 14 plugin modules — see [README.md](README.md#plugin-list) for table |

---

## Sessions (briefs)

One-file briefs for each session, ordered chronologically. Each is a one-page statement of objective, deliverables, and verification checklist.

| File | Session |
|------|---------|
| [sessions/S1-baseline.md](sessions/S1-baseline.md) | S1 — Baseline and contract audit |
| [sessions/S2-registry.md](sessions/S2-registry.md) | S2 — Identity, registry, discovery |
| [sessions/S3-command-events.md](sessions/S3-command-events.md) | S3 — Transactional commands |
| [sessions/S4-telemetry.md](sessions/S4-telemetry.md) | S4 — Typed telemetry |
| [sessions/S5-authority-plugins.md](sessions/S5-authority-plugins.md) | S5 — Authority plugins |
| [sessions/S6-experiments.md](sessions/S6-experiments.md) | S6 — Experiments runtime |
| [sessions/S7-resources.md](sessions/S7-resources.md) | S7 — Resource map |
| [sessions/S8-ground-service.md](sessions/S8-ground-service.md) | S8 — Ground-station service |
| [sessions/S9-dashboard-shell.md](sessions/S9-dashboard-shell.md) | S9 — Dashboard shell |
| [sessions/S10-operational-plugins.md](sessions/S10-operational-plugins.md) | S10 — Operational plugins |
| [sessions/S11-analysis-agents.md](sessions/S11-analysis-agents.md) | S11 — Analysis API |
| [sessions/S12-integration.md](sessions/S12-integration.md) | S12 — Integration and release |
| [sessions/S13-documentation.md](sessions/S13-documentation.md) | S13 — Documentation enrichment |
| [sessions/S14-implementation.md](sessions/S14-implementation.md) | S14 — Command panel + plugin improvements |

---

## Reports (detailed)

Detailed per-session reports covering changes, evidence, tests, and outcomes. The `reports/` directory has a local [README](reports/README.md) as well.

| File | Session |
|------|---------|
| [reports/S1-baseline.md](reports/S1-baseline.md) | S1 — Baseline audit |
| [reports/S2-registry.md](reports/S2-registry.md) | S2 — Registry foundation |
| [reports/S3-command-events.md](reports/S3-command-events.md) | S3 — Command protocol |
| [reports/S4-telemetry.md](reports/S4-telemetry.md) | S4 — Telemetry validation |
| [reports/S5-authority-plugins.md](reports/S5-authority-plugins.md) | S5 — Authority management |
| [reports/S6-experiments.md](reports/S6-experiments.md) | S6 — Experiment runtime |
| [reports/S7-resources.md](reports/S7-resources.md) | S7 — Resource mapping |
| [reports/S8-ground-service.md](reports/S8-ground-service.md) | S8 — Service implementation |
| [reports/S9-dashboard-shell.md](reports/S9-dashboard-shell.md) | S9 — Shell and plugin API |
| [reports/S10-operational-plugins.md](reports/S10-operational-plugins.md) | S10 — 6 operational plugins |
| [reports/S11-analysis-agents.md](reports/S11-analysis-agents.md) | S11 — Analysis API |
| [reports/S12-integration.md](reports/S12-integration.md) | S12 — Integration and release |
| [reports/S13-documentation.md](reports/S13-documentation.md) | S13 — 4 reference docs |
| [REPORT_S13-commands-motors.md](REPORT_S13-commands-motors.md) | S13 — Experiment and motor bench panels |
| [REPORT_S13-plots-bandwidth.md](REPORT_S13-plots-bandwidth.md) | S13 — Time-series, FFT, bandwidth panels |
| [REPORT_S13-replay-path.md](REPORT_S13-replay-path.md) | S13 — Replay and path panels |
| [reports/S14-implementation.md](reports/S14-implementation.md) | S14 — Command panel + plugin improvements |
| [reports/S14-commands.md](reports/S14-commands.md) | S14 — Detailed command panel report |

---

## Change history

| File | Purpose |
|------|---------|
| [CHANGELOG.md](CHANGELOG.md) | Doc change history — S13 → S14 additions |

---

## Other docs

| File | Purpose |
|------|---------|
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Original 12-session implementation plan |
| [RESEARCH.md](RESEARCH.md) | Design research notes |
| [DASHBOARD_COMMANDS.md](DASHBOARD_COMMANDS.md) | Command panel UI notes (legacy — see `command-panel.js` for current implementation) |
| [SESSION_TEMPLATE.md](SESSION_TEMPLATE.md) | Template for session briefs |
