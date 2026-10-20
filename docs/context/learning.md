# Context shard: Personal learning ledger

Use this shard for *meta* tasks: mental models, "why is the agent doing X",
preferences, and things you want *remembered about how you work*, not about
the project itself.

This shard is yours. Agents may suggest entries and ask you to confirm;
you decide what gets added. Nothing here is enforced by `tools/check.sh`.

## Concept-to-Artifact Entry Points (verified by tools/doc_paths.py)

| Concept | Where in the repo |
| --- | --- |
| The repo's agent-facing rules (single source for global rules) | `AGENTS.md` |
| Active task boundary (what "now" means) | `docs/agent/HANDOFF.md` (stream `main`) or `docs/agent/streams/<name>.md` |
| Your durable preferences, parsimoniously | this file |
| The manager-agent model when you paused for review | `.claude_state.md` (stub; archived in `docs/agent/ledger/`) |

## Standing preferences

- **Strategic globally, tactical locally.** Persist cross-agent truth to
  files other agents actually read. Pick the smallest locally-verifiable step.
- **Use prose checklists only as reference; rely on mechanical gates.**
  Under context pressure, agents rubber-stamp prose; the repo's mechanical
  gates (`tools/check.sh`, `.githooks/`, GitHub branch protection) cannot
  rubber-stamp.
- **When unblocking locally introduces a violation globally, stop.** Append
  the contradiction to `docs/context/inbox/<id>.md` and report. Do not
  override a Constraint unilaterally.
- **One task per branch, squash-merge to main.** Negative results go in the
  tombstone sections of the relevant shard, not in the commit history.

## When this shard does not apply

If the task is about firmware, MRAC, EKF, or hardware, use `flash.md`. For
thesis claims, math, or benchmarks, use `thesis.md`. If you do not know
which one, start at the router (`README.md`).

## How to add an entry here

Append a dated bullet under `## Standing preferences` or a new section.
Do not rewrite history; corrections become new entries that supersede old
ones. If the new entry is a project-wide rule, also propose an update to
`AGENTS.md` in the same commit.
