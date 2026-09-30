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
- Default: hand investigation and bulk coding to a worker, then verify. Inline only for edits of 3 lines or
  fewer at a known location, safety-critical judgment, or after a failed worker on a big UI task.
- Routing: Gemini models on agy first; Claude/GPT pools only when the Gemini pools are spent. Free `oc` workers
  (Hetzner Qwen, OpenRouter :free) for parallel work. Details: `env.md`.
- Laptop power is fragile (battery 3.5% health, AC drops under load): at most 1-2 parallel workers, commit before heavy jobs.
- Workers never contact 8081 or the probe, never flash. The supervisor does the live steps.

## Git
- Commit and push at every verified task boundary without asking; put the measurement in the message.
  Unpushed commits are what gets lost across a compact or crash.
- `.agent-ops/` is untracked and `.claude/` is gitignored: `git diff` shows nothing for them. Verify with grep/sed.
- About 200 files are dirty (OBJ/). Never run a bare `git status`; filter to a path.

## Token budget (limited plan)
- Every tool call re-sends the context. Read small (grep/glob first, offset/limit), pipe output through head/tail.
- Above 100k: finish the subtask, update `docs/agent/HANDOFF.md`, suggest compact. Above 150k: save and stop.
- Do not poll. Wait on background work with one background command.

## Handoff
- Keep `docs/agent/HANDOFF.md` current at every task boundary, not at the session limit: at the limit the
  agent cannot write. See AGENTS.md > Session handoff.
