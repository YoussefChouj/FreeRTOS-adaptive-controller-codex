STATUS: done
FILES CHANGED:
- .agent-ops/research/gemini_codegen_quirks.md: catalogue of Gemini failure modes, official prompting guidance, guardrails block, and sources.
- .agent-ops/out/gq1.md: task completion digest and verification summary.

METRICS:
- Row count in quirks table: 15 rows (>= 12 required).
- Sources by tag (Section 4, 21 total): [official]: 5, [paper]: 8, [issue/forum]: 7, [blog]: 1.
- Could not find: No primary source showing positive benefit from negative constraint phrasing ("do not X"); all official docs recommend positive framing with structural delimiters.

VERIFICATION:
- grep -c "http" .agent-ops/research/gemini_codegen_quirks.md -> 45 (pass, >= 15)
- grep -c "vertexaisearch" .agent-ops/research/gemini_codegen_quirks.md -> 0 (pass, == 0)
- Guardrails block line count -> 13 lines (pass, <= 30)
- Quote word counts -> all <= 25 words (pass, 0 violations)
- HTTP 200 check across all 21 canonical URLs -> 21/21 passed (pass)

OPEN QUESTIONS / RISKS:
- Hardware integration / firmware execution: not verified (task is headless research; no hardware present).
- Behavioral variance across future Gemini point releases may require re-tuning prompt anchors.

SUBSTITUTIONS: none
