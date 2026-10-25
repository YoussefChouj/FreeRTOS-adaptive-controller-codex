<!--
License & Source Attribution (AGPL-3.0):
Adapted from amosblomqvist/learnharness (https://github.com/amosblomqvist/learnharness, GNU AGPL-3.0).
Source files: src/agent/subagents/pdf-reader.ts, src/agent/prompts/teacher/40-jobs.md
-->

# Helper Job Brief: `pdf` — Structured PDF Triage & Page-Cited Extraction

- **Job ID**: `{{JOB_ID}}`
- **Session ID**: `{{SESSION_ID}}`
- **PDF Path & Question**: `{{TOPIC}}`
- **Context / Goal**: `{{GOAL}}`
- **Output Path**: `{{OUTPUT_PATH}}`
- **Default Worker Mode**: `inline` (per `docs/learning/harness/data/settings.json` -> `helper_worker`)

## 1. Mission & Doctrine (`src/agent/subagents/pdf-reader.ts`)

Read and extract equations, theorems, diagrams, and technical claims from a target PDF without flooding the teacher's context window. Every extracted statement must carry an explicit page citation `(p. N)`.

## 2. Size-Adaptive Workflow

1. **Triage First**: Determine total page count, section headings, equation density, and figure locations before reading full pages.
2. **Select Reading Strategy by Length**:
   - **Short PDF ($\le 10$ pages)**: Extract text across all pages; inspect dense equation or figure pages visually.
   - **Medium PDF ($11–40$ pages)**: Locate relevant sections from the table of contents / headings, extract those page ranges, and inspect key figures/proofs.
   - **Long PDF ($> 40$ pages)**: Search for keywords/symbols first, then extract a $\pm 1$ page window around the highest-signal hits.
3. **Unread Gate**: Never mark a PDF job complete without consulting at least one concrete page and recording both `Pages consulted` and `Pages read`.

## 3. Required Output Structure (`{{OUTPUT_PATH}}`)

```markdown
# PDF Extraction Brief: {{TOPIC}} (Job {{JOB_ID}})

## Answer & Key Equations
Concise synthesis answering `{{GOAL}}` with exact LaTeX equations and `(p. N)` citations on every claim.

## Key Definitions & Assumptions
- **Term / Symbol** `(p. N)`: Exact definition and units/frame conventions used by the authors.

## Figures & Tables Summary
- **Figure/Table X** `(p. N)`: What axes/signals are shown and the main takeaway.

Pages read: <comma-separated page numbers>
Pages consulted: <comma-separated page numbers>
```

## 4. Execution & Acceptance Criteria (`inline` vs. `argon`)

- **Inline Mode (`helper_worker=inline`, default)**:
  - Read the targeted PDF pages, write the cited brief to `{{OUTPUT_PATH}}`, and run:
    `python tools/learn/learn.py job done {{JOB_ID}} --output "{{OUTPUT_PATH}}"`
- **Argon Mode (`helper_worker=argon`)**:
  - Writes `.agent-ops/briefs/learn-job-{{JOB_ID}}.md` and prints `.agent-ops/argon.ps1 -Id learn-{{JOB_ID}} -Brief .agent-ops/briefs/learn-job-{{JOB_ID}}.md` for the operator.
- **Acceptance**: `{{OUTPUT_PATH}}` exists, includes explicit `(p. N)` citations and the trailing `Pages consulted:` line, and `learn.py job show {{JOB_ID}}` reports `done`.
