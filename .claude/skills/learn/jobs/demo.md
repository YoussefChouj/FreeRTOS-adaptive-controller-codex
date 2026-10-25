<!--
License & Source Attribution (AGPL-3.0):
Adapted from amosblomqvist/learnharness (https://github.com/amosblomqvist/learnharness, GNU AGPL-3.0).
Source files: src/agent/subagents/demo-builder.ts, src/agent/prompts/teacher/50-demos.md
-->

# Helper Job Brief: `demo` — Self-Contained Interactive HTML Teaching Instrument

- **Job ID**: `{{JOB_ID}}`
- **Session ID**: `{{SESSION_ID}}`
- **Demo Title / Concept**: `{{TOPIC}}`
- **Simulation Specification / Goal**: `{{GOAL}}`
- **Output Path**: `{{OUTPUT_PATH}}` (under `docs/learning/harness/data/demos/`)
- **Default Worker Mode**: `argon` (per `docs/learning/harness/data/settings.json` -> `helper_worker`)

## 1. Mission & Doctrine (`src/agent/subagents/demo-builder.ts`, `prompts/teacher/50-demos.md`)

*"If the concept is dynamic — a parameter to sweep, a process to animate, a distribution to sample, a system to simulate — build the learner the instrument rather than describing its behavior in prose. Ten seconds of dragging a slider teaches more than a paragraph."*

Every demo must be a **single self-contained `.html` file** with zero external JS/CSS dependencies so it works offline inside the viewer's sandboxed iframe (`http://127.0.0.1:8770/api/demo/<id>`) and can be opened directly in any browser.

## 2. Design & Engineering Requirements

1. **Self-Contained Single File**: Complete `<!DOCTYPE html><html>...</html>` with inline `<style>`, `<canvas>` or `<svg>`, and `<script>`. Do not use `localStorage`, `sessionStorage`, or `document.cookie` (sandbox-safe).
2. **Dark-Mode First + Theme Toggle**: Default to a clean dark palette (`#0f141c` background, `#e6edf3` text) with a built-in Light/Invert toggle button (`invert-toggle`) to prevent bright white flashbangs.
3. **Interactive Parameter Sliders & Presets**:
   - Every swept parameter must have an `<input type="range">` with a live numeric + unit readout and preset buttons illustrating key regimes (e.g., Overdamped, Critically Damped, Underdamped, Unstable, or Disturbance step).
4. **Live Quantitative Readouts & Bilingual Labels**:
   - Compute and display key metrics in real time (e.g., rise time $t_r$, time constant $\tau$, steady-state value $y_{\text{ss}}$, steady-state error $e_{\text{ss}}$, overshoot $M_p$).
   - Include Chinese + pinyin terms alongside English labels when relevant to the `internship` track.

## 3. Verification Gate (`run_demo` Equivalent)

- Before marking the job done, verify that:
  1. The file parses and executes with **zero JS syntax or runtime errors**.
  2. Every slider `oninput` handler redraws the canvas/SVG and updates the numeric readouts immediately.
  3. The file is saved under `docs/learning/harness/data/demos/` at `{{OUTPUT_PATH}}`.

## 4. Execution & Acceptance Criteria (`inline` vs. `argon`)

- **Argon Mode (`helper_worker=argon`, default)**:
  - `python tools/learn/learn.py job new --kind demo --brief-file <path>` writes `.agent-ops/briefs/learn-job-{{JOB_ID}}.md` and outputs:
    `.agent-ops/argon.ps1 -Id learn-{{JOB_ID}} -Brief .agent-ops/briefs/learn-job-{{JOB_ID}}.md`
  - Never invoke Argon directly; hand the command to the operator or run inline when instructed.
- **Inline Mode (`helper_worker=inline`)**:
  - Author `{{OUTPUT_PATH}}`, verify that it renders cleanly, and run:
    `python tools/learn/learn.py job done {{JOB_ID}} --output "{{OUTPUT_PATH}}"`
- **Acceptance**: `{{OUTPUT_PATH}}` is a self-contained HTML file that renders a live interactive plot with sliders and metrics, and `learn.py job show {{JOB_ID}}` (plus `/api/demo/<id>`) serves it with HTTP 200.
