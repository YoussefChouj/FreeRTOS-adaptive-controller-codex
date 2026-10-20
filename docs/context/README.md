# Context — domain shards

Each agent working in this repo reads `AGENTS.md` first, then drops into one
or more of these shards based on the task. The router lives in `README.md`
in this directory. Shards are tracked in git, indexed by `tools/doc_paths.py`,
and stay small (≤ 120 lines each).

| Shard | Use when the task is about |
| --- | --- |
| `README.md` | routing only — start here |
| `flash.md` | firmware, hardware, MRAC, EKF, safety, flight tuning |
| `thesis.md` | the research thesis: claims, math, sim benchmarks, citations |
| `learning.md` | meta-learning: mental models, Q&A notes, things worth keeping |
| `inbox/<task-id>.md` | discovered out-of-scope items filed mid-task (never edited in place after the task closes) |

## Rules

1. **One shard per task.** Do not auto-load two shards; pick the closest
   and load it via the router.
2. **Inbox only for out-of-scope.** In-task findings belong in
   `docs/agent/HANDOFF.md` (stream `main`) or `docs/agent/streams/<name>.md`
   (other streams). Cross-shard findings go in `inbox/<task-id>.md`.
3. **Mechanical boundary enforcement.** The `Allowed-Paths:` line in the
   stream page + `.githooks/pre-push` (or `tools/agent_push_guard.py`)
   enforce scope. Tombstone sections and concept-to-artifact headers are
   verified by `tools/doc_paths.py` and never silently rot.
4. **Never edit another shard's section.** Cross-cutting rules go in
   `AGENTS.md`; this directory only adds domain detail.
