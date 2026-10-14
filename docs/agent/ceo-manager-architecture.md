# CEO / Manager two-account architecture

Status: proposal, 2026-10-01. Every number below was measured on 2026-10-01 from local files; how to re-measure is at the end.

## 1. What the data says

| Fact | Value | Source |
|---|---|---|
| Account A (desktop, org `c59b21`) weekly use | **76 %** at 10-01 07:26 | `%APPDATA%/Claude/plan-usage-history.json` |
| A: time to burn a full 5 h window | 68 min (09-30 13:50→14:58), 76 min (19:16→20:32) | same |
| A: weekly points per full 5 h window | 13 (51→64), 12 (64→76) | same |
| Account B (CLI, org `bbe3f1`) | 5 h ≈ 5 %, weekly ≈ 1 % (last sample 09-30 19:05) | same |
| CLI login | account B (`~/.claude.json` oauthAccount org `bbe3f1`); desktop keeps its own login (A) | `~/.claude.json` |
| Desktop base context (first request of a session) | 55–78 k tokens, ~40 k of it a cross-session cached prefix | session transcripts `usage` |
| CLI base context (first request) | 51 k, 55 k | same |
| Manager base with `--disable-slash-commands --strict-mcp-config` | **27.6 k** (research run, 2026-10-01) | same |

Conclusions:
1. **The weekly limit binds, not the 5 h window.** At the current burn, A gets roughly 8 full windows a week; with 24 points left it has about 2. Account B mainly doubles *weekly* capacity.
2. At ~70 k tokens of fixed context per request, every tool call in the CEO seat re-sends a small book. Fewer CEO tool calls, and a smaller base, both stretch a window.
3. Effort is the other big lever on A: effort drives output and thinking tokens, the expensive side.

## 2. Roles

| Seat | Account / surface | Model, effort | Does | Never does |
|---|---|---|---|---|
| CEO | A, desktop Code tab | Opus 5.5; `high` by default, `max` only for hard debug and design sessions | plans, decides, writes briefs, accepts or rejects reports, hard debugging | reads worker logs, polls, bulk reading or coding |
| Manager | B, `claude` CLI (headless per work package) | Opus 5.5; `high` for any package that includes review, `medium` for dispatch-only | dispatches agy/oc/ark workers, waits in the background, reviews diffs, runs scoped tests, commits on the work branch, writes the report | flash firmware, touch 8081 or motors, write auto-memory, push to `main` unless the brief says so |
| Workers | agy (VPS default), oc (free), ark (deepseek) | unchanged | code and investigation | unchanged (see AGENTS.md) |

Why not `medium` for everything on B: the worker-verification memories show that workers' DONE/rc=0 are *claims*. A medium-effort reviewer behind an unreliable worker stacks two weak links. Start at `high`, measure B's burn per package, then drop to `medium` where the reports stay clean.

## 3. Channel: file contract + headless doorbell

**Contract (always):** `docs/agent/briefs/WP-<id>.md` → `docs/agent/reports/WP-<id>.md`. Both live in the repo, so they survive `/compact`, crashes and power loss.

**Transport (default): headless per package.** The CEO runs, in the background:

```bash
bash .agent-ops/manager.sh run <id>          # follow-up: manager.sh resume <id> <msg-file>
```

- `manager.sh` makes worktree `../wt-wp<id>` on branch `wp/<id>`. It pipes `docs/agent/MANAGER.md` (the manager loop and rules) plus the brief into `claude -p` with the trim flags and a fixed `--allowedTools` list: worker script, gate, pytest, read-only git, merge `--ff-only`, add and commit.
- **Manager loop:** write a worker task → spawn on the VPS → bounded wait (`timeout 540`, re-run on exit 124) → `merge --ff-only` → `gate.py` → bounce or accept → report.
- **Manager never rewrites:** it bounces with the gate output verbatim. Max 2 bounces (3 rounds), then Status BLOCKED to the CEO.

- The CEO waits on one background notification, with zero polling, and only the report enters A's context.
- Every package gets a fresh B context, so no bloat accumulates. Follow-ups use `claude -p --resume <session_id>`, taken from the JSON.
- Headless runs cannot ask for permission. Give an explicit `--allowedTools` list, or a permission mode, per brief.
- Validated on 2026-10-01 by the research run in section 6: it ran headless on B, launched from the A desktop session.

**Alternative: interactive manager + `SendMessage`.** `ListAgents` in the desktop already lists local peer sessions, including an interactive non-desktop session. This is useful when you want to watch the manager's terminal. It is untested across two accounts, and every reply lands in A's context. Try it once before relying on it.

### Brief template (CEO → Manager)
```
Goal: <one sentence>
Acceptance: <exact commands + expected output>
Scope: <files/dirs>          Forbidden: <flash / 8081 / motors / main>
Allow globs: <for gate.py --allow, include ".agent-ops/out/*" for the worker digest>
Worker lane: <agy-vps chain | oc | ark>   Max worker rounds: 3   Effort: <medium|high>
Report to: docs/agent/reports/WP-<id>.md (<= 40 lines)
```
Example: `docs/agent/briefs/WP-1.md`.
### Report template (Manager → CEO)
```
Status: DONE | PARTIAL | BLOCKED
Commits: <sha — subject>
Gate: <final GATE line, verbatim>
Verification: <command> -> <raw pass/fail line>   (run by manager, not claimed by worker)
Worker rounds: <n>/3; one line per bounce: why
Deviations / open questions: ...
```
The CEO verifies by spot-check (`git show --stat <sha>`, re-run one acceptance command), not by re-reading.

## 4. Trimming the base context

Moved to [ceo-research.md](ceo-research.md) section A, with the 2026-10-14 measurements. Manager trim flags: `--disable-slash-commands --strict-mcp-config` (`--bare` needs an API key). Only the CEO writes auto-memory.

## 5. Measure, don't guess

- Burn per window: read `plan-usage-history.json` samples per org, where `fh` is the 5 h % and `sd` the weekly %. Compare %/hour at CEO `max` vs `high`, and B's weekly points per work package.
- Base context: the first assistant `usage` per transcript (`input + cache_creation + cache_read`) in `~/.claude/projects/<proj>/*.jsonl`.
- Candidate manager task WP-0 (not written): a tools/usage_report.py that prints both of these per account.

## 6. Research findings

Moved to [ceo-research.md](ceo-research.md) section B (terms, how limits count, two accounts on one PC, orchestration, trimming).
