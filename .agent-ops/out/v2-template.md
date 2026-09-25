STATUS: done

FILES CHANGED:
- .agent-ops/tasks-src/TEMPLATE.md: Created supervisor task brief fill-in template (<45 lines) with required sections and model routing note.

VERIFICATION:
- `wc -l .agent-ops/tasks-src/TEMPLATE.md`: 36 lines (pass, < 45 lines).
- Checked required sections present: Goal, Context pointers, Constraints, Deliverables, Verification, Out of scope (pass).
- Checked model routing note contents: agy:gemini-3.1-pro-high, agy:gemini-3.8-flash-high, agy:gemini-3.8-flash-low, qwen, free, agy:gemini-3.8-flash-high,qwen (pass).
- `git status --porcelain .agent-ops/tasks-src/`: only TEMPLATE.md untracked; no other files touched (pass).

OPEN QUESTIONS / RISKS:
- None. Template follows conventions from STANDING-RULES.md, OVERNIGHT.md, and recent task briefs.
