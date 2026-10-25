# Spec: learn harness (/learn) for Claude Code desktop

Owner: operator. Reviewer: CEO (Claude Code desktop session), who reviews everything after the build.
Goal: rebuild the features of amosblomqvist/learnharness so that **Claude Code desktop is the teacher**
(no API key, no ChatGPT/Copilot sign-in). The operator likes his method and UI and wants feature parity:
the step-by-step flow, every session saved and searchable, linked sessions, continuing a session,
practice sessions and demos made from the material and progress. Use: learning new topics, thesis research,
internship prep (Chinese interviews).

## 0. Read first (in this order)

1. Demo transcript: `docs/research/youtube_learning_harness_demo_transcript.md` (22 min video). It lists the features.
   Never paste its text into any file. Paraphrase.
2. Reference repo: `git clone https://github.com/amosblomqvist/learnharness ../learnharness-ref` (outside this repo,
   never commit it). Read:
   - `src/agent/prompts/**` (teacher 05-method, 10-planning, 20-teaching, 28-predictions, 30-question-principles,
     40-jobs, 50-demos; prober 10-probe-doctrine; practice 00-base, 10-doctrine; shared 10..40)
   - `src/agent/system-prompt.md`, `src/agent/tools/*.ts` (plan, probe, practice, graph, jobs, mermaid-plan)
   - `src/agent/subagents/*.ts` (researcher, pdf-reader, visual-maker, demo-builder, extraction)
   - `skills/learnharness/references/data-format.md`, `skills/learnharness/scripts/{_data,graph,sessions}.py`
   - `tests/fixtures/golden-data/**` (the on-disk layout), `tests/skills/*.py`
   - the UI under `src/app` and `src/components`: list every screen and control (session list, search, plan map,
     graph view, cards, confidence slider, practice view, jobs/demos view)
3. License: the repo is AGPL-3.0 (the skill folder has its own LICENSE). Anything copied or adapted keeps a header
   naming the source file and the license. This repo is private; never publish the harness.
4. Existing learner data here: `docs/learning/internship/` (lessons; `data/progress.json` with glossary, lessons,
   breakpoints), `docs/agent/learning-log.md`. Existing skills to cooperate with (do not change them):
   `grill-paper`, `wiki`, `grilling`, `control-rigor`.

## 1. Architecture (fixed; do not swap for a different design without writing why in the report)

| Part | His app | Here |
|---|---|---|
| Teacher model | pi-ai agent with his prompts | Claude Code desktop, driven by the skill `.claude/skills/learn/SKILL.md` |
| Answer cards (predict, check, reading box) | React components | HTML card templates Claude fills in and renders with its `show_widget` tool; the Submit button calls the global `sendPrompt(text)`, which posts the answer as the next chat message |
| Plan map, graph, sessions, search, practice, jobs | Electron/Next UI | Local viewer web app served by `learn.py serve` on port 8770, opened in the desktop app's browser pane |
| Data | his data dir | Same file layout and formats (his `data-format.md`) under `docs/learning/harness/data/` |
| Tools the agent calls | TS tools | One Python CLI `tools/learn/learn.py` (stdlib only), compact output |
| 4 helper subagents | background LLM runs | job records; Claude runs them inline, or the Argon lane for slow jobs (section 6) |

Platform: Windows 10, PowerShell + Git Bash, Python 3 (stdlib only for CLI and server; pytest for tests).
Viewer: plain HTML/JS, no build step. Libraries only from cdnjs/jsdelivr with exact pinned versions
(e.g. cytoscape or d3 for the graph, mermaid for the plan), or vendored into the viewer folder.

## 2. Work packages

WP1 must finish first (it defines the data API). WP2, WP3, WP4 can then run in parallel; they touch different folders.

### WP1: data store + `learn.py` (folder `tools/learn/`, data in `docs/learning/harness/data/`)

- Mirror his golden-data layout and formats exactly (graph/nodes.jsonl, chains.jsonl, coverage.jsonl, sessions.jsonl,
  sources.json, positions.json, render-settings.json; chats/*.json; jobs/*.json; settings.json). Add only what his
  format lacks (plans, practice attempts, card events) and document each addition in `tools/learn/API.md`.
- Vendor his `_data.py`, `graph.py`, `sessions.py` into `tools/learn/vendor/` (with license header). They must run
  against our data dir unchanged in logic (parity check).
- Subcommands (all accept `--json`; default output is compact text, under 1.5 KB unless `--full`):
  - `session new --topic T --goal G [--track internship|thesis|general] [--link ID...]` -> prints id
  - `session list [--track] [--limit]`, `session show ID`, `session link ID OTHER`, `session close ID`
  - `continue [ID]` -> resume pack: topic, goal, plan with current step, last 3 card events, open questions,
    weak nodes, linked sessions. Default ID = most recent open session. Must stay under 1.5 KB.
  - `plan set ID --file plan.json` (steps with id, title, deps, status todo|current|done), `plan advance ID`,
    `plan show ID` (text tree with a "you are here" marker), `plan mermaid ID`
  - `log ID --kind predict|reading|check|probe|practice|note --step S --payload-file F` (stores the card answer,
    confidence 0-100, thinking fields, Claude's score and feedback); also appends to the chat file
  - `extract ID --file nodes.json` -> appends propositions (nodes) and reasoning chains with provenance
    (session, step, source, timestamp); dedupes by normalized text; cross-session links allowed
  - `search Q [--in chats,nodes,plans,problems] [--limit]` -> ranked hits with session id + step + 1-line snippet
  - `probe seed ID` -> list of candidate nodes for the probe, from the graph + `progress.json` breakpoints
  - `practice next ID|--nodes N...`, `practice record ID --problem P --result correct|partial|wrong --hints K`
    (writes coverage.jsonl and updates `docs/learning/internship/data/progress.json` breakpoints when track=internship)
  - `job new --kind research|pdf|visual|demo --brief-file F`, `job list`, `job show`, `job done ID --output PATH`
  - `import-internship` -> imports existing internship lessons/progress as sessions (idempotent)
  - `selftest`, `serve --port 8770`
- Tests: `tools/learn/tests/` (pytest). Use a temp data dir; never write test data into the real data dir.

### WP2: viewer (folder `tools/learn/viewer/`, served by `learn.py serve`)

Screens (match his UI where it exists; list his screens in the report and map each one):
- Sessions: list with track, topic, last activity, status; search box (calls `/api/search`); open, continue, link.
- Session view: the full history (cards, answers, confidence, scores, feedback) in order; linked sessions.
- Plan map: dependency tree for the session, current step highlighted ("you are here"), done/todo states.
  Auto-refresh: shows a `plan advance` within 3 s without a manual reload.
- Graph: nodes = propositions, arrows = reasoning direction, y position = depth (longest chain from a root),
  cross-session links styled differently, color = coverage (mastered / shaky / unseen from practice results),
  click a node -> provenance (session, step, source, snippet). Filter by session/track. Save positions to positions.json.
- Practice: problem stream history per session: problem, node tags, difficulty, hints used, result.
- Jobs: research digests, PDFs read, visuals, demos; open a demo (self-contained HTML under `data/demos/`).
- JSON API: `/api/sessions`, `/api/session/<id>`, `/api/plan/<id>`, `/api/graph`, `/api/search?q=`, `/api/practice/<id>`,
  `/api/jobs`, `/api/demo/<id>`. Read-only, except saving graph positions.
- Add a `learn-viewer` entry to `.claude/launch.json` (create the file if missing; keep any existing entries):
  runtimeExecutable `python`, args `tools/learn/learn.py serve --port 8770`, port 8770.
- Works at 375 px width and in dark mode.

### WP3: skill + card templates (folder `.claude/skills/learn/`)

- `SKILL.md` (under ~250 lines) with the commands: `/learn <topic>`, `/learn continue [id]`, `/learn practice [topic|nodes]`,
  `/learn demo <concept>`, `/learn search <q>`, `/learn sessions`, `/learn graph`, `/learn extract`.
- Method sections adapted from his prompts (cite the source file of each): probe (multiple choice that binary-searches
  the learner's edge, seeded by `probe seed`), planning (minimal dependency tree, `plan set`), teaching (one reasoning step
  at a time, each obvious when it lands), predictions, question principles, confidence, voice/accuracy/mental model,
  practice doctrine, jobs, demos.
- Per teaching step, the loop is: predict card -> explanation with a "thinking while reading" card -> check card
  (3 thinking fields, 2 questions), each with a confidence slider. After the answer: score, short feedback,
  `log`, `plan advance`, and every few steps `extract`.
- Session start: `session new` (or `continue`), open the viewer in the browser pane (`preview_start` name `learn-viewer`).
- Operator style (put in the skill): bottom line first, plain and visual, one analogy, tables; for the internship track,
  key terms in Chinese with pinyin; every step saved before the next card.
- Token rules for Claude: use CLI outputs, never read the data files whole; resume via `continue`.
- `cards/`: `predict.html`, `reading.html`, `check.html`, `probe.html` (multiple choice), `practice.html`
  (problem + thinking sent in chunks + "hint" button + submit). Rules for `show_widget` HTML: no doctype/html/head/body
  tags, transparent background, CSS variables with fallbacks, each card under 6 KB, placeholders like `{{QUESTION}}`.
  Submit calls `sendPrompt(text)` with this exact format (document it in SKILL.md):
  ```
  [learn] session=<id> step=<step> kind=<kind> confidence=<0-100>
  think1: ...
  think2: ...
  think3: ...
  q1: ...
  q2: ...
  ```
  (omit empty fields; practice adds `hints=<k>`; probe adds `choice=<letter>`). Hint button sends `[learn] ... kind=hint`.
- `cards/test.html`: a page that loads each card with sample values and a stub `sendPrompt` that prints the payload,
  so the cards can be checked in a normal browser.

### WP4: practice, demos, helpers, migration

- Practice doctrine from his prompts: adaptive problem stream; thinking can be sent in chunks; minimal hints;
  difficulty = number of ideas combined + how familiar the cues are; every problem tagged with node ids;
  the operator can drop own problem files into `docs/learning/harness/data/problems/` (md or json; document the format).
  Results go back into coverage.jsonl and the progress.json breakpoints.
- Demos: per his 50-demos prompt; a demo is one self-contained HTML file (sliders, live plot) in `data/demos/`, listed
  as a job, viewable in the viewer and publishable later by the CEO as an Artifact. Ship one example demo
  (a first-order step response with a gain slider) to prove the path.
- Helpers (his researcher, pdf-reader, visual-maker, demo-builder): define each as a job kind with a brief template in
  `.claude/skills/learn/jobs/<kind>.md`. Setting `helper_worker` in `data/settings.json`: `inline` (Claude does it in the
  session) or `argon` (writes `.agent-ops/briefs/learn-job-<id>.md` and prints the command
  `.agent-ops/argon.ps1 -Id learn-<id> -Brief <path>` for the CEO; never run Argon yourself). Default `argon` for
  research and demo jobs, `inline` for pdf and visual.
- Run `import-internship` on the real data once and commit the result.

## 3. Acceptance tests (run all; record command + real output in the report)

1. `pytest tools/learn/tests -q` passes; `python tools/learn/learn.py selftest` exits 0.
2. Format parity: the vendored `graph.py` and `sessions.py` run against `docs/learning/harness/data` and a temp copy of
   his golden-data fixture, exit 0, same kind of output on both.
3. Round trip in a temp data dir: `session new` -> `plan set` (5 steps) -> `log` predict + reading + check ->
   `plan advance` -> `extract` 3 nodes + 2 chains -> `search integral` finds the session and the node ->
   `continue` prints current step 2, the last card, and stays under 1.5 KB.
4. `session link A B`: both `session show` outputs and `/api/session/<id>` list the link.
5. Viewer: with `serve` running, every API route returns 200 and valid JSON; the plan page reflects a `plan advance`
   within 3 s (check by polling the API the page uses, and with a screenshot if a browser is available).
6. Cards: every card in `cards/test.html` renders, the slider runs 0-100, Submit produces a payload that a test parses
   back into the fields (pytest or node test); each card file is under 6 KB.
7. Practice: `practice next` returns a problem whose difficulty follows the rule; `practice record` updates
   coverage.jsonl and (track internship) the progress.json breakpoint, test in a temp copy.
8. Jobs: `job new --kind demo` with `helper_worker=argon` writes the brief and prints the argon command without running it.
9. `import-internship` run twice gives the same data (idempotent).
10. `docs/learning/harness/PARITY.md`: every feature from the transcript, his prompts and his UI screens is a row with
    the implementation and the test that covers it. Nothing may be marked "dropped"; anything not built is "open" with a reason.

## 4. Rules

- Branch `learn-harness` from `main`. Stage by path only (never `git add -A` or `git add .`). The tree has many
  unrelated modified/untracked files: leave them alone. Commit after each finished piece. Never push.
- No firmware build or flash, never touch port 8081, the SWD probe, or the lab service.
- No paid services, no API keys, no new accounts. No global npm installs.
- Do not edit existing skills, AGENTS.md, CLAUDE.md, or memory files.
- Progress file: after each finished piece append to `.agent-ops/out/learn-harness-progress.md`
  (done, next, commit hash), so a stopped run can resume.

## 5. Hand back

`.agent-ops/out/learn-harness-report.md`: acceptance table (pass/fail, command, output excerpt), commits, files added,
his-UI-screen to our-screen map, deviations from this spec with reasons, open questions.
