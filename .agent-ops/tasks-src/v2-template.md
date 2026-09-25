# Task v2-template: brief template for remote workers

Write `.agent-ops/tasks-src/TEMPLATE.md`, a fill-in template (< 45 lines) that a supervisor copies
to brief a worker. Research what makes coding-agent task briefs succeed (read
`.agent-ops/STANDING-RULES.md`, `.agent-ops/OVERNIGHT.md` and the 5 newest files in
`.agent-ops/tasks-src/` for this project's conventions). Sections: Goal (one sentence, observable
done-condition), Context pointers (exact files/symbols, `agent_map explain` names), Constraints
(safety tier, what not to touch), Deliverables, Verification (exact commands + expected result),
Out of scope. Add a 5-line "model routing" note: agy:gemini-3.1-pro-high for design/hard
debugging; agy:gemini-3.8-flash-high default implementation; agy:gemini-3.8-flash-low lookups;
qwen mechanical edits; free last resort; typical fallback chain
`agy:gemini-3.8-flash-high,qwen`. Do not touch other files.
