<!--
License & Source Attribution (AGPL-3.0):
Adapted from amosblomqvist/learnharness (https://github.com/amosblomqvist/learnharness, GNU AGPL-3.0).
Source files: src/agent/subagents/researcher.ts, src/agent/prompts/teacher/40-jobs.md
-->

# Helper Job Brief: `research` — Background Fact & Literature Verification

- **Job ID**: `{{JOB_ID}}`
- **Session ID**: `{{SESSION_ID}}`
- **Topic / Question**: `{{TOPIC}}`
- **Context / Goal**: `{{GOAL}}`
- **Output Path**: `{{OUTPUT_PATH}}`
- **Default Worker Mode**: `argon` (per `docs/learning/harness/data/settings.json` -> `helper_worker`)

## 1. Mission & Doctrine (`src/agent/subagents/researcher.ts`)

Verify uncertain facts, domain conventions, equations, or primary literature before or while teaching so that zero hallucinated claims enter the learner's mental model. Never offload pure arithmetic or internal Socratic reasoning to a research job—use this strictly for factual, documentary, or codebase verification.

## 2. Workflow

1. **Decompose into 2–4 Specific Facets**: Break `{{TOPIC}}` into verifiable technical questions (primary source definitions, governing equations, hardware/firmware parameters, or benchmark bounds).
2. **Search & Triage**:
   - Check local repository docs (`docs/research/`, `docs/wiki/`, firmware sources) and primary references first.
   - When web search is needed, query authoritative academic or vendor documentation; discard low-signal secondary summaries.
3. **Synthesize Standalone Digest**: Write a concise Markdown digest to `{{OUTPUT_PATH}}` containing the 4 mandatory sections below.

## 3. Required Output Structure (`{{OUTPUT_PATH}}`)

```markdown
# Research Digest: {{TOPIC}} (Job {{JOB_ID}})

## Summary
2–3 sentences stating the verified answer directly.

## Findings
1. **<Claim / Formula / Parameter>** — Detailed verification with inline citation (`file:line` or `[Source](url)`).
2. ...

## Sources
- **Kept**: List authoritative sources used and why.
- **Dropped**: List noisy or conflicting sources excluded and why.

## Gaps
Any remaining unverified edge cases or assumptions the teacher must flag explicitly.
```

## 4. Execution & Acceptance Criteria (`inline` vs. `argon`)

- **Inline Mode (`helper_worker=inline`)**:
  - Execute the search and write `{{OUTPUT_PATH}}` directly within the current session.
  - Complete the job via: `python tools/learn/learn.py job done {{JOB_ID}} --output "{{OUTPUT_PATH}}"`
- **Argon Mode (`helper_worker=argon`)**:
  - `python tools/learn/learn.py job new --kind research --brief-file <path>` writes `.agent-ops/briefs/learn-job-{{JOB_ID}}.md` and prints:
    `.agent-ops/argon.ps1 -Id learn-{{JOB_ID}} -Brief .agent-ops/briefs/learn-job-{{JOB_ID}}.md`
  - Never invoke Argon automatically; hand the command to the operator and continue teaching around the pending verification.
- **Acceptance**: `{{OUTPUT_PATH}}` exists, is non-empty, contains all 4 sections (`Summary`, `Findings`, `Sources`, `Gaps`), and `learn.py job show {{JOB_ID}}` reports status `done`.
