# Task p2-a1-nb: Digest the operator's 5 adaptive-control notebooks into one cited reference

<!-- Model: gemini-3.1-pro-high on local WSL agy (notebooks are untracked, local only). -->

## Goal
`.agent-ops/research/nb_digest.md` exists. It states exactly which plant models, controllers, adaptation laws, regressors and tuning values the 5 notebooks contain. Every claim cites `nbX cell N`, and the digest lists what our simulator does not implement yet.

## Context pointers
- Notebooks, stripped to text (outputs and images removed; each cell starts with `# %% [MD|CODE cell i]`):
  - `.agent-ops/research/nb/nb1_tutorial1.txt` (Adaptive_Control_Tutorial)
  - `.agent-ops/research/nb/nb2_tutorial2.txt` (Adaptive_Control_Tutorial_2)
  - `.agent-ops/research/nb/nb3_rpy_pid_mrac.txt` (Roll_Pitch_Yaw_PID_MRAC, 3-DOF, inner-loop MRAC)
  - `.agent-ops/research/nb/nb4_direct_mrac_ff_proj_v1.txt` and `nb5_direct_mrac_ff_proj_v2.txt`. These are the v1 and v2 of "Direct MRAC + FF controller + Projection operator". They are large (~7.5k lines) and near-duplicates.
- The operator warns that the notebooks may conflict, are poorly structured and are big. Read them in chunks, e.g. `sed -n` on 400-800 lines at a time. Diff nb4 and nb5 with `python -c "import difflib..."` or `diff`; do not read both in full.
- Our simulator, for the "not implemented yet" comparison (read only):
  - `sim/adaptive_compare/`
  - `sim/bench/ctrl_mrac.py`, `sim/bench/ctrl_mrac3l.py`, `sim/bench/ctrl_mrac_b.py`, `sim/bench/ctrl_l1.py`, `sim/bench/ctrl_hybrid.py`
- The MRAC in these notebooks follows Tansel Yucelen's lectures (USF / LACIS). Name the lecture concepts where the notebooks use them.

## Constraints
- Read-only everywhere. The only files you may create are the two deliverables below.
- Do not touch firmware (`API/ TASK/ BSP/ USER/ Global_file/ FreeRTOS/ stm32_lib/ OBJ/`). No builds, no flashing, no probe, no port 8081, no UDP.
- Foreground commands only. Do not commit.
- Do not invent anything. If a notebook is ambiguous or contradicts itself, quote the lines and say so. Do not resolve it silently.

## Deliverables
1. `.agent-ops/research/nb_digest.md`, at most 450 lines, with these sections:
   1. **Per notebook** (nb1, nb2, nb3, and nb4/5 together):
      - purpose
      - plant model: states, equations, parameter values with units, and the discretisation/dt
      - reference model (A_m, B_m, poles, bandwidth)
      - controllers: PID cascade, MRAC form (direct/indirect, state/output feedback, integral augmentation), feedforward form
      - adaptation laws as exact equations: Gamma, P/Lyapunov (Q), sigma/e-mod/projection with the projection bounds and epsilon, dead-zone, normalisation
      - regressor/basis: physical terms, RBF centres/widths/count, bias term
      - disturbance/uncertainty models tested
      - tuning values
      - any sysID or estimation
      - any claimed results (numbers) and the conditions they hold under
   2. **nb4 vs nb5:** what changed from v1 to v2, as a table of change / cells / effect.
   3. **Conflicts:** contradictions between notebooks, or between the notebooks and Yucelen's standard forms, e.g. sign conventions, different Gamma/P, projection defined differently.
   4. **Ideas not yet in our sim:** each item gives notebook+cell, one line on the mechanism, and the closest file in `sim/bench/` or "none". Put these first: performance-recovery / low-frequency learning terms, set-theoretic or error-bounding terms, CRM (closed-loop reference model), projection variants, FF structure, and any physical-feature or RBF regressor.
   5. **Operator intent:** what the operator seems to have been converging towards across the versions. Quote the markdown cells, at most 15 words per quote.
2. `.agent-ops/out/p2-a1-nb.md`, a digest of at most 30 lines: STATUS, the file written with its line count, the top 8 findings, and the biggest open questions.

## Verification
- `wc -l .agent-ops/research/nb_digest.md` -> at most 450.
- `grep -c "cell " .agent-ops/research/nb_digest.md` -> at least 40 cell citations.
- `git status --porcelain -- sim API TASK BSP USER` -> empty (you changed nothing).

## Out of scope
- Running notebooks, installing packages, writing controller code, editing any other file.
