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
| `inbox/friction-<slug>.md` | environment or workflow friction found mid-task (see below) |
| `inbox/<task-id>.md` | discovered out-of-scope items filed mid-task (never edited in place after the task closes) |

## Friction-to-immunity

Friction is a first-class artifact here, not a footnote in a stream note. File it the moment it costs you a retry,
in `docs/context/inbox/friction-<slug>.md`:

```
Trigger:       what failed or felt slow, verbatim enough to grep for
Token/time:    how many tool calls or minutes it wasted
Mechanical cure: the script, hook or config that must change so it cannot recur
Cure status:   filed | done (with the commit)
```

Agent-discovered (a command failed for a non-logic reason: a missing tool, a
path, a line ending) and operator-noticed (too many turns, wrong file,
redundant questions) both count. The last line is the point: a friction file
whose cure is still `filed` is an open item, and the cure is usually smaller
than the retry loop it replaces.

The machinery that turns friction into immunity:

| Friction | Cure |
| --- | --- |
| a step fails because a tool is absent | exit 77 = SKIP (`tools/check.sh`) |
| bash tripping over CRLF | `*.sh text eol=lf` in `.gitattributes` |

## Rules

1. **One shard per task.** Do not auto-load two shards; pick the closest
   and load it via the router.
2. **Inbox only for out-of-scope.** In-task findings belong in
   `docs/agent/HANDOFF.md` (stream `main`) or `docs/agent/streams/<name>.md`
   (other streams). Cross-shard findings go in `inbox/<task-id>.md`.
3. **Mechanical boundary enforcement.** Stream-page scope enforcement
   arrives with the gate PR; until then scope is a review check. Tombstone sections and concept-to-artifact headers are
   verified by `tools/doc_paths.py` and never silently rot.
4. **Never edit another shard's section.** Cross-cutting rules go in
   `AGENTS.md`; this directory only adds domain detail.
