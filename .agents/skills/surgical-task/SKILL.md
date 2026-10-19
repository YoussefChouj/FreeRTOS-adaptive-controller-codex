---
name: surgical-task
description: Execute any feature, bugfix, or refactor using strict subagent scouting, bounded 50-line file reads, and isolated test verification to keep main context under 150k tokens.
---
# Surgical Task Execution Skill

## Instructions
1. **Phase 1 — Isolated Recon:** Do NOT read project files in the main thread. Call `invoke_subagent` targeting `codebase-scout` with the user's goal to obtain exact file paths and line ranges.
2. **Phase 2 — Surgical Slice Read:** In the main thread, read ONLY the +/- 40 line slices identified by `codebase-scout`.
3. **Phase 3 — Minimal Diff:** Apply targeted edits using `replace_file_content` on those specific line ranges.
4. **Phase 4 — Isolated Verification:** Call `invoke_subagent` targeting `test-runner` to run the relevant test or build check and return a compact pass/fail summary.