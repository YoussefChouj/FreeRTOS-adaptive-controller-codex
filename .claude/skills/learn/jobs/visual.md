<!--
License & Source Attribution (AGPL-3.0):
Adapted from amosblomqvist/learnharness (https://github.com/amosblomqvist/learnharness, GNU AGPL-3.0).
Source files: src/agent/subagents/visual-maker.ts, src/agent/prompts/teacher/50-demos.md
-->

# Helper Job Brief: `visual` — Precision SVG Figure & Geometric Diagram

- **Job ID**: `{{JOB_ID}}`
- **Session ID**: `{{SESSION_ID}}`
- **Diagram Subject**: `{{TOPIC}}`
- **Visual Specification / Goal**: `{{GOAL}}`
- **Output Path**: `{{OUTPUT_PATH}}`
- **Default Worker Mode**: `inline` (per `docs/learning/harness/data/settings.json` -> `helper_worker`)

## 1. Mission & Doctrine (`src/agent/subagents/visual-maker.ts`, `prompts/teacher/50-demos.md`)

Use inline Mermaid blocks for simple flowcharts or trees, but dispatch a `visual` job whenever teaching requires exact coordinate geometry, free-body force vectors (e.g., tilted quadrotor thrust decomposition $T\cos\theta$ vs. $T\sin\theta$), Bode/Nyquist sketches, or phase-plane trajectories.
**Core Rule**: *Verify by looking*—never deliver an SVG whose geometry, arrow directions, or label placements have not been checked for overlap and clipping.

## 2. Authoring & Verification Workflow

1. **Coordinate Planning**:
   - Define an explicit `viewBox="0 0 720 420"` (or appropriate aspect ratio) with at least `32px` padding on all sides.
   - Use legible typography (`font-family="system-ui, sans-serif"`, `font-size >= 13`), high-contrast strokes, and at most 2 semantic accent colors.
2. **Geometric Accuracy**:
   - Compute vector endpoints, angles, and curve coordinates mathematically rather than guessing pixel positions.
   - Place text labels offset from line segments and arrowheads so zero characters collide with strokes.
3. **Visual Inspection Gate**:
   - Inspect the rendered SVG (in the viewer or headless check) to verify that all `<text>`, `<path>`, and `<marker>` elements are inside the `viewBox` and visually balanced.

## 3. Required Deliverables

- Save the self-contained `.svg` (or `.html` wrapper with inline `<svg>`) to `{{OUTPUT_PATH}}`.
- Record 1–2 sentences of teacher notes describing how to walk the learner through the figure.

## 4. Execution & Acceptance Criteria (`inline` vs. `argon`)

- **Inline Mode (`helper_worker=inline`, default)**:
  - Write the verified SVG to `{{OUTPUT_PATH}}` and mark complete via:
    `python tools/learn/learn.py job done {{JOB_ID}} --output "{{OUTPUT_PATH}}"`
- **Argon Mode (`helper_worker=argon`)**:
  - Writes `.agent-ops/briefs/learn-job-{{JOB_ID}}.md` and prints `.agent-ops/argon.ps1 -Id learn-{{JOB_ID}} -Brief .agent-ops/briefs/learn-job-{{JOB_ID}}.md` for the operator.
- **Acceptance**: `{{OUTPUT_PATH}}` is valid XML/SVG with an explicit `viewBox`, zero overlapping labels, and `learn.py job show {{JOB_ID}}` reports `done`.
