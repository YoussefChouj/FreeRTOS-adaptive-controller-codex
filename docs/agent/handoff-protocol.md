# Session handoff protocol

The rules are in `AGENTS.md` > Session handoff. This page keeps the reasoning behind them (moved 2026-10-14).


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
