# FreeRTOS-adaptive-controller-codex — Project Agent Instructions

## Project identity

This is a clean workspace derived from the full UAV adaptive controller project.
It contains only the firmware code plus the four operational capabilities needed
to work with the running hardware.

**Original project** (full agent orchestration): `FreeRTOS---Six_Degrees_of_Freedom _Adaptive_controller`

## What is in this project

| Path | What it is |
| --- | --- |
| `API/` `TASK/` `BSP/` `USER/` | STM32F4 firmware sources. Compiled by Keil ARMCC V5.06. |
| `FreeRTOS/` `Global_file/` `stm32_lib/` | Vendor / FreeRTOS / HAL support headers and sources. |
| `OBJ/JX_FLY.axf` | Build output with DWARF — needed for symbol-named reads. |
| `ground_station/livewatch/` | pyOCD probe: read/write memory, registers, flash, breakpoints, watchpoints, RTT, SWO, GDB, DWARF-named reads. |
| `ground_station/comm/` | UDP/serial telemetry: `wifi_bridge`, frame decoders, subscribe protocol helpers. |
| `ground_station/flashtool/` | Keil/UV4 build orchestration + pyOCD flash helpers. |
| `docs/telemetry-protocol.md` | Wire-format reference for subscribe protocol (frame types 0x08/0x09..0x0C). |
| `docs/glossary.md` | Domain terms. |
| `docs/RUNBOOK.md` | How to start/restart the dashboard service, tier-0 switch, MCP notes. |
| `docs/skills/` | Reference docs for the four core capabilities. |
| `docs/dashboard-platform/` | Long-lived firmware/dashboard platform specification. |
| `ground_station/service/` | Dashboard ground-station service (port 8081). Agents: read `docs/dashboard-platform/AGENT_GUIDE.md` first (`GET /api/routes`, UI selectors, `python -m ground_station.service.browser_smoke`). Never POST to the live service, except through the dashboard MCP tools (`.mcp.json` server `dashboard`). |

## The four core capabilities

These are the only agent-level workflows in this project.

### 1. livewatch (probe)
Full pyOCD capability layer. Read or write any memory, flash, halt/step/resume/reset,
breakpoints, watchpoints, RTT, SWO trace, GDB server — by DWARF name or by address.

```powershell
# Always verify first — catches stale ELF
python -m ground_station.livewatch verify

python -m ground_station.livewatch read mrac_state.roll.What[0]
python -m ground_station.livewatch watch group:ekf --hz 20 --secs 30
python -m ground_station.livewatch log of_drift --secs 60
```

### 2. capture-multislot
Drive a multi-slot subscribe capture over WiFi from a shell (no GUI).

```powershell
python -m ground_station.livewatch.capture_preset flight_comprehensive --secs 10
```

Preset definitions: `ground_station/livewatch/multi_slot_presets.yaml`

### 3. micoair-connect
Establish and validate a working MicoAir WiFi UDP connection (`192.168.4.1` AP).

### 4. stream-log
Log any firmware variables from the running drone to CSV over serial or WiFi,
at up to four independent rates — no firmware change and no reflash.

```powershell
python -m ground_station.livewatch.stream_log --seconds 30 --out logs/run.csv
```

## Firmware build

```powershell
# Keil uVision CLI build (from USER/ directory)
UV4 -b -t JX_FLY -j0 JX_FLY.uvprojx

# Then flash (never --force; stop on a non-zero exit)
python -m ground_station.flashtool.rebuild_and_flash --yes
```

## Hardware notes

- **Flashing**: SWD wireless debugger on UART5, WiFi module on USART3.
- **Inner-PID axis mapping**: `gyrox` = roll loop, `gyroy` = pitch loop.
- **EKF** (`s_ekf`) is authorized for the control path (operator, 2026-09-26). Bias estimation has 3 selectable modes; the default is fixed bias at boot.
- **Cold-boot == flash**. A successful firmware flash produces a cold-boot init by default.
- **Flash integrity is the operator's responsibility.** UV4 silent link noop is a known failure mode.
  Verify with `python -m ground_station.livewatch verify`.

## Safety

- **Research drone override (2026-08-18):** All pyOCD probe capabilities are unlocked.
  The agent has unrestricted access to `python -m ground_station.livewatch`.
- **EKF in the control path**: authorized (operator, 2026-09-26). Keep bias mode fixed-at-boot as the default unless measured evidence favours another mode.
- **Arm gate**: Flashing is blocked when armed.

## Authorizations (the only grants in force)

Anything not listed here is NOT authorized. Grants expire as stated. Workers hold none of them.

| Grant | Who | Scope | Source / date | Expiry |
| --- | --- | --- | --- | --- |
| Full probe + dashboard (8081) access: POST any route, restart 8081, re-grant tier-0, write and revert params | supervisor | live steps | operator 2026-09-25 | standing |
| Reflash reviewed, committed firmware via `rebuild_and_flash --force --yes`, then `livewatch verify` | supervisor | never while armed | operator 2026-09-21 | standing |
| Commit and push at every verified task boundary, no asking | any agent | repo `origin` | operator 2026-09-23 | standing |
| EKF in the control path, bias default fixed-at-boot | supervisor | firmware | operator 2026-09-26 | standing |
| Arm, idle and fly | agent | ONLY inside an operator-opened battery session: `allow_agent_arm` on, operator said "go" for this battery, RC transmitter on and in reach (ch10 = hard kill) | operator 2026-09-29, workflow B | ends at 30% battery or any abort; next battery needs a new "go" |
| Claude Code subagents (Agent tool) | nobody | REVOKED: outside workers only (agy, ark, oc) | operator 2026-09-30 14:20 | until the operator re-grants |

Never, without a fresh operator instruction: spin motors or send idle / MOTOR_BENCH, arm outside the session above,
flash while armed, let a worker touch 8081 or the probe.

The hardware rows (probe, 8081, reflash, arm) are usable only by the session that holds stream `main` and the `hw`
lock (see Streams below). Every grant is agent-agnostic (operator 2026-09-30): a supervisor session in any harness or
account (Claude Code, Cursor, Codex, agy, ...) holds all rows above, hardware included, with no re-grant in its chat.
Delegated work goes to outside workers only (agy, ark, oc), never to Claude Code subagents.

## Precedence and roles

1. Operator's latest chat message. 2. The table above. 3. This file. 4. `docs/agent/memory/rules.md`.
5. Harness-private memory (for example `~/.claude/.../memory`) is a convenience copy and never widens a grant.
If two rules conflict, the stricter one wins until the operator says otherwise.

- **supervisor**: a session the operator started on a stream, in any harness or account. It verifies and integrates
  that stream. The role follows how the session was started, not which tool runs it.
- **worker**: a delegated agent. Edits only its named checkout, never flashes, never contacts 8081 or the probe.
- A harness without the dashboard MCP tools uses read-only `GET http://127.0.0.1:8081/api/routes` (bypass any proxy) and
  leaves live POSTs to the supervisor.

## Session handoff

Several sessions run in parallel, and any of them can end abruptly (plan limit). An agent at its limit cannot write.
The tool is `python -m ground_station.agent_handoff` (`ah` below). It runs from any worktree, in any harness or account.

**Streams.** A stream is one line of work: one tree, one branch, one page, one session at a time.
- Stream `main` = the main tree. It is the only stream that builds with Keil, flashes, uses the probe or 8081, and
  merges other branches. Its page is `docs/agent/HANDOFF.md`.
- Every other stream lives in `.worktrees/<stream>` on branch `<stream>`. Its page is `docs/agent/streams/<stream>.md`,
  committed on its own branch, so pages never conflict in a merge.
- First command of every session: `ah start [stream] --as <harness>`. It claims the stream, refreshes the page and
  prints the board and the page. If it answers STOP, a live session holds that tree: run `ah new <name>` for your own
  worktree, or pick another stream. Use `--takeover` only when the operator says the other session is dead or at its limit.
- Never edit another stream's tree or page. Read other streams with `ah board`.

**Shared resources.** Take the lock before use, release it after: `ah lock hw` (probe, flash, 8081 writes, arming) and
`ah lock keil` (UV4 build, writes `OBJ/`). Exit code 3 means another stream holds it: do other work, do not poll.
Locks expire (default 30 min). Workers are scarce: count running ones first (`ah board --workers`), one worker per
stream, and see `docs/agent/memory/rules.md` > Parallel sessions.

**At every task boundary:** commit with an explicit pathspec (`git commit -m "..." -- <paths>`; never `git add -A`,
`git add .` or `commit -a`, because other sessions' files are in the same index), push, overwrite the stream page
(goal, facts, next 3 actions, do-nots), then `ah refresh`.

**Continuing elsewhere** (another Claude account, Cursor, Codex, agy): the claims, locks and pages are files on this
machine and in git, so nothing depends on a harness's own session store. A harness that reads this repo runs
`ah start <stream> --as <harness> --takeover`. One that cannot gets the output of `ah prompt <stream> --for <harness>`
pasted in (about 4k tokens: role, AGENTS.md, the stream page, `rules.md`). Do not resume the old transcript.

Durable lessons go to `docs/agent/memory/` (git-tracked), not only to a harness-private memory. Environment facts go
to `env.md`, behavior rules to `rules.md`.

## Conventions

- C for Keil ARMCC V5.06, project built with `--C99` (no C11: compile-time checks use the typedef form, coding
  standard rule 8b). Declarations at block top, no VLAs.
- Before a commit: `bash tools/check.sh` (steps: `docs/firmware-quality.md`), no Keil or hardware needed. While
  iterating: `bash tools/check.sh --fast` or named steps (`bash tools/check.sh doc-paths`); a commit needs CHECK PASS.
- Match surrounding style over personal preference.
- Minimum code that solves the problem. Nothing speculative.
- Touch only what the task requires.
- Tunable parameter sets (PID loops, MRAC axes, filter/estimator gains): one aligned row per
  instance under a single column header, via a `<THING>_ROW(...)` macro, with history below
  the table. Follow `docs/firmware-table-pattern.md` (reference: `API/pid.c`).

## Code navigation protocol

Applies to every agent: interactive sessions, workers, dashboard agents.
Spec: `docs/dashboard-platform/AGENT_MAP_SPEC.md`. No vector RAG, no LLM-written metadata.

1. Exact name known: Serena `find_symbol` / `find_referencing_symbols` when the `serena` tools
   are loaded (Windows sessions and opencode workers, read-only), else `rg`/grep.
2. When `ground_station/agent_map/` exists: run `python -m ground_station.agent_map explain <name>`
   before opening files.
3. Do not search `stm32_lib/`, `FreeRTOS/` or `OBJ/` unless the task is about vendor code.
4. Check the file's safety tier (spec, step 2) before editing. Tier 0 (flight-critical)
   needs explicit permission in the task.
5. Log navigation failures (could not find X, landed in the wrong file) to
   `.agent_memory/frictions.jsonl`.
6. After firmware sources are added or moved, regenerate clangd's database:
   `python -m ground_station.flashtool.compile_commands`.

## Available skills (Claude Code slash commands; other harnesses: follow the same steps from `docs/skills/`)

| Skill | Purpose |
| --- | --- |
| `/session-start` | Begin a session — read state, surface open items |
| `/session-end` | Wrap up — summarize, verify, update session state |
| `/stream-log` | Reference: variable-rate CSV logging + rebuild/flash pipeline |
| `/agy-delegate` | Hand a task to an Antigravity worker in WSL tmux and supervise it |
| `/workflow-a` | Workflow A: manual RC flight, WiFi `stream_log` capture, Keil preset, offline `flight_review` |
| `/workflow-b` | Workflow B: launch a fly campaign; the operator runs `campaign_fly` in a terminal (MCP only if agent-flown) |
| `/workflow-c` | Workflow C: fly one flight at a time, debrief each (`ground_station.analysis.flight_debrief`), propose the next |

All three are terminal and chat only (operator 2026-10-07): confirmations are QA in chat, gain and preset writes
go through the Keil watch window, no dashboard approvals. Offline analysis of a recorder session:
`python -m ground_station.analysis.flightlab analyze <log dir>` (spec `docs/analysis/flightlab-spec.md`).
Campaigns for B/C: `ground_station/service/campaigns/*.yaml`.

Probe and capture references: `docs/skills/livewatch.md`, `docs/skills/capture-multislot.md`.

## Session state

The live handoff page is your stream's page (`docs/agent/HANDOFF.md` for `main`, `docs/agent/streams/<stream>.md`
otherwise; small, overwritten at each checkpoint). `ah start` prints it; update it at every task boundary. `.claude_state.md` is a stub; the full history is archived in
`docs/agent/ledger/` and must not be read whole.

## Friction tracking

Friction data lives in `.agent_memory/frictions.jsonl`. Proposals land in
`.agent_state/friction-proposals/`.
