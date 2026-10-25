<!--
License & Source Attribution (AGPL-3.0):
Adapted from amosblomqvist/learnharness (https://github.com/amosblomqvist/learnharness, GNU AGPL-3.0).
Source files: src/agent/prompts/practice/00-base.md, src/agent/prompts/practice/10-doctrine.md
-->

# Custom Practice Problem Sets (`docs/learning/harness/data/problems/`)

The `/learn practice` harness supports dropping custom problem files directly into `docs/learning/harness/data/problems/` in either **JSON (`.json`)** or **Markdown (`.md`)** format.

Per the practice doctrine (`src/agent/prompts/practice/10-doctrine.md`), every practice problem targets one or more proposition nodes from `data/graph/nodes.jsonl` and explicitly decomposes its difficulty along two axes:

$$\text{difficulty} = \text{ideas\_combined} + \text{cue\_familiarity}$$

where:
- **`ideas_combined`** ($\ge 1$): Number of distinct propositions or reasoning steps that must be chained together to solve the problem.
- **`cue_familiarity`** ($0\text{–}3$): How disguised or unfamiliar the surface cues are relative to the lesson context (`0` = direct textbook cue, `1` = lightly varied context, `2` = disguised flight-log symptom, `3` = cross-domain or multi-symptom synthesis).

---

## 1. Required Problem Fields

| Field | Type | Description |
|---|---|---|
| `id` | `string` | Unique problem slug (e.g., `"prob_hover_tilt_01"`). |
| `title` | `string` | Short descriptive title for the viewer's Practice screen. |
| `statement` | `string` | Full problem prompt (supports Markdown, LaTeX `$...$`, and bilingual terms). |
| `nodes` | `string[]` | Proposition node IDs exercised by this problem (used to update `coverage.jsonl`). |
| `ideas_combined` | `integer` | Count of propositions combined ($\ge 1$). |
| `cue_familiarity` | `integer` | Surface cue unfamiliarity score ($0$ = familiar, $1\text{–}3$ = increasingly novel). |
| `difficulty` | `integer` | Total difficulty score (`ideas_combined + cue_familiarity`). |
| `hints` | `string[]` | Ordered coarse-to-fine hint ladder (`R0` attention redirect $\to$ `R3` named relation $\to$ `R5` setup). |
| `solution` | `string` | Complete worked reference solution with final number + unit. |
| `track` | `string` | `"internship"`, `"thesis"`, or `"general"`. |

---

## 2. JSON Format (`*.json`)

A JSON problem file contains either a top-level array of problem objects or `{"problems": [...]}`:

```json
[
  {
    "id": "prob_hover_tilt_01",
    "title": "Tilted Quadrotor Horizontal Acceleration",
    "statement": "A quadrotor of mass $m = 2.0\\text{ kg}$ hovers at constant altitude while tilted at $\\theta = 20^\\circ$ ($g = 9.81\\text{ m/s}^2$). Compute the total thrust $T$ and horizontal acceleration $a_h$ with units.",
    "nodes": ["n_thrust_decomp", "n_tilt_accel"],
    "ideas_combined": 2,
    "cue_familiarity": 1,
    "difficulty": 3,
    "hints": [
      "Holding constant altitude means the vertical component of thrust balances gravity.",
      "Write vertical balance T * cos(theta) = m * g first, then substitute into T * sin(theta) = m * a_h.",
      "Eliminate T to get a_h = g * tan(theta); remember to convert 20 deg when evaluating trig."
    ],
    "solution": "Vertical balance: $T \\cos 20^\\circ = mg = 19.62\\text{ N} \\Rightarrow T = 20.88\\text{ N}$. Horizontal: $a_h = g \\tan 20^\\circ = 3.57\\text{ m/s}^2$.",
    "track": "internship"
  }
]
```

---

## 3. Markdown Format (`*.md`)

You can also drop a `.md` file with YAML frontmatter (or `## Problem: <id>` sections):

```markdown
---
id: prob_cascade_order_01
title: Cascaded PID Inner vs Outer Bandwidth
nodes: [n_cascade_hierarchy, n_inner_bandwidth]
ideas_combined: 2
cue_familiarity: 1
difficulty: 3
track: internship
hints:
  - Which loop commands the actuators directly, and which loop generates the attitude reference?
  - State the relationship in Chinese using the pattern A是B的C.
---

## Statement
Explain why the angular rate loop (角速度环 `jiǎo sù dù huán`) must be tuned before and run faster than the attitude loop (姿态环 `zī tài huán`), and write the hierarchy sentence in Chinese (`A是B的C`).

## Solution
The outer attitude loop outputs a desired angular rate $\omega_{\text{des}}$ that assumes the inner rate loop tracks nearly instantaneously ($\tau_{\text{inner}} \ll \tau_{\text{outer}}$). In Chinese: **角速度环是姿态环的内环，姿态环是速度环的内环。先调内环。**
```
