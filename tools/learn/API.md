# LearnHarness CLI & API Reference (`tools/learn`)

`tools/learn/learn.py` is the unified Python 3.10+ standard-library CLI and HTTP API server for the learning harness. It maintains 100% on-disk format compatibility with `learnharness-ref/docs/data-format.md` and the vendored inspectors (`tools/learn/vendor/{_data,graph,sessions}.py`), while extending the store for Claude Code + `generative_ui` workflows, internship tracking, and Argon helper jobs.

---

## 1. On-Disk Layout (`docs/learning/harness/data/`)

All files below match the upstream `learnharness-ref/docs/data-format.md` layout and are readable directly by `tools/learn/vendor/graph.py` and `tools/learn/vendor/sessions.py`:

| Path | Format | Purpose |
|---|---|---|
| `settings.json` | JSON object | Global harness settings (theme, `helper_worker` routing). |
| `graph/format.json` | JSON object | Schema version marker (`{"version": 1}`). |
| `graph/nodes.jsonl` | Append-only JSONL | Proposition ledger (`op: "add"` with `id`, `name`, `statement`, `createdAt`, `provenance`, or `op: "retract"` / `"amend"`). |
| `graph/chains.jsonl` | Append-only JSONL | Directed hyperedge ledger (`op: "add"` with `id`, `premises[]`, `conclusion`, `createdAt`, `provenance`). |
| `graph/coverage.jsonl` | Append-only JSONL | Folded extraction & practice attempt markers (`chatId`, `fp`, `tokens`, `foldedAt`, plus practice fields). |
| `graph/sessions.jsonl` | Append-only JSONL | Session birth events (`op: "create"`, `sessionId`, `kind`, `nodeIds[]`, `createdAt`). |
| `graph/sources.json` | JSON object | Per-node and per-chain source provenance map (`{ "<id>": [{ "kind": "chat", "sessionId": "...", "timestamp": "..." }] }`). |
| `graph/positions.json` | JSON object | Saved viewer 2D layout coordinates (`{ "positions": { "<nodeId>": { "x": 0.0, "y": 0.0 } } }`). |
| `graph/render-settings.json` | JSON object | Graph canvas visual settings (repulsion, spring length, damping, labels). |
| `chats/<chat_id>.json` | JSON object | Full session record (`id`, `title`, `mode`, `createdAt`, `updatedAt`, `messages[]`, `tree`, `cards[]`). |
| `jobs/<job_id>.json` | JSON object | Helper subagent job record (`id`, `kind`, `chatId`, `toolCallId`, `status`, `activity`, `worker`, `brief`, `output`, `result`). |

---

## 2. Additions Beyond `data-format.md`

To support interactive `generative_ui` cards, dependency plans, internship progress synchronization, and Argon subagent dispatch without breaking upstream tools, `learn.py` adds the following fields and directories:

1. **Session Metadata (`chats/<chat_id>.json`)**:
   - `topic` (`str`): Human-readable topic title.
   - `goal` (`str`): Concrete session learning objective.
   - `track` (`"internship" | "thesis" | "general"`): Track tag for filtering and internship breakpoint updates.
   - `status` (`"active" | "closed"`): Session lifecycle state (`closedAt` set when closed).
   - `links` (`list[str]`): Bidirectional cross-session links (`session link A B`).
2. **Dependency Plan (`chats/<chat_id>.json -> tree`)**:
   - Stores both upstream-compatible `rootId` / `nodes` map and ordered `steps` (`[{ "id", "title", "deps": [], "status": "done"|"current"|"todo", "notes" }]`), `edges` (`[{ "from", "to" }]`), and `currentStepId`.
3. **Structured Card Events (`chats/<chat_id>.json -> cards[]`)**:
   - Every `learn.py log` call appends a structured card entry (`id`, `kind`, `step`, `confidence`, `score`, `feedback`, `payload`, `raw`, `timestamp`) alongside the standard `messages[]` user/assistant transcript turns.
4. **Practice Ledger & Coverage Fields (`practice_attempts.jsonl` & `graph/coverage.jsonl`)**:
   - `practice_attempts.jsonl` records every practice outcome (`id`, `sessionId`, `problemId`, `result`, `hints`, `nodes[]`, `timestamp`).
   - `graph/coverage.jsonl` entries include `attemptId`, `problemId`, `result` (`"correct" | "partial" | "wrong"`), `hints`, `nodes[]`, and `nodeId` in addition to `chatId`, `fp`, `tokens`, `foldedAt`.
   - Merged nodes in `/api/graph` compute `coverage` (`"covered" | "shaky" | "unseen"`), `depth`, `tracks`, and `sessionIds`.
5. **Problem Catalog (`problems/*.json`)**:
   - Curated and generated practice problems with explicit difficulty rule fields: `number_of_ideas_combined`, `cue_unfamiliarity`, and `difficulty = number_of_ideas_combined + cue_unfamiliarity`.
6. **Interactive Demos (`demos/*.html`)**:
   - Self-contained HTML/Canvas/SVG simulations served at `/demos/<name>.html` and `/api/demo/<id>`.
7. **Helper Worker Routing (`settings.json -> helper_worker`)**:
   - Maps job kinds (`default`, `research`, `pdf`, `visual`, `demo`) to `"inline"` or `"argon"`. When `"argon"` is active, `job new` writes a brief to `.agent-ops/briefs/learn-job-<id>.md` and prints the exact `.agent-ops/argon.ps1 -Id learn-<id> -Brief <path>` invocation without executing it.

---

## 3. CLI Subcommands and Flags

Global flags (accepted before or after any subcommand):
- `--data-dir <path>`: Override data directory (default: `docs/learning/harness/data`, or `LH_DATA_DIR`).
- `--json`: Emit machine-readable JSON output.
- `--full`: Disable the default `1500`-byte compact output cap.
- `--briefs-dir <path>`: Override directory for Argon briefs (default: `.agent-ops/briefs`).
- `--internship-progress <path>`: Override path to `docs/learning/flight-control-internship/progress.json`.

### Subcommands

- **Session lifecycle**
  - `python tools/learn/learn.py session new --topic "<topic>" --goal "<goal>" [--track internship|thesis|general] [--link <id>...] [--title "<title>"] [--id <id>]`
  - `python tools/learn/learn.py session list [--track <track>] [--limit <n>]`
  - `python tools/learn/learn.py session show <id>`
  - `python tools/learn/learn.py session link <id> <other_id>`
  - `python tools/learn/learn.py session close <id>`
  - `python tools/learn/learn.py continue [<id>]` — prints a `< 1500`-byte resume pack (session goal, current step, last card summary, linked sessions, shaky/unseen nodes).
- **Dependency plan**
  - `python tools/learn/learn.py plan set <id> --file <plan.json>`
  - `python tools/learn/learn.py plan advance <id>`
  - `python tools/learn/learn.py plan show <id>`
  - `python tools/learn/learn.py plan mermaid <id>`
- **Card logging & proposition extraction**
  - `python tools/learn/learn.py log <id> --kind predict|reading|check|probe|practice|note --step <step_id> --payload-file <file> [--score <0..1>] [--feedback <text>]`
  - `python tools/learn/learn.py extract <id> --file <nodes_and_chains.json>`
- **Search, probe & adaptive practice**
  - `python tools/learn/learn.py search "<query>" [--in chats,nodes,plans,problems] [--limit 15]`
  - `python tools/learn/learn.py probe seed <id>`
  - `python tools/learn/learn.py practice next [<id>] [--nodes <n1> <n2>...]`
  - `python tools/learn/learn.py practice record <id> --problem <pid> --result correct|partial|wrong [--hints <k>] [--nodes <n1>...]`
- **Helper jobs & utilities**
  - `python tools/learn/learn.py job new --kind research|pdf|visual|demo --brief-file <file> [--session <id>] [--worker inline|argon] [--title "<title>"]`
  - `python tools/learn/learn.py job list`
  - `python tools/learn/learn.py job show <job_id>`
  - `python tools/learn/learn.py job done <job_id> --output <path>`
  - `python tools/learn/learn.py import-internship [--internship-dir <path>]`
  - `python tools/learn/learn.py selftest`
  - `python tools/learn/learn.py serve [--host 127.0.0.1] [--port 8770]`

---

## 4. HTTP Routes (`learn.py serve --port 8770`)

| Method & Route | Query / Body Parameters | Response JSON Schema |
|---|---|---|
| `GET /` | — | Serves `tools/learn/viewer/index.html` (and static assets `/app.js`, `/style.css`, `/demos/<file>.html`). |
| `GET /api/sessions` | — | `[{ "id", "title", "topic", "goal", "track", "status", "currentStep", "stepProgress", "links", "createdAt", "updatedAt" }]` |
| `GET /api/session/<id>` | — | Full session object from `chats/<id>.json` plus `currentStepIndex`, `currentStep`, `steps`. |
| `GET /api/plan/<id>` | — | `{ "sessionId", "topic", "goal", "updatedAt", "currentStepIndex", "currentStep", "steps": [...], "edges": [...], "mermaid": "flowchart TD..." }` |
| `GET /api/graph` | `?session=<id>&track=<track>` | `{ "nodes": [...], "chains": [...], "positions": {...}, "renderSettings": {...}, "sources": {...}, "sessions": [...] }` |
| `POST /api/graph` (or `/api/graph/positions`) | `{ "positions": { "<nodeId>": { "x", "y" } } }` | `{ "ok": true, "positions": {...} }` (persists coordinates to `graph/positions.json`). |
| `GET /api/search` | `?q=<query>&in=chats,nodes,plans,problems&limit=20` | `{ "query": "<q>", "hits": [{ "kind", "id", "sessionId", "title", "snippet", "score" }] }` |
| `GET /api/practice/<id>` (or `/api/practice`) | `<id>` = session ID or `"all"` | `{ "sessionId", "attempts": [...], "problems": [...] }` |
| `GET /api/jobs` | — | `{ "jobs": [...] }` |
| `GET /api/demo/<id>` (or `/api/demos`) | `<id>` = demo stem, job ID, or `"default"` | `{ "id", "title", "jobId", "path", "url", "html" }` |
