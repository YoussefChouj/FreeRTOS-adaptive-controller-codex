# Durable behavior rules (tool-neutral)

Migrated 2026-09-30 from the Claude-private memory (`~/.claude/projects/.../memory/`).
Authorizations and their expiry live in `AGENTS.md` > Authorizations, not here.

## Verification
- A worker's DONE, `rc=0` and self-reported model are claims. Read the raw `.out` log and re-check the
  artifact (`git diff --stat`, the tests, one targeted check). A worker once stubbed its own test and reported PASS.
- `rc=0` means the shell command returned, not that the task succeeded. `rc=3` is both auth failure and quota: read the log tail.
- Never write a number into a spec, design doc or state file that was not measured this session.
- Liveness: STALLED / unrecognized_model / STARTED lines lie. Only `pgrep` inside WSL is real.
- Run scoped tests (changed files' pytest + their JS harnesses), not the whole tree, unless a shared
  fixture/conftest changed or the operator asks. A full tree can hang on probe routes with the drone off.

## Delegation
- Default since 2026-10-04 (operator order): the desktop session (CEO) does every item itself, inline, one at a
  time. No Claude Code subagents, no manager, no outside workers unless the operator asks again.
  The worker rules below are kept for when that changes.
- Routing: Gemini models on agy first; Claude/GPT pools only when the Gemini pools are spent. Free `oc` workers
  (Hetzner Qwen, OpenRouter :free) for parallel work. Details: `env.md`.
- Laptop power is fragile (battery 3.5% health, AC drops under load): at most 1-2 parallel workers, commit before heavy jobs.
- Workers never contact 8081 or the probe, never flash. The supervisor does the live steps.

## Git
- Commit and push at every verified task boundary without asking; put the measurement in the message.
  Unpushed commits are what gets lost across a compact or crash.
- `.agent-ops/` is untracked and `.claude/` is gitignored: `git diff` shows nothing for them. Verify with grep/sed.
- About 200 files are dirty (OBJ/). Never run a bare `git status`; filter to a path.

- Commit by explicit pathspec: `git commit -m "..." -- <paths>`. Never `git add -A`, `git add .` or `commit -a`.
  Several sessions share the main tree's index; commit 1053086 swept in another session's staged, unverified files.

## Parallel sessions
- One stream = one tree = one session. Start with `python -m ground_station.agent_handoff start`. On STOP, take
  your own worktree (`new <name>`); do not work in a tree a live session holds. See AGENTS.md > Session handoff.
- Only stream `main` builds with Keil, flashes, uses the probe or 8081, and merges. Other streams hand a branch to
  `main` and ask for the live step in their page.
- Locks `hw` and `keil` are leases, not queues. Exit 3 = held by another stream: do other work, tell the operator.
- Workers: count real processes before spawning (`board --workers`), never trust a ledger. Local cap is 1 (power
  guard in `agent-ops.ps1`). One worker per stream. Free `oc` pools share 10 and 20 req/min across all sessions.
- Write the brief to a file in your own tree; name the worker after the stream (`<stream>-<task>`) so logs do not collide.

## Token budget (limited plan)
- Every tool call re-sends the context. Read small (grep/glob first, offset/limit), pipe output through head/tail.
- Above 100k: finish the subtask, update your stream page, suggest `/clear` and restart from the page (cheaper
  than compact: about 4k tokens in, no summary to pay for). Above 150k: save and stop.
- Do not poll. Wait on background work with one background command.
- The 5-hour window is shared by every Claude session on one account. Measured 2026-09-30: about 63k tokens of
  fixed overhead per session (tools, MCP, skills) before any work. So: at most 2 Claude supervisor sessions at once;
  park the others (page written, claim released) and let outside workers carry them.
- Never resume a long transcript in a new session or account: it re-sends the whole history uncached. Start fresh
  from the stream page.

## Handoff
- Keep your stream page current at every task boundary, not at the session limit: at the limit the agent cannot
  write. See AGENTS.md > Session handoff.
