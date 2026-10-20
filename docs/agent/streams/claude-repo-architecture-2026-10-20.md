# Stream claude/repo-architecture-2026-10-20 — handoff page

(Overwrite at every task boundary; small.)

Tree: main worktree | Branch: `claude/repo-architecture-2026-10-20`.
Edit only this page, never another stream's page.

## Goal

Land the 2026-10-20 repo-architecture work on top of `main`:
tracked pre-commit + pre-push git hooks; per-branch `Allowed-Paths:`
guard; context shards `docs/context/{README,flash,thesis,learning}.md`;
slimmed `AGENTS.md` (≤ 170 lines, enforced by `tools/check.sh
doc-budget`); mechanical `CHECK PASS` recording for the pre-push gate.

## Facts

- Working tree clean on `claude/repo-architecture-2026-10-20`.
- `tools/check.sh doc-paths doc-budget fw-lint row-meta` pass (subset).
- **Full gate cannot pass in this WSL/bash environment**: `gcc`,
  `clang-tidy` and the bash `python3` `pytest` module are all absent, so
  `host-tests`, `mrac-equiv`, `c-pytest`, `sil-smoke`, `clang-tidy` and
  `float` fail on missing tools, not on code. They fail identically on
  an unmodified checkout, so they are not caused by this branch.
  Windows Python does have pytest 9.0.3; the bash steps still need gcc.
- Pushed to `origin/claude/repo-architecture-2026-10-20` (guard: OK,
  16 files within Allowed-Paths).
- New files all in `docs/context/`, `.githooks/`, `tools/`,
  `docs/agent/`. No firmware edits.
- `tools/agent_push_guard.py` refuses to push until an
  `Allowed-Paths:` line is present (this page is the proof).

Allowed-Paths: docs/agent/streams/claude-repo-architecture-2026-10-20.md, docs/context/*, docs/context/inbox/**, .githooks/pre-commit, .githooks/pre-push, tools/check.sh, tools/install-hooks.sh, tools/agent_push_guard.py, tools/doc_paths.py, docs/agent/completion-checklist.md, docs/agent/governance-zones.md, docs/agent/HANDOFF.md, AGENTS.md

Acceptance-Cmd: bash tools/check.sh

## Next actions (3 lines, most urgent first)
1. Operator reviews the diff (`git diff main...origin/claude/repo-architecture-2026-10-20`);
   activate tracked hooks with `git config core.hooksPath .githooks` if satisfied.
2. Merge to `main` (CEO only) — squash-merge preferred; commit message
   to follow Conventional Commits. Run `bash tools/check.sh` on a machine
   that has gcc + clang-tidy first (Acceptance-Cmd).
3. Next session sets its own stream page's `Allowed-Paths:` based on
   the new governance-zones.md table.

## Do not
- Do not bypass the pre-push guard with `--no-verify` on `origin/main`.
- Do not edit `API/`, `TASK/`, or `sim/` from this branch (governance
  Red/Yellow zones require a separate stream + explicit `TIER0_OVERRIDE`).
- Do not extend `AGENTS.md` past 170 lines; move detail to a shard.

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-20 (initial)
<!-- AUTO:END -->
