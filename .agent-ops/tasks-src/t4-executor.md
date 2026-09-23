# Task T4: the experiment executor and workflow library (ground side only)

Read docs/research-platform/SPEC.md; it is binding. It builds on ground_station/research (T2 Run/store/analysis, T3 sim).
Rules: NO firmware edits (the firmware Simplex is a separate operator-reviewed task), no probe, no flashing, no contact with 127.0.0.1:8081.

## Find first
How parameters are written and captures started today:
- ground_station/service agent actions: `/api/agent/actions` registry and plans (grep `register_action`, `run_plan`);
- ground_station/livewatch/capture_preset;
- the approval queue and tier-0 logic.

The executor must go THROUGH those existing paths (plans and approvals), never around them.

## Build (ground_station/research/)
1. `workflow.py`: a YAML schema for workflows and a loader/validator.
   - Steps (typed): `set_params`, `fly_trajectory`, `capture`, `wait_until`, `analyze`, `revert`, `note`, and `call` (composes another workflow by name).
   - An experiment spec has: hypothesis, phase, variant, changes, trajectory, capture, envelope (state bounds and adaptive-health bounds, with `mode: enforce|observe_only`), revert: always.
   - Unknown step types are a validation error.
2. `trajectories.py`: presets (step, doublet, chirp, multisine, figure8), parametric families, and excitation overlays.
   - A feasibility check against a rig profile: `fixture_4dof` (roll/pitch ±40°, yaw free, z 0.42–0.65 m, no x/y) or `free_flight`.
   - Output: a time series of references.
3. `executor.py`: runs a workflow through a `Backend` interface.
   - Backends:
     - `SimBackend` (T3);
     - `DashboardBackend`, which builds agent plans for the service's plan and approval path. Its HTTP client is injectable. In tests use a fake; there must be no live calls.
   - The executor ALWAYS runs `revert` (try/finally).
   - It evaluates the envelope on streamed state:
     - `enforce`: abort, revert, and record the event;
     - `observe_only`: record the would-be trip only.
   - It writes a Run with every event. Rule: a workflow must pass `dry_run` in sim before `DashboardBackend` will execute it; store the sim Run id in the hardware Run.
4. `campaign.py` (L3): an operator-approved envelope over parameters (ranges plus a step budget). It proposes the next point (grid, then a simple Bayesian or successive-halving option), stays inside the envelope, and stops at the budget.
5. `workflows/`: starter library YAML:
   - `pid_baseline_step.yaml`;
   - `chirp_sysid_roll.yaml`;
   - `mrac_ab_gate_compare.yaml` (uses the runtime variant parameter; mark it TODO-firmware);
   - `validate_new_feature.yaml`.
6. CLI subcommands: `workflow validate|dryrun|run`, `campaign plan`.
7. Tests:
   - schema rejection;
   - revert runs even when a step raises;
   - an enforce envelope aborts and an observe_only envelope does not;
   - hardware refused without a sim pass;
   - the campaign never leaves its envelope and respects its budget;
   - trajectory feasibility rejects 50° roll on the fixture.

## Acceptance
- The full tree is green (paste the last line).
- Append a "## T4 as built" section (at most 20 lines) to the SPEC.
- Commit on your branch; the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Workspace rule (hard)
Work ONLY inside your own worktree (your starting cwd, under .worktrees/<id>). Never edit files in the main checkout. Commit on your branch there.


## First command (hard)
Run: `cd "$(git rev-parse --show-toplevel 2>/dev/null)"; pwd`. Then cd to the worktree path given at the bottom of this task (under .worktrees/), and run `git rev-parse --show-toplevel`. It MUST end in .worktrees/<id>. Repeat that check right before `git commit`. If it prints the main checkout, STOP and cd back to the worktree. Edits or commits in the main checkout are a task failure.

