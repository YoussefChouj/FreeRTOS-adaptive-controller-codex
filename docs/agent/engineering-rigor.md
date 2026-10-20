# Engineering rigor: six skills, run as gates and coached

Source: Brian Douglas, MATLAB Tech Talk "6 Engineering Skills That AI Won't Replace" (operator, 2026-10-20).
The operator's long-term goal is to own these skills for the thesis and the job market. Every agent on this
repo (Claude, Argon, Jules, Codex) does two jobs: it runs the six gates on its own work, and it coaches the
operator in them.

**When:** any change to control, estimation, adaptation, trajectories, the mixer, or an experiment design
(`TASK/`, `API/`, `NN/`, `firmware/`, `ground_station/analysis/`, `docs/lab/days/`). For pure tooling and
docs, run gates 1 and 2 only.

## The six gates

| # | Skill | What to write down (commit body, brief or PR) | Example from this project |
|---|---|---|---|
| 1 | Problem formulation | The symptom, with numbers and a log path. The hypothesis. What result would prove it wrong. No code until this is written. | 10-19 figure-8, 36.9 cm error. "Tracking lag" and "abort limit too tight" are two problems with two fixes. |
| 2 | Constraints and trade-offs | The hard limits the change touches, and what it gives up. | 120 s airborne cap, `pos_err_m` 0.50 m, slow-lane `PLANNING_BUDGET_BPS`, task stacks (drift NN 384 words), tilt trip, pack gate. Bandwidth costs noise, and noise costs latency. |
| 3 | Control-theory anti-patterns | Reject these, or justify them: an unstable pole-zero cancellation; an adaptive law with no projection or leakage (MRAC Theta drift); raw differentiation of a noisy signal (use a filtered D, on the measurement); Lyapunov assumptions that the code breaks (saturation, dt jitter, rate limits, deadzones). | "Does the code do what the proof assumes?" MRAC proofs assume no actuator saturation, but the mixer saturates. |
| 4 | System coupling | Trace the change through the chain: planner → position → velocity → attitude → rate → MRAC → mixer → motors → sensors/EKF/camera fusion → back. Name the loop above and below, and their bandwidth gap (3-5x is the rule of thumb). | Velocity FF changes the position error that the MRAC adapts on. A fusion mode switch changes what the drift NN is allowed to touch. |
| 5 | Model fidelity header | For every sim, model or fake-drone result: what is modelled, what is left out, and where it breaks. | `fake_drone` models the link and the rates, not aerodynamics. A soak pass proves the pipeline, not the controller. |
| 6 | Staged V-model verification | Requirement → nominal sim → stress (noise, delay, saturation, battery sag) → sim-to-firmware parity (frames, dt, float32, units cm vs m, signs) → bench (`livewatch`) → flight with a pre-registered pass. Say which stage the evidence comes from. | A pass mark is PROPOSED until it is flown. A host test is not flight evidence. |

## Coaching mode (the operator's goal)

- On a control or design question, ask the operator one gate question first (usually gate 1 or 4) and wait for
  the answer. Then give your answer, grade theirs (right / partly / gap) and fill the gap in up to 5 lines with
  one analogy.
- One coaching question per reply at most. Skip coaching at the lab (lab time is for flying), or when the
  operator says "just do it".
- Log each break point (what they could not answer, and the fix) in `docs/agent/learning-log.md`, one line each:
  date, skill #, topic, the gap.
- Flag it when the operator's own plan skips a gate. Example: a gain change with no falsifier is a gate-1 miss.

## Checklist (paste into a brief or PR)

```text
[ ] 1 problem, numbers + log path, falsifier     [ ] 4 loops above/below named, bandwidth gap
[ ] 2 limits touched, what is given up           [ ] 5 fidelity header on every sim/model claim
[ ] 3 no anti-pattern (or justified)             [ ] 6 evidence stage named; parity if sim -> firmware
```
