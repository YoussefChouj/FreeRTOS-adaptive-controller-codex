# Learn Harness (`/learn`) — Build & Acceptance Report

- **Branch**: `learn-harness` (branched from `main` at `4bd2d1a0`)
- **Spec**: `.agent-ops/briefs/learn-harness.md`
- **Reference Repo**: `https://github.com/amosblomqvist/learnharness` (`../learnharness-ref`, outside repo, uncommitted) & installed app `C:\Users\Acer\AppData\Local\Programs\learnharness\LearnHarness.exe`

---

## 1. Acceptance Tests Table

| # | Acceptance Criterion | Status | Command Executed | Real Output Excerpt |
|---|---|---|---|---|
| **1** | `pytest tools/learn/tests -q` passes; `python tools/learn/learn.py selftest` exits 0 | **PASS** | `pytest tools/learn/tests -q && python tools/learn/learn.py selftest` | `10 passed in 4.35s` / `SELFTEST OK` (exit code `0`) |
| **2** | Format parity: vendored `graph.py` and `sessions.py` run against `docs/learning/harness/data` and a temp copy of golden-data fixture, exit 0, same kind of output on both | **PASS** | `python tools/learn/vendor/graph.py --data-dir docs/learning/harness/data overview && python tools/learn/vendor/sessions.py --data-dir docs/learning/harness/data list` (+ `pytest -k test_2_format_parity`) | `# Overview` ... `## L01 · How a quadrotor flies (四旋翼怎么飞) (chat_internship_l01) - Kind: chat - Birth propositions: 6` / `# Sessions` ... `Skipped/unreadable sessions: 0` (matches golden-data Markdown structure; `test_2_format_parity PASSED`) |
| **3** | Round trip in a temp data dir: `session new` -> `plan set` (5 steps) -> `log` predict + reading + check -> `plan advance` -> `extract` 3 nodes + 2 chains -> `search integral` finds session and node -> `continue` prints current step 2, last card, and stays `< 1.5 KB` | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_3_round_trip -v` | `test_3_round_trip PASSED` — verified `search integral` hits both `chat` and `node`, `continue` reports `[2/5]` (`step_2`, `currentStep: step_2`), includes last `check` card event, and byte length is `< 1500` bytes (`~690` bytes) |
| **4** | `session link A B`: both `session show` outputs and `/api/session/<id>` list the link | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_4_session_link_bidirectional -v` | `test_4_session_link_bidirectional PASSED` — both `session show <A>` and `session show <B>` output the counterpart ID, and `GET /api/session/<A>` and `GET /api/session/<B>` include the counterpart in `session.links` and `session.linkedSessions` |
| **5** | Viewer: with `serve` running, every API route returns 200 and valid JSON; plan page reflects `plan advance` within 3 s | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_5_viewer_http_server -v` | `test_5_viewer_http_server PASSED` — verified HTTP 200 + valid JSON on `/api/sessions`, `/api/session/<id>`, `/api/plan/<id>`, `/api/graph`, `POST /api/graph`, `/api/search?q=integral`, `/api/practice/<id>`, `/api/practice/all`, `/api/jobs`, `/api/demo/<id>`; `plan advance` reflected in `/api/plan/<id>` in `< 0.1 s` (viewer polls every `1.5 s` < `3 s`) |
| **6** | Cards: every card in `cards/test.html` renders, slider runs `0-100`, Submit produces a payload that a test parses back into fields; each card file `< 6 KB` | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_6_cards_and_payload_parser -v` | `test_6_cards_and_payload_parser PASSED` — `predict.html` (`4,594 B`), `reading.html` (`4,738 B`), `check.html` (`5,288 B`), `probe.html` (`5,619 B`), `practice.html` (`5,786 B`) are all `< 6,144 B`, fragment-clean, have `0-100` sliders, and `learn.parse_card_payload()` round-trips all fields |
| **7** | Practice: `practice next` returns a problem whose difficulty follows the rule (`ideas_combined + cue_unfamiliarity`); `practice record` updates `coverage.jsonl` and (`track=internship`) `progress.json` breakpoint in a temp copy | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_7_practice_difficulty_and_internship_progress -v` | `test_7_practice_difficulty_and_internship_progress PASSED` — verified `difficulty == number_of_ideas_combined + cue_unfamiliarity`, `coverage.jsonl` updated with `result="correct"`, and temp `progress.json` breakpoint updated to `status="retested"` with `last_practice` record |
| **8** | Jobs: `job new --kind demo` with `helper_worker=argon` writes the brief and prints the Argon command without running it | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_8_job_new_argon_dispatch -v` | `test_8_job_new_argon_dispatch PASSED` — wrote `.agent-ops/briefs/learn-job-<id>.md` (in temp `--briefs-dir`) and printed `.agent-ops/argon.ps1 -Id learn-<id> -Brief <path>` without invoking PowerShell |
| **9** | `import-internship` run twice gives the same data (idempotent) | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_9_import_internship_idempotent -v` | `test_9_import_internship_idempotent PASSED` — full directory snapshot after 1st and 2nd run of `import-internship` is byte-identical (`3` sessions, `7` nodes, `4` chains) |
| **10** | `docs/learning/harness/PARITY.md`: every feature from transcript, prompts, and UI screens is a row with implementation and test; nothing marked `"dropped"` | **PASS** | `pytest tools/learn/tests/test_learn.py -k test_10_parity_matrix -v` | `test_10_parity_matrix PASSED` — `docs/learning/harness/PARITY.md` contains `60+` rows covering the 22-min walkthrough transcript, all 18 prompt files, 5 subagents, agent tools, and UI screens; `0` rows marked `"dropped"` |

---

## 2. Commits on Branch `learn-harness`

1. `65778f56` — `learn(wp1): add learn.py CLI, HTTP API server, vendored scripts, API doc, and tests`
2. `f4b966e2` — `learn(wp2): add local viewer web app on port 8770 and claude launch.json config`
3. `454fb56c` — `learn(wp4): add helper job templates, step-response demo, custom problems, imported internship data, and PARITY.md`
4. `3177a054` — `learn(wp3): track .claude/launch.json, /learn skill, HTML cards, and job brief templates`

---

## 3. Files Added

### WP1 (`tools/learn/`)
- `tools/learn/__init__.py`
- `tools/learn/learn.py` — Stdlib-only Python 3 CLI (`session`, `continue`, `plan`, `log`, `extract`, `search`, `probe`, `practice`, `job`, `import-internship`, `selftest`, `serve`) + HTTP JSON/static server on port `8770` + `parse_card_payload()`.
- `tools/learn/API.md` — Documentation of the on-disk schema, schema extensions beyond upstream `data-format.md`, CLI subcommands, and HTTP JSON API.
- `tools/learn/vendor/__init__.py`
- `tools/learn/vendor/_data.py` — Vendored from `amosblomqvist/learnharness: skills/learnharness/scripts/_data.py` (`AGPL-3.0`).
- `tools/learn/vendor/graph.py` — Vendored from `amosblomqvist/learnharness: skills/learnharness/scripts/graph.py` (`AGPL-3.0`).
- `tools/learn/vendor/sessions.py` — Vendored from `amosblomqvist/learnharness: skills/learnharness/scripts/sessions.py` (`AGPL-3.0`).
- `tools/learn/tests/test_learn.py` — 10-test pytest suite covering all acceptance criteria in isolated `tmp_path` directories.

### WP2 (`tools/learn/viewer/` & `.claude/launch.json`)
- `tools/learn/viewer/index.html` — 6-tab viewer SPA (`#sessions`, `#session`, `#plan`, `#graph`, `#practice`, `#jobs`), dark/light theme support, responsive down to `375 px`.
- `tools/learn/viewer/style.css` — LearnHarness dark theme (`#141414` canvas, `#1c1c1c` raised cards, `#434343` borders), 6-step confidence rail color ramp, coverage badges, and 375 px mobile layout rules.
- `tools/learn/viewer/app.js` — Interactive SVG dependency plan DAG with `1.5 s` live auto-refresh poller, interactive SVG proposition graph with depth layout / dashed cross-session links / coverage colors / provenance drawer / position saving (`POST /api/graph/positions`) / Chain Table view, session transcript viewer, practice stream viewer, and sandboxed demo iframe previewer.
- `.claude/launch.json` — `learn-viewer` entry running `python tools/learn/learn.py serve --port 8770` on port `8770`.

### WP3 (`.claude/skills/learn/`)
- `.claude/skills/learn/SKILL.md` — 149-line teacher skill driving Claude Code desktop with `/learn` commands, probe/plan/teach/practice/demo doctrines citing upstream prompts, Chinese+pinyin internship style, token hygiene rules, and `[learn]` card payload spec.
- `.claude/skills/learn/cards/predict.html` (`4,594 B`) — Prediction widget fragment with `0–100` confidence slider and `sendPrompt()`.
- `.claude/skills/learn/cards/reading.html` (`4,738 B`) — "Thinking while reading" widget fragment with `0–100` confidence slider and `sendPrompt()`.
- `.claude/skills/learn/cards/check.html` (`5,288 B`) — 3-thinking-field + 2-question check widget fragment with `0–100` confidence slider and `sendPrompt()`.
- `.claude/skills/learn/cards/probe.html` (`5,619 B`) — Multiple-choice (`A..D` + `Pass`) diagnostic probe widget fragment with `0–100` confidence slider and `sendPrompt()`.
- `.claude/skills/learn/cards/practice.html` (`5,786 B`) — Adaptive practice widget fragment with node/difficulty badges, chunked thinking, Hint counter button (`kind=hint`), and Submit button (`kind=practice`).
- `.claude/skills/learn/cards/test.html` (`8,499 B`) — Standalone browser harness loading all 5 cards with sample placeholders, stub `window.sendPrompt`, and payload parser inspector.

### WP4 (`.claude/skills/learn/jobs/` & `docs/learning/harness/`)
- `.claude/skills/learn/jobs/research.md` — Research digest job brief template.
- `.claude/skills/learn/jobs/pdf.md` — Size-adaptive PDF reader job brief template.
- `.claude/skills/learn/jobs/visual.md` — SVG diagram visual-maker job brief template.
- `.claude/skills/learn/jobs/demo.md` — Interactive HTML demo-builder job brief template.
- `docs/learning/harness/PARITY.md` — Complete feature parity matrix across walkthrough transcript, prompts, subagents, tools, and UI screens.
- `docs/learning/harness/data/demos/demo_step_response.html` — Self-contained interactive HTML5 Canvas demo of a First-Order & Cascade Step Response ($G(s) = K / (\tau s + 1)$) with Gain $K$, Time Constant $\tau$, Load Disturbance $d$, and Integral $K_i$ sliders.
- `docs/learning/harness/data/problems/README.md` — Custom problem file schema documentation (JSON and Markdown).
- `docs/learning/harness/data/problems/control_basics.json` — 4 graded UAV/control practice problems tagged with proposition node IDs.
- `docs/learning/harness/data/settings.json` — Harness settings including `helper_worker` (`argon` for `research`/`demo`, `inline` for `pdf`/`visual`).
- `docs/learning/harness/data/chats/chat_internship_{l01,l01r,l02}.json`, `docs/learning/harness/data/graph/{format.json,nodes.jsonl,chains.jsonl,coverage.jsonl,sessions.jsonl,sources.json,positions.json,render-settings.json}`, `docs/learning/harness/data/jobs/job_demo_step_response.json` — Real data initialized via `import-internship`.

---

## 4. His UI Screen / Component to Our Screen Map

| Upstream Screen / Component (`amosblomqvist/learnharness`) | Purpose in Upstream App | Our Implementation (`tools/learn/viewer/` & `.claude/skills/learn/cards/`) | API Route / Mechanism |
|---|---|---|---|
| `app/globals.css`, `components/settings/appearance.tsx` | Dark theme (`#141414` bg, `#1c1c1c` card, `#434343` border), typography, theme toggle | `tools/learn/viewer/style.css` + `#btn-theme-toggle` in `index.html` (`localStorage` persisted) | CSS variables + 375 px responsive breakpoints |
| `components/top-bar.tsx`, `components/lh-mark.tsx` | Brand mark, active session indicator, top navigation | `.top-bar` in `tools/learn/viewer/index.html` (`LH` mark, `#active-session-pill`, 6 view tabs with live counts) | SPA tab router in `app.js` |
| `components/home.tsx`, `components/minimal-home.tsx`, `components/stats-bar.tsx` | Knowledge summary strip (sessions, propositions, chains, mastery ratio, jobs) | `#home-summary-strip` in `#view-sessions` (`index.html`, `app.js`) | `GET /api/sessions`, `GET /api/graph`, `GET /api/jobs` |
| `components/sidebar.tsx`, `components/new-session.tsx` | Session list, track/status filters, session actions (open, continue, link) | `#view-sessions` (`#sessions-container`) + `#action-modal` in `index.html` / `app.js` | `GET /api/sessions` |
| `app/api/search/route.ts`, `components/session-find.tsx` | Global search across chats/nodes/plans/problems + in-session find & highlight | `#global-search-input` + `#search-results-panel` (`#view-sessions`) & `#session-find-input` (`#view-session`) | `GET /api/search?q=<q>&in=chats,nodes,plans,problems` |
| `components/chat.tsx`, `components/session-navigation.tsx`, `components/graph-context-card.tsx` | Session history view, linked session pills, turn/card stream | `#view-session` (`#session-detail-header`, `#session-cards-stream`, `#session-raw-messages`) | `GET /api/session/<id>` |
| `components/question-dock.tsx`, `components/reading-thinking.tsx`, `components/artifacts/probe-done-card.tsx` | Interactive teacher cards (`predict`, `reading`, `check`, `probe`) | `.claude/skills/learn/cards/{predict,reading,check,probe}.html` via Claude `show_widget` + `window.sendPrompt` | Tested in `cards/test.html` & `test_6_cards_and_payload_parser` |
| `components/answered-card.tsx` | Completed card display with learner thinking, answers, score, and teacher feedback | `.turn-card` in `tools/learn/viewer/app.js` (`renderSessionDetail`) | `GET /api/session/<id>` |
| `components/confidence-rail.tsx` | 6-step color ramp (`0` `#ef4444` -> `100` `#10b981`) for self-reported confidence | `confidenceColor()` & `renderConfidenceRail()` in `app.js` + `0–100` range sliders in `cards/*.html` | Rendered on every card in `#view-session` and `#view-practice` |
| `components/plan-block.tsx`, `components/plan-window.tsx`, `components/mermaid-render.ts` | Dependency tree plan map (`done`, `current` "you are here", `todo`) + Mermaid view | `#view-plan` (`#plan-svg-viewport` interactive SVG DAG + `#plan-mermaid-details` + `.you-are-here-badge` + `1.5 s` poller) | `GET /api/plan/<id>` (auto-refreshes within `< 3 s` of `plan advance`) |
| `components/graph-view.tsx`, `components/graph-canvas.ts`, `components/pan-zoom.ts` | Proposition graph (`y` = depth, directed arrows, cross-session styling, coverage color, provenance drawer, draggable positions) | `#view-graph` (`#graph-svg-container`, `#graph-drawer` provenance panel, `POST /api/graph/positions`) | `GET /api/graph`, `POST /api/graph/positions` |
| `components/chain-table.tsx` | Tabular view of reasoning chains (`Premises -> Conclusion`, provenance) | `#graph-chain-table-wrap` & `renderChainTable()` in `#view-graph` (`#btn-graph-mode-table`) | `GET /api/graph` |
| `components/practice-dock.tsx`, `components/practice-cards.tsx` | Adaptive practice problem card, chunked thinking, hint counter, attempt history | `.claude/skills/learn/cards/practice.html` (live widget) + `#view-practice` (`#practice-history-list`, `#practice-problems-list`) | `GET /api/practice/<id>` |
| `components/activity-dock.tsx`, `components/artifacts/job-card.tsx`, `components/artifacts/visual-card.tsx` | Background job monitor (`research`, `pdf`, `visual`, `demo`) | `#view-jobs` (`#jobs-list-container` with kind/status filters, `inline` vs `argon` badge, brief & output path) | `GET /api/jobs` |
| `components/artifacts/demo-card.tsx` | Self-contained interactive HTML demo rendered in a sandboxed iframe | `#demo-preview-panel` & `#demo-iframe` (`sandbox="allow-scripts allow-same-origin"`) in `#view-jobs` | `GET /api/demo/<id>` & `/demos/<id>` |

---

## 5. Deviations from Spec & Reasons

1. **`graph/format.json` included in `docs/learning/harness/data/graph/`**:
   - **Reason**: Upstream `skills/learnharness/scripts/graph.py` and `sessions.py` (at the current `HEAD` of `amosblomqvist/learnharness`) verify `graph/format.json` (`{"version": 1}`) before reading `nodes.jsonl` and `chains.jsonl`. Writing `graph/format.json` in `ensure_data_dir()` ensures both vendored scripts exit `0` with zero modifications to their logic.
2. **`.claude/` files staged with `git add -f <path>`**:
   - **Reason**: The repository's `.gitignore` has a blanket `.claude` pattern, while the spec requires `.claude/launch.json` and `.claude/skills/learn/**` to be committed on branch `learn-harness`. Explicit path-only `git add -f` was used for those 12 files.

---

## 6. Open Questions for Reviewer (CEO)

1. **`helper_worker` default for `pdf` vs large PDFs (>40 pages)**: Currently `settings.json` sets `pdf: "inline"` and `visual: "inline"` while `research: "argon"` and `demo: "argon"` as specified in WP4. Should `job new --kind pdf` automatically switch to `argon` when the target PDF exceeds 40 pages, or keep strict adherence to `settings.json` unless `--worker argon` is passed?
2. **Existing `.claude/skills/` gitignore rule**: Should `.gitignore` be updated on `main` to unignore `.claude/skills/learn/` and `.claude/launch.json`, or keep adding them via explicit path?
