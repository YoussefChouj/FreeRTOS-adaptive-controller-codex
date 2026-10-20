# FreeRTOS-adaptive-controller-codex — Project Agent Instructions

Cap: 170 lines. Hard rule: when this file grows, move detail to
`docs/context/*.md` — do not append.

## Router

Every agent in every harness starts here, then loads exactly one
**context shard**: `docs/context/README.md` (router). The shard has a
10-line *concept-to-artifact* header verified by `tools/doc_paths.py`.
Reads are small, on-demand, and may be cached for the duration of one task.

## Project identity

Clean workspace derived from the full UAV adaptive controller project.
Firmware + four operational capabilities only.

**Original project** (full agent orchestration): `FreeRTOS---Six_Degrees_of_Freedom _Adaptive_controller`

## What is in this project

Detailed directory map with vendored paths, build outputs, ground-station
modules and the dashboard service layout:
`docs/dashboard-platform/AGENT_GUIDE.md` and `docs/RUNBOOK.md`. Use them
for navigation; do not duplicate them here.

## The four core capabilities

These are the only agent-level workflows. Full references:
`docs/skills/{livewatch,capture-multislot,micoair-connect,stream-log}.md`.

1. **livewatch (probe)** — pyOCD: read/write memory, flash, halt/step/reset,
   breakpoints, watchpoints, RTT, SWO, GDB, DWARF-named reads. Always
   `python -m ground_station.livewatch verify` first.
2. **capture-multislot** — drive a multi-slot subscribe capture over WiFi.
3. **micoair-connect** — `192.168.4.1` AP; validate UDP subscribe.
4. **stream-log** — log firmware vars to CSV at ≤ 4 rates, no reflash.

Build: `bash .agent-ops/win.sh "UV4 -b -t JX_FLY -j0 USER/JX_FLY.uvprojx"`
then `python -m ground_station.flashtool.rebuild_and_flash --yes`.

## Hardware notes

- SWD wireless debugger on UART5; WiFi module on USART3.
- Inner-PID axis: `gyrox` = roll, `gyroy` = pitch.
- EKF (`s_ekf`) is authorized for the control path (operator, 2026-09-26).
  Bias: 3 selectable modes; **default fixed-at-boot** unless measured
  evidence says otherwise. Tombstone: `docs/context/flash.md`.
- Cold-boot == flash. UV4 silent link noop is a known failure mode —
  verify with `python -m ground_station.livewatch verify` after every
  flash.

## Safety

- **Research drone override (2026-08-18):** pyOCD probe capabilities are
  unlocked. Agents have unrestricted access to
  `python -m ground_station.livewatch`.
- **EKF in the control path** is authorized (operator, 2026-09-26);
  bias mode default is fixed-at-boot.
- **Arm gate:** flashing is blocked when armed.

## Authorizations

Only the rows below are granted; everything else is NOT authorized. Shrinks only on operator instruction.

| Grant | Who | Scope | Source / date | Expiry |
|---|---|---|---|---|
| Full probe + dashboard (8081) access: POST any route, restart 8081, re-grant tier-0, write and revert params | supervisor | live steps | operator 2026-09-25 | standing |
| Reflash reviewed, committed firmware via `rebuild_and_flash --force --yes`, then `livewatch verify` | supervisor | never while armed | operator 2026-09-21 | standing |
| Commit and push at every verified task boundary, no asking | any agent | repo `origin` | operator 2026-09-23 | standing |
| EKF in the control path, bias default fixed-at-boot | supervisor | firmware | operator 2026-09-26 | standing |
| Arm, idle and fly | agent | inside an operator-opened battery session only: `allow_agent_arm` on, operator said "go", RC transmitter on (ch10 = hard kill) | operator 2026-09-29, workflow B | ends at 30% battery or any abort; each battery needs a new "go" |
| Claude Code subagents (Agent tool) | nobody | REVOKED: outside workers only (agy, ark, oc) | operator 2026-09-30 14:20 | until re-granted |
| Use the operator's GitHub token (`~/.config/agent-secrets/github.token`) | any agent | CI, branches, PRs, issues on this repo; never merge, `main`, rulesets, settings or secrets (`docs/agent/governance-zones.md`) | operator 2026-10-20 | standing |

Hardware rows usable only by the session that holds stream `main` and
the `hw` lock. Every grant is agent-agnostic (operator 2026-09-30). No
Claude Code subagents ever.

## Precedence and roles

1. Operator's latest chat message. 2. Authorizations table above.
3. This file. 4. `docs/agent/memory/rules.md`.
5. Harness-private memory (`~/.claude/.../memory`) — convenience copy,
   never widens a grant. **Stricter rule wins.**
- **supervisor** — operator-started session on a stream; verifies and
  integrates that stream. Role follows how the session was started.
- **worker** — delegated agent. Edits only its named checkout; never
  flashes; never contacts 8081 or the probe.

## Session handoff

`ah` = `python -m ground_station.agent_handoff`. Full protocol: `docs/agent/handoff-protocol.md`.

- Stream `main`: page `docs/agent/HANDOFF.md`. Only main builds, flashes,
  uses the probe or 8081, and merges. Other streams:
  `.worktrees/<stream>`, branch `<stream>`, page
  `docs/agent/streams/<stream>.md`. **Never edit another stream's page.**
- Locks: `ah lock hw` (probe, flash, 8081 writes, arming), `ah lock keil`
  (UV4, writes `OBJ/`). Exit 3 = held elsewhere.
- Ship at ship points with `bash .agent-ops/ship.sh --push "msg" <paths>`
  (pathspec only; never `git add -A`); then overwrite the stream page
  (goal, facts, next 3 actions, do-nots) and `ah refresh`.
- Worker count: `ah board --workers`; one per stream.
- Cloud sessions start on `main`, branch from the brief's base (default
  `overnight-2026-10-08`), push their own branch (`git push -u origin HEAD`)
  — never to `main` or `overnight-*`. Hardware and `ah` locks are
  unavailable in the cloud; report that step as blocked.

## Conventions

- **Strategic globally, tactical locally** (operator 2026-10-20): persist
  cross-agent truth to files other agents actually read
  (`docs/agent/HANDOFF.md`, `docs/agent/memory/`); local = smallest,
  cheapest, locally-verifiable step.
- **Always read the context shard before editing.** Shard rule violations
  go in `docs/context/inbox/<task-id>.md`, not inline.
- C for Keil ARMCC V5.06, built with `--C99` (no C11). Declarations at
  block top; no VLAs.
- Run `bash tools/check.sh --changed` (the steps your diff needs, per
  `tools/check_routes.txt`); it records the pass and its tree in
  `.agent_state/last_check`, and the pre-push guard refuses a push that
  pass does not cover. CI runs the full gate; main moves only through
  `bash tools/land.sh <branch>` (`docs/agent/governance-zones.md`).
- Match surrounding style; minimum code; touch only the task.
- Tunable parameter sets use the `<THING>_ROW(...)` macro, one aligned
  row per instance, history below the table. Pattern:
  `docs/firmware-table-pattern.md` (reference: `API/pid.c`).
- Lab commands live in `docs/lab/COMMANDS.md`. A commit that adds or
  changes a command/flag updates that doc in the same commit;
  `ground_station/lab/tests/test_commands_doc.py` fails otherwise.

## Code navigation protocol

Spec: `docs/dashboard-platform/AGENT_MAP_SPEC.md`. No vector RAG, no
LLM-written metadata. (1) Exact name: `serena find_symbol` /
`find_referencing_symbols` when loaded, else `rg`/`grep`. (2) When
`ground_station/agent_map/` exists, `python -m ground_station.agent_map
explain <name>` first. (3) Do not search `stm32_lib/`, `FreeRTOS/`, or
`OBJ/` unless vendor. (4) Read the file's safety tier; tier 0
(flight-critical) needs explicit permission. (5) Log nav failures to
`.agent_memory/frictions.jsonl`. (6) After firmware moves, regenerate
clangd DB: `python -m ground_station.flashtool.compile_commands`.

## Session state

The stream page is authoritative at every checkpoint
(`docs/agent/HANDOFF.md` for `main`, `docs/agent/streams/<name>.md`
otherwise; small, overwritten). `ah start` prints it. `.claude_state.md`
is a stub; full history is archived in `docs/agent/ledger/` and must
not be read whole.

## Friction tracking

`.agent_memory/frictions.jsonl`; proposals `.agent_state/friction-proposals/`;
shard rule violations `docs/context/inbox/<task-id>.md`.
