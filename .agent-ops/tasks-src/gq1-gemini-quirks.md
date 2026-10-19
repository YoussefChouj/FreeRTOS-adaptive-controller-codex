# Task gq1: research Gemini code-generation quirks and write a guardrails block

Read `.agent-ops/tasks-src/vps-common.md` first; its rules apply. This is a RESEARCH task: no code changes.
Digest: `.agent-ops/out/gq1.md`. Main output: `.agent-ops/research/gemini_codegen_quirks.md`. Commit both on your branch.

## Goal
A sourced catalogue of how Gemini models (2.5 Pro/Flash, 3.x Pro/Flash) go wrong when writing Python code as
autonomous agents, and one pasteable "Gemini guardrails" block (<= 30 lines) that a supervisor adds to worker briefs.

## What to find (answer each; write "no source found" rather than guess)
1. Google's OFFICIAL prompting guidance for Gemini 3 / 2.5 on coding and agentic tasks: instruction placement
   (before/after context), verbosity, temperature, thinking level, how to state constraints, known "do / don't".
   Primary sources only: ai.google.dev, cloud.google.com/vertex-ai docs, developers.googleblog.com,
   github.com/google-gemini (gemini-cli issues/docs).
2. Reported failure modes, each with evidence. At least these: rewriting or deleting unrelated code; placeholder
   stubs ("... rest of code unchanged", `pass`, TODO); hallucinated APIs, kwargs or imports (numpy/scipy/pandas);
   ignoring explicit constraints ("do not modify X"); claiming tests pass without running them; editing or
   weakening tests to make them pass; broad `try/except: pass` that hides errors; loops / repeated edits;
   overlong answers; silently changing public signatures or data contracts; numerical mistakes (off-by-one
   windows, wrong axis, NaN handling, float==). Sources: gemini-cli GitHub issues, Google AI developer forum,
   arXiv papers that evaluate Gemini on code (SWE-bench, LiveCodeBench, empirical error taxonomies).
3. Empirical papers on LLM code-generation error taxonomies (any model) that give category frequencies.
4. Concrete mitigations that were shown to help (explicit file allow-lists, interface-first, tests-first,
   "return full function" rules, small diffs, self-check steps), with the source.

## Evidence rules (strict)
- Every claim: a verbatim quote of <= 25 words in quotes, plus the FULL canonical URL.
- NEVER cite `vertexaisearch.cloud.google.com/grounding-api-redirect/...` or any redirect URL. Open the page and
  cite its real URL. If you cannot get the real URL, drop the claim.
- Mark each item `[official]`, `[paper]`, `[issue/forum]` or `[blog]`. Blogs count as weak evidence.
- Do not invent paper titles, arXiv ids, issue numbers or percentages. If you did not open it, do not cite it.

## Output format for `.agent-ops/research/gemini_codegen_quirks.md`
1. Table: `# | quirk | evidence (quote + URL + tag) | mitigation (one imperative line for a brief)`; >= 12 rows.
2. Section "Official prompting guidance": bullet list, each with quote + URL.
3. Section "Guardrails block": a fenced markdown block <= 30 lines of imperative rules for a Python worker brief.
4. Section "Sources": deduplicated URL list with tag.

## Verification
- `grep -c "http" .agent-ops/research/gemini_codegen_quirks.md` -> >= 15
- `grep -c "vertexaisearch" .agent-ops/research/gemini_codegen_quirks.md` -> 0
- Digest `.agent-ops/out/gq1.md` <= 30 lines: STATUS, row count, source count by tag, what you could not find.

## Out of scope
All repo code, firmware, tests, dashboards. Do not edit any file except the two outputs above.
