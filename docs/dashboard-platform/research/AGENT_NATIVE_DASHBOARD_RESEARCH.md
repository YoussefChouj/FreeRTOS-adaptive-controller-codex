# Agent-Native Dashboard Research
**Scope:** letting a local CLI agent (e.g. Claude Code) cooperate live with a single
operator of the drone ground-station dashboard, and analyse the recording afterwards.
**Target system in this repo:**
- Service: `ground_station/service/` — a Python stdlib `http.server`
  (`ThreadingHTTPServer` + `BaseHTTPRequestHandler`) on `127.0.0.1:8081`.
- Shell: vanilla-JS plugins in `docs/dashboard-platform/shell/`, no build step, no React.
- Agent affordances added in commit `56c3989`: opt-in recording
  (`GET /api/recording`, `POST /api/recording/start|stop`), session notes
  (`POST /api/session/note`, `GET /api/session/notes`), plus a `manifest.json` and
  `events.jsonl` written per recording (see `AGENT_GUIDE.md` and `SESSION_DATA.md`).
- The shell currently **polls** `/state` every 500 ms; there is no SSE or WebSocket
  route in `api.py` (verified by grep — none exists; `do_GET`/`do_POST` dispatch on
  `/state`, `/api/*`, `/plugins/*`, etc. only).
- **Safety:** powered drone. An agent may only PROPOSE arm / motor / parameter-write
  commands; a human click in the browser is always required for them.
This document was written from scratch, in line with the environment standing rules
for the `docs/dashboard-platform/research/` directory. It supersedes the previous
file in this location, which was surfaced for deletion and provided no usable source.
---
## 1. Executive Summary
**One recommended architecture for this repo:**
A *companion stdio MCP server* (`ground_station/agent/mcp_server.py`) that the CLI
agent already spawns as a child process, which reaches the dashboard **only through
the service's existing localhost HTTP API**, plus one new **SSE push route** in the
service to carry agent→operator UI events (proposals, highlights, step guides) to the
polling shell. Operator→agent traffic reuses the **session notes** channel
(`POST /api/session/note`, `GET /api/session/notes`). The shell adds a small vanilla-JS
"co-pilot" plugin: it opens an `EventSource` on the SSE route and renders a
confirmation-card + message panel + step-highlight overlay. The MCP tool layer is a
thin action registry over the existing endpoints.
Rationale, in three lines:
1. The stdlib `http.server` stays a single-purpose JSON REST + SSE host; the MCP
   transport and tool-calling live in the companion process where the SDK is a clear
   fit and non-h2-socket code is not mixed into the firmware-facing service.
2. The push mechanism to the browser is a plain `text/event-stream` route
   (`ThreadingHTTPServer` gives each SSE connection its own thread — required for SSE).
3. Relying on the already-existing session-notes channel and `events.jsonl` for the
   operator↔agent message log means no new persistence schema in Phase 1.
### 1.1 The two open questions, answered
**Q1 — Companion stdio MCP server, or an HTTP endpoint embedded in the service?**
Chosen: **companion stdio MCP server** (a separate process spawned by the agent over
`stdio`). Reasons:
- The external agent (Claude Code) is already an MCP *client*; `stdio` is its most
  mature transport and needs no port, no token, no CORS.
- The MCP server is a thin, stateless shim that forwards to the service REST API on
  `127.0.0.1:8081`. The service is the single source of truth and stays the only thing
  that touches the drone.
- Keeping MCP JSON-RPC out of `BaseHTTPRequestHandler` avoids reimplementing
  bidirectional JSON-RPC and its `tools/call` + notifications semantics in the stdlib
  server, which the Python docs explicitly warn is not for production.
- Elicitation / approval still works: the MCP server returns an `input_required`-style
  "proposal created, awaiting human" intermediate result instead of blocking.
An embedded **Streamable HTTP** MCP endpoint (`POST /mcp`) is a *Phase 3* option (the
current spec's `2026-07-28` Streamable HTTP shape is a single POST endpoint) if we later
want non-stdio agents; it is not needed for the single localhost operator.
**Q2 — Add SQLite?**
Chosen: **no new SQLite for Phase 1.** The service already uses an in-process
`SessionStore` (`:memory:`, per `SESSION_DATA.md`) and the standalone `CsvRecorder` +
`events.jsonl` are the durable exporter. Proposals, highlights and the operator
transcript are **ephemeral in-process state** in Phase 1 and land durably only as
`events.jsonl` rows once a recording is finalised. This matches the existing
"recording is opt-in, session lives in RAM" model and keeps the Windows-bounded
dependency set unchanged. Moving `SessionStore` to a file path is a noted follow-up,
not a Phase 1 requirement.
---
## 2. Status table (protocols & products)
One row per protocol/product. "Date" is the latest date/version cited on the referenced
source page as of 2026-09-22.
| Item | Status (2026-09-22) | URL | Date |
|---|---|---|---|
| MCP spec | Latest revision `2026-07-28` (RC-following release); stateless core, sessions/`initialize` removed | modelcontextprotocol.io/specification/2026-07-28 | 2026-07-28 |
| MCP Streamable HTTP | Current HTTP transport; single POST endpoint, replaces HTTP+SSE | modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http | 2026-07-28 |
| MCP Apps (UI extension) | Extension / SEP-1865; server-rendered HTML in sandboxed iframes; not a requirement here | blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps | 2025-11-21 |
| MCP Tasks extension / elicitation | Elicitation folded into Multi Round-Trip Requests; `input_required` results | modelcontextprotocol.io/specification/2026-07-28/changelog | 2026-07-28 |
| WebMCP (browser API) | Origin trial from Chrome 149; flag + polyfills; not needed for a localhost tool | developer.chrome.google.cn/docs/ai/webmcp | Chrome 149 |
| AG-UI | Event-based agent↔UI protocol; `RunStarted`/`RunFinished`/`RunError` run boundaries | docs.ag-ui.com/concepts/events | current |
| A2UI | Declarative, catalog-governed agent UI; v0.9.1 current, v1.0 candidate | a2ui.org | 2025-12 (v1.0 candidate) |
| MCP (21 Nov 2025) | Extension proposal SEP-1865, backward-compatible | blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps | 2025-11-21 |
| CopilotKit | React framework; frontend tools + HITL; too heavy for vanilla-JS shell | docs.copilotkit.ai | current |
| Vercel AI SDK | `toolApproval` policy HITL; SDK pattern, not a transport | ai-sdk.dev | AI SDK 7 |
| Grafana Assistant | Cloud SaaS assistant; context not matching a localhost offline tool | grafana.com/products/cloud/ai-assistant | current |
| Foxglove MCP server | Desktop local MCP server on `127.0.0.1:7333/mcp` with bearer token — closest analogue | docs.foxglove.dev/docs/agents/mcp-server | current |
| Chrome DevTools agents (`chrome-devtools-mcp`) | MCP + flags to attach to a live Chrome; debugging-oriented | developer.chrome.com/docs/devtools/agents | current |
| Playwright MCP | Snapshot/a11y-tree based browser automation for LLMs | playwright.dev/mcp/introduction | current |
| MCAP (mcap python) | Pure-Python writer/reader; optional LZ4/ZSTD compression deps | mcap.dev/docs/python/raw_reader_writer_example; pypi.org/project/mcap | current |
| SSE vs WebSocket | SSE is unidirectional over plain HTTP; native `EventSource`, no upgrade | dev.to/mindinu/stop-defaulting-to-websockets-why-server-sent-events-sse-are-usually-better | 2026 |
**Design take-away from the status table.** The browser push in Phase 1 is **plain SSE**
(not WebSocket): it is unidirectional server→shell, rides the already-threaded
`ThreadingHTTPServer`, needs no upgrade handshake and no extra dependency, and matches
the transport the MCP spec itself uses (SSE response streams) — so the same mental model
applies everywhere.
---
## 3. Phase 1 spec (implementation-ready)
This section is written to be executable by an implementation worker without further
questions. All routes, schemas and behaviours below are new; none exist in `api.py`
today (verified by grep — no SSE route, no `/api/agent/*` route).
### 3.1 Security boundary and risk model
Three risk levels drive every action and route:
- **`safe`** — the agent may execute directly with no confirmation: read-only state,
  tab switching, subscribing/unsubscribing read-only telemetry, notes, highlights.
- **`confirm`** — the agent may execute **only after** the operator clicks **Approve**
  on a browser confirmation card (non-flight effect on shared runtime state, e.g.
  start/stop recording, prefill a command).
- **`human-only`** — the agent does **not** execute at all. It only *proposes*:
  a Proposal object is written, pushed to the shell over SSE, and rendered as a card
  describing the exact commanded action. The operator must perform it **in the browser
  with a real click**. This is mandatory for arm, motor-spin and parameter-write.
Backing principle (from the standing rules and this repo's AGENT_GUIDE): nothing an
agent proposes can move a rotor or mutate firmware parameters without a human click.
### 3.2 New service routes
Every new route is keyed under `/api/agent/...` and registered in `_ROUTE_MAP` in
`api.py` (the `/api/routes` test enumerates it). Host remains `127.0.0.1:8081`.
| Method | Route | Purpose |
|---|---|---|
| GET | `/api/agent/state` | UI-state snapshot JSON (agent-facing; §3.3) |
| GET | `/api/agent/events` | SSE stream of agent→operator events to the shell |
| POST | `/api/agent/proposals` | Create a Proposal (called by the MCP server for `human-only`/`confirm`) |
| GET | `/api/agent/proposals` | List pending proposals, oldest first |
| GET | `/api/agent/proposals/<id>` | Fetch one proposal by id |
| POST | `/api/agent/proposals/<id>/accept` | Operator **Approved** → shell posts this |
| POST | `/api/agent/proposals/<id>/reject` | Operator **Declined** → shell posts this |
| POST | `/api/agent/highlight` | Set/clear the step-highlight overlay (agent→shell) |
| DELETE | `/api/agent/highlight` | Clear the highlight |
`GET /api/session/note`-family already exists and is reused verbatim for operator→agent
messages (see §3.6). `POST /api/recording/start|stop`, `GET /api/recording` exist and are
reused by the `start_recording`/`stop_recording` MCP tools and the `confirm` actions.
Response envelope (shared by all `/api/agent/*` route responses):
```json
{ "ok": true, "data": { ... } }
```
Errors use the service's existing style:
```json
{ "ok": false, "error": { "code": "proposal_not_found", "message": "no proposal with id 'p_9'" } }
```
### 3.3 UI-state snapshot JSON schema
`GET /api/agent/state` returns everything the agent needs to see what the operator
sees, without screenshots:
```json
{
  "schema": "ui-state/v1",
  "generated_at": "<ISO-8601>",
  "active_tab": "panel-system-overview",
  "tabs": [
    { "id": "panel-system-overview", "title": "System Overview", "visible": true, "order": 0 }
  ],
  "subscriptions": [
    { "slot": 1, "key": "status.arm", "fresh": true, "last_update_ns": 123 }
  ],
  "recording": {
    "recording": true,
    "session_dir": "logs/sessions/20260922-060001",
    "started_at": "<ISO-8601>",
    "rows": 1234,
    "reason": null,
    "enabled": true
  },
  "visible_panels": ["panel-system-overview"],
  "operator_view": { "url": "/", "viewport": { "w": 1280, "h": 800 } },
  "pending_proposals": [
    { "id": "p_9", "title": "Arm the aircraft", "risk": "human-only",
      "created_at": "<ISO-8601>", "state": "pending", "expires_at": "<ISO-8601>" }
  ],
  "active_highlight": null,
  "command_form": { "prefilled_command": null }
}
```
Rules:
- `pending_proposals` and `active_highlight` are the only fields that change as a
  direct result of agent actions; the rest maps onto data the shell already holds.
- **Honesty rule:** any field that is not currently known (e.g. a subscription whose
  slot is present but the key set is unknown) is rendered as `"unpublished": true`
  alongside the key; it is never invented. Absent values are `null`, never `0`.
- This endpoint is cheap (no `?stats=1` session scan) so an agent may poll it freely.
### 3.4 Action registry schema
The MCP server and the service share one JSON descriptor per action (kept in
`ground_station/agent/actions.py` as a string/JSON list, exported as
`GET /api/agent/actions` under `data.actions`). Each entry:
```json
{
  "name": "switch_tab",
  "risk": "safe",
  "summary": "Switch the active shell tab.",
  "params": { "tab_id": { "type": "string", "required": true,
                          "description": "panel slug, e.g. panel-system-overview" } },
  "route": { "method": "POST", "path": "/api/agent/actions/switch_tab" }
}
```
Initial action table (~16 actions):
| # | Action | Risk | Effect |
|---|---|---|---|
| 1 | `list_tabs` | safe | return the tab list from `/api/agent/state` |
| 2 | `switch_tab(tab_id)` | safe | change `active_tab`, push highlight to shell |
| 3 | `get_ui_state()` | safe | return §3.3 snapshot |
| 4 | `list_variables(pattern?)` | safe | search published telemetry keys |
| 5 | `subscribe_variable(slot, key)` | safe | add a read-only telemetry subscription |
| 6 | `unsubscribe_variable(slot, key)` | safe | remove a subscription |
| 7 | `subscribe_preview(slot, key)` | safe | validate a subscribe without applying (mirrors existing `/subscribe/preview`) |
| 8 | `start_recording(reason?, label?)` | confirm | opt-in recording; operator Approves |
| 9 | `stop_recording(reason?)` | confirm | finalise `manifest.json`; operator Approves |
| 10 | `post_note(text, kind?)` | safe | append an operator-visible note |
| 11 | `list_notes(limit?)` | safe | read buffered notes (`GET /api/session/notes`) |
| 12 | `highlight_step(step, guide_id?)` | safe | show a step-highlight overlay |
| 13 | `clear_highlight()` | safe | hide the overlay |
| 14 | `prefill_command(command_id, params)` | confirm | fill the command form; hidden until Approve, still gated by send |
| 15 | `propose(title, steps[], rationale, target)` | human-only | write a Proposal card for the operator |
| 16 | `analyze_session(session_dir)` | safe | read-only summary of `events.jsonl`/`telemetry.csv` |
`list_tabs`, `get_ui_state`, `list_variables`, `analyze_session` are pure reads.
`subscribe_*`, notes, highlights and tab switching are `safe` (no rotor, no write to
firmware). `start/stop_recording` and `prefill_command` are `confirm`. Only `propose`
covers the `human-only` surface: arm, motor-spin and parameter-write are expressed
through a `propose` call whose `target` names the exact command, and are **never**
executed by the agent.
### 3.5 Agent → operator proposal flow and the confirmation card
Flow for any `human-only` or `confirm` action:
1. The MCP server calls `POST /api/agent/proposals` with
   `{ "title", "risk", "steps": [...], "rationale", "target": {...}, "ttl_seconds": 120 }`.
2. The service stores it in the in-process proposal store with `id`, `created_at`,
   `expires_at = created_at + ttl_seconds`, `state = "pending"`, and broadcasts an
   SSE event `agent:proposal` to the shell.
3. The shell's co-pilot plugin renders a **confirmation card** fixed at the bottom-right
   corner. It shows, in order: title, the human-readable `steps[]`, the `rationale`,
   and the exact `target` command payload. It disables the primary button until the
   operator has read the steps (a simple "I've read the steps" checkbox; for
   `human-only arm/motor` the checkbox text is the mandatory safety sentence).
4. Buttons:
   - **Approve** → `POST /api/agent/proposals/<id>/accept`; for `confirm` actions the
     service then performs the action; for `human-only` it does **not** perform
     anything — it only logs `Proposal accepted by operator` and, where applicable,
     navigates the operator's focus to the exact control (e.g. the arm button) and
     highlights it, **leaving the final click to the operator**.
   - **Decline** → `POST /api/agent/proposals/<id>/reject`; logs and notifies the agent
     via the notes channel.
5. **Timeout & expiry.** Each proposal carries `expires_at`. If neither Approve nor
   Decline arrives before `expires_at`:
   - the shell's card is dismissed,
   - the service marks the proposal `state = "expired"` and broadcasts
     `agent:proposal_expired`,
   - the agent learns of the expiry on its next `list_notes` / `get_ui_state` poll
     (`pending_proposals` no longer includes the id). A `confirm` action is **not**
     executed on expiry. Default `ttl_seconds` = 120; `propose` for arm may lower it
     but never raises it above 300.
6. The TTL is enforced server-side (the shell merely renders the remaining seconds), so
   a closed tab cannot leave a proposal stuck `pending`.
### 3.6 Operator ↔ agent message channel (reuses session notes)
- **Agent → operator UI events:** the SSE stream (§3.7) carries `agent:proposal`,
  `agent:highlight`, `agent:step_guide`, `agent:message` event types.
- **Operator → agent:** the browser writes `POST /api/session/note` with
  `{ "text": "...", "kind": "note"|"goal"|"marker"|"reply", "source": "operator" }`;
  the agent reads them back via `GET /api/session/notes`. `kind:"reply"` is new but
  rides the existing buffered-notes path: buffered in-process, flushed into the next
  recording's `events.jsonl` as a `note` event (kind preserved in `data`).
- So there is **no new bidirectional socket**: operator↔agent dialogue is the existing
  notes mechanism; only the agent→operator push is new (SSE).
- The recording's `events.jsonl` already records `goal`/`note`/`marker` kinds, so a
  session's agent dialogue is part of the durable record automatically.
### 3.7 How agent actions reach the browser (the push mechanism)
`GET /api/agent/events` is `Content-Type: text/event-stream` on the
`ThreadingHTTPServer`. Each SSE connection runs in its own handler thread (this is why
the threaded server is required — see the Python docs quote in the evidence table). The
server keeps a small broadcaster list; on any agent→operator event it writes:
```
event: agent:proposal
data: {"id":"p_9","title":"Arm the aircraft","risk":"human-only",...}
event: agent:highlight
data: {"step_id":"s3","tab":"panel-system-overview","sel":"[data-testid=arm-btn]"}
```
A heartbeat comment line (`: ping\n\n`) is sent every 15 s to defeat idle timeouts.
`BrokenPipeError`/`ConnectionResetError` are caught on write to drop stale clients. The
shell opens exactly one `EventSource("/api/agent/events")` in the co-pilot plugin and
reconnects automatically (native behaviour). The existing 500 ms `/state` poll stays;
SSE only carries **agent-initiated** events, keeping churn low.
The MCP server never talks to the browser directly — every effect funnels through the
service's REST routes, which in turn emit SSE to the shell. This keeps a single
authoritative state and a single safety gate (the service).
### 3.8 MCP server tool list (stdio companion)
The companion server (`ground_station/agent/mcp_server.py`) advertises these tools
(`tools/list`). Names and input schemas:
| Tool | Input (JSON Schema) | Maps to |
|---|---|---|
| `get_ui_state` | `{}` | `GET /api/agent/state` |
| `switch_tab` | `{tab_id: string}` | `POST /api/agent/actions/switch_tab` |
| `list_tabs` | `{}` | `GET /api/agent/state` → `tabs` |
| `list_variables` | `{pattern?: string, limit?: integer}` | `GET /api/symbols` + `GET /api/manifest` |
| `subscribe_variable` | `{slot: integer, key: string}` | `POST /subscribe` (read-only) |
| `unsubscribe_variable` | `{slot: integer, key: string}` | `POST /subscribe` remove |
| `subscribe_preview` | `{slot: integer, key: string}` | `POST /subscribe/preview` |
| `recording_status` | `{}` | `GET /api/recording` |
| `start_recording` | `{reason?: string, label?: string}` | `POST /api/proposals` (confirm) |
| `stop_recording` | `{reason?: string}` | `POST /api/proposals` (confirm) |
| `post_note` | `{text: string, kind?: string}` | `POST /api/session/note` |
| `list_notes` | `{limit?: integer}` | `GET /api/session/notes` |
| `highlight_step` | `{step: string, tab?: string, guide_id?: string}` | `POST /api/agent/highlight` |
| `clear_highlight` | `{}` | `DELETE /api/agent/highlight` |
| `prefill_command` | `{command_id: string, params: object}` | `POST /api/proposals` (confirm) |
| `propose` | `{title: string, steps: string[], rationale: string, target: object, risk?: string}` | `POST /api/proposals` (human-only) |
| `analyze_session` | `{session_dir: string, kind?: string}` | read-only `events.jsonl`/`telemetry.csv` |
`tools/call` returns the service's `{ok, data|error}` body verbatim. For `confirm` and
`human-only` tools the result object is:
```json
{ "ok": true, "data": { "created": true, "proposal_id": "p_9",
  "state": "pending", "awaiting": "operator",
  "message": "Sent with risk=human-only; the operator must approve and click in the browser." } }
```
The MCP server config (`claude_desktop.json`/`~/.claude.json` style entry) names
`command: "<python> -m ground_station.agent.mcp_server"` with `transport: "stdio"` and,
optionally, an `MCP_GS_BASE_URL` env defaulting to `http://127.0.0.1:8081`.
### 3.9 Files to add or change
New:
- `ground_station/agent/__init__.py`
- `ground_station/agent/mcp_server.py` — stdio MCP server, `tools/list` + `tools/call`, HTTP forwarding.
- `ground_station/agent/actions.py` — the action registry (§3.4), exported as JSON.
- `ground_station/agent/proposals.py` — in-process proposal store + TTL logic.
- `docs/dashboard-platform/shell/plugins/agent-co-pilot.js` — EventSource, confirmation card, message panel, highlight overlay, step-guide view.
- `ground_station/service/tests/test_agent_routes.py` — pytest for the new routes.
- `ground_station/service/tests/test_agent_proposals.py` — pytest for proposal lifecycle/expiry.
- `ground_station/agent/tests/test_mcp_server_tools.py` — pytest for `tools/list`/`tools/call` against a mocked service.
- `ground_station/service/tests/agent_panel_harness.js` — node DOM harness for the co-pilot plugin.
Changed:
- `ground_station/service/api.py` — add the `/api/agent/*` routes, SSE handler, `_ROUTE_MAP` entries.
- `ground_station/service/core.py` — proposal store, SSE broadcaster, TTL sweeper.
- `docs/dashboard-platform/shell/index.html` — load the co-pilot plugin, reserve a footer slot.
- `docs/dashboard-platform/shell/plugin-api.md` — document the new `shellApi` surface and event stream.
- `.gitignore` already covers `logs/sessions/`; no change needed.
No new third-party dependency is required (SSE is hand-rolled; MCP is served over
`stdio` using only `json` and `http.client`, or the existing standard library).
### 3.10 Tests
pytest (run via `win.sh`, only the touched files):
- `test_agent_routes.py` — `GET /api/agent/state` shape; `POST /api/agent/proposals`
  returns an id; `GET /api/agent/proposals` ordering; `/api/agent/proposals/<id>/accept|reject`
  transitions; 404 for unknown id; `/api/routes` includes the new routes (the two share
  the `test_http_api_routes_endpoint` guard).
- `test_agent_proposals.py` — default TTL 120 s enforced server-side; expiry without
  accept/reject sets `state="expired"`; a `confirm` action is **not** executed on
  expiry; `human-only` proposal never triggers an arm/motor execute path.
- `test_mcp_server_tools.py` — `tools/list` has ≥16 tools; `get_ui_state`/`list_tabs`
  forward correctly against a stubbed `http.client`; a `propose` call returns
  `awaiting:"operator"`.
Node harness:
- `agent_panel_harness.js` — stubs `shellApi` (`getState`, `submitCommand`,
  `addEventListener` on the EventSource), loads `agent-co-pilot.js` in a sandboxed VM,
  asserts: a proposal SSE event renders a card; Approve posts to the right route; the
  card auto-dismisses on `expiry`; an arm proposal shows the safety sentence and a
  disabled action until read.
---
## 4. Phase 2 and Phase 3 (outlines)
**Phase 2 — analysis, replay and richer tooling.**
- Post-session analysis agent: open a finished recording, produce a structured
  summary (timeline of proposals, note dialogue, command lifecycle, stream stalls from
  `events.jsonl`; per-key statistics from `telemetry.csv`). New tool `analyze_session`
  becomes the primary interface.
- **MCAP export** besides CSV/JSONL: `mcap` (pure-Python writer) emits one channel per
  `(slot,key)` with the telemetry rows, written from the same `CsvRecorder` flush path;
  compression (LZ4/ZSTD) stays optional. Because the core `mcap` writer is pure Python,
  it is a legitimate small optional export without new heavy deps. Decision point for
  the operator: whether MCAP joins the default recording or stays an `export` action.
- Richer MCP: expose resources (`recording://<session>/events`, `recording://<session>/summary`),
  an optional `subscriptions/listen`-style long stream, and an `analyze` tool that
  replies with MRTR-style intermediate progress.
- Smarter step guides: multi-step runs with `step_guide` events and per-step
  preconditions; the agent verifies the operator completed each step via `/api/agent/state`.
**Phase 3 — standard interop and hardening.**
- Embed a **Streamable HTTP MCP endpoint** (`POST /mcp`) in the service with a
  generated bearer token, mirroring the Foxglove local-pattern
  (`127.0.0.1:7333/mcp`), to support non-stdio agents; keep the stdio companion as the
  primary path.
- Explore **WebMCP / AG-UI / A2UI** surfaces: A2UI's component **Catalog** discipline
  is the closest map to our `risk`-gated action registry and would let a future hosted
  A2UI renderer render proposal cards from a whitelist; **MCP Apps** if we ever want
  server-rendered panels, which we deliberately do not in Phase 1 (vanilla-JS shell).
- Harden: OAuth/DCR or CIMD-style client identity if the endpoint is ever remote;
  multi-agent admission (only one co-pilot at a time modifies tab/recording state).
---
## 5. Operator decisions needed
1. **stdio companion vs embedded HTTP endpoint** — this doc recommends the stdio
   companion for Phase 1; confirm.
2. **SQLite** — this doc recommends no new SQLite in Phase 1 (proposals/notes stay
   in-process, `events.jsonl` is the durable transcript). Confirm, or ask for a
   proposal store on disk.
3. **Recording as `confirm`, not `safe`** — start/stop recording posts a card that the
   operator must Approve. Confirm, or downgrade recording to `safe`.
4. **Default proposal TTL** (120 s, cap 300 s) and the exact safety sentence shown on an
   arm/motor card. Wordsmith once, then hard-code.
5. **Whether `analyze_session` may read any `logs/sessions/*` dir** or only dirs the
   operator has explicitly shared (scope of read for the after-session agent).
6. **MCAP export placement** (Phase 2): alongside CSV by default, or only on an
   explicit `export` action.
7. **Co-pilot on/off** default and whether the plugin is loaded for every shell render
   or gated behind a flag.
---
## 6. Evidence / Sources
Verified 2026-09-22. Each URL was fetched with WebFetch; the quote is a verbatim
sentence from the page body and supports the claim in its row. UNVERIFIED means we could
not confirm one of these on the live page.
| # | URL | Date checked | Claim it supports | Verbatim quote |
|---|---|---|---|---|
| 1 | https://modelcontextprotocol.io/specification/2026-07-28/changelog | 2026-09-22 | MCP 2026-07-28 made the core stateless and removed the `initialize` handshake | "Make MCP stateless: remove the `initialize`/`notifications/initialized` handshake." |
| 2 | https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http | 2026-09-22 | Streamable HTTP is the current HTTP transport, replacing HTTP+SSE | "Streamable HTTP was introduced in protocol version 2025-03-26 as a replacement for the HTTP+SSE transport from protocol version 2024-11-05." |
| 3 | https://blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps/ | 2026-09-22 | MCP Apps is an extension rendering HTML in sandboxed iframes | "The initial extension specification supports only `text/html` content, rendered in sandboxed iframes." |
| 4 | https://developer.chrome.google.cn/docs/ai/webmcp?hl=en | 2026-09-22 | WebMCP is shipped as an origin trial | "Join the WebMCP origin trial from Chrome 149." |
| 5 | https://docs.ag-ui.com/concepts/events | 2026-09-22 | AG-UI defines explicit run-boundary event types | "The `RunStarted` and either `RunFinished` or `RunError` events are mandatory, forming the boundaries of an agent run." |
| 6 | https://a2ui.org/introduction/what-is-a2ui/ | 2026-09-22 | A2UI is a declarative agent-driven UI protocol | "A2UI (Agent to UI) is a declarative UI protocol for agent-driven interfaces." |
| 7 | https://a2ui.org/specification/v1.0-a2ui/ | 2026-09-22 | A2UI restricts renderable components to a catalog | "The set of available UI components and functions is defined in a **Catalog**." |
| 8 | https://a2ui.org/concepts/actions/ | 2026-09-22 | A2UI is designed around sandboxed, safe client communication | "A2UI is designed with secure, sandboxed communication as a core principle." |
| 9 | https://docs.copilotkit.ai/agent-spec/human-in-the-loop | 2026-09-22 | CopilotKit lets an agent pause for user confirmation | "Human-in-the-loop (HITL) lets an agent pause mid-run to collect input, confirmation, or a choice from the user" |
| 10 | https://docs.copilotkit.ai/langgraph-typescript/frontend-tools | 2026-09-22 | CopilotKit frontend tools run client-side in the browser | "Frontend tools let your agent define and invoke client-side functions that run entirely in the user's browser." |
| 11 | https://ai-sdk.dev/v7/docs/agents/tool-approvals | 2026-09-22 | Vercel AI SDK has a `toolApproval` HITL policy | "Use `toolApproval` on `ToolLoopAgent` to review, approve, or deny selected tool calls before they execute." |
| 12 | https://grafana.com/products/cloud/ai-assistant/ | 2026-09-22 | Grafana Assistant is a natural-language observability assistant | "Grafana Assistant helps teams understand systems, investigate incidents, and take action using natural language." |
| 13 | https://docs.foxglove.dev/docs/agents/mcp-server | 2026-09-22 | Foxglove runs a local-only MCP server on the loopback | "The server only listens on your own machine (`http://127.0.0.1:7333/mcp`)." |
| 14 | https://docs.foxglove.dev/docs/agents | 2026-09-22 | Foxglove agents perform the same tasks a human can | "Agents can perform almost all of the tasks that humans can perform in Foxglove." |
| 15 | https://playwright.dev/mcp/introduction | 2026-09-22 | Playwright MCP uses accessibility snapshots, not vision models | "Enables LLMs to interact with web pages through structured accessibility snapshots — no vision models required." |
| 16 | https://developer.chrome.com/docs/devtools/agents | 2026-09-22 | Chrome DevTools agents attach to a live Chrome session | "Connect to your active Chrome session to inspect, pause, and troubleshoot in real-time." |
| 17 | https://mcap.dev/docs/python/raw_reader_writer_example | 2026-09-22 | MCAP has a Python writer API | "from mcap.writer import Writer" |
| 18 | https://pypi.org/project/mcap/ | 2026-09-22 | The `mcap` PyPI package is the Python library for MCAP | "MCAP libraries for Python" |
| 19 | https://docs.python.org/3.13/library/http.server.html | 2026-09-22 | `ThreadingHTTPServer` handles requests with threads (needed for SSE) | "This class is identical to HTTPServer but uses threads to handle requests by using the ThreadingMixIn." |
| 20 | https://docs.python.org/3.13/library/http.server.html | 2026-09-22 | the stdlib server is not for production (constrains where MCP lives) | "`http.server` is not recommended for production. It only implements basic security checks." |
| 21 | https://docs.langchain.com/oss/python/langgraph/interrupts | 2026-09-22 | LangGraph interrupt-based HITL needs a checkpointer | "A checkpointer to persist the graph state (use a durable checkpointer in production)" |
| 22 | https://dzone.com/articles/mcp-elicitation-human-in-the-loop-for-mcp-servers | 2026-09-22 | MCP elicitation is a standardized user-input mechanism | "MCP elicitation provides a standardized way for servers to request real-time user input through the client during a session." |
| 23 | https://dev.to/mindinu/stop-defaulting-to-websockets-why-server-sent-events-sse-are-usually-better-3k2g | 2026-09-22 | SSE is a one-way HTTP stream, suitable for dashboard push | "SSE is a unidirectional, HTTP-based stream." |
Note: rows #19 and #20 share one URL (the Python docs page) but support two distinct
claims; every other row is a distinct URL. All 23 rows were re-fetched with WebFetch on
2026-09-22 and the quoted sentence is present on the referenced page body. Nothing in
this table required a login, a cookie banner, or navigation text; no rows are marked
UNVERIFIED because all quotes were confirmed on the live pages.
---
## 7. Design notes that do not fit above
- **Why not WebMCP/AG-UI/A2UI for Phase 1.** WebMCP is an origin trial (Chrome 149)
  aimed at *sites* exposing tools to *browser-resident* agents; it adds value for public
  web pages, not a localhost tool whose agent is already a CLI process with file + HTTP
  access. AG-UI's schema (RunStarted/RunFinished boundaries) is useful as a *vocabulary*
  for our `agent:message` events but does not change the transport choice. A2UI's
  Catalog discipline is the closest conceptual match to our `risk` registry and is
  planned as a Phase 3 surface. None require reworking the vanilla-JS shell now.
- **Why the confirmation card is still needed even when the push is via SSE.** SSE is a
  one-way pipe; the Approve/Decline click must round-trip through the service REST API
  (`/api/agent/proposals/<id>/accept|reject`), and the TTL lives server-side so an idle
  tab cannot strand a proposal. The browser card is pure rendering plus two POSTs.
- **Concreteness for the after-session agent.** All state an agent needs to describe
  "what happened" is already durable in `events.jsonl` (command lifecycle
  SUBMITTED…VERIFIED, `arm_state`, `stream_stall`, notes/goals/markers) and
  `telemetry.csv`; the Phase 1 agent need build no extra recorder.
---
*End of document. This supersedes all prior content at
`docs/dashboard-platform/research/AGENT_NATIVE_DASHBOARD_RESEARCH.md`.*