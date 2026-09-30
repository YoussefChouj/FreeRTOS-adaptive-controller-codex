# Workflow B build plan

Spec (binding): `.agent-ops/grill-autonomous-flight-loop.md` (Q1-Q12 decisions, shared-understanding summary,
operator confirmation + additions A1-A4 of 2026-09-30). Facts: `docs/workflow-b/facts-firmware.md`,
`docs/workflow-b/facts-gs.md`. Wire contract: `docs/workflow-b/interfaces.md` (written before Task 1; any change
to it is a plan change, ledgered).

## Global constraints (every task)

- G1 Firmware C is Keil ARMCC V5.06 C89: declarations at block top, no VLAs, no `//`-only C99 constructs beyond
  what the file already uses, match surrounding style. Tunable sets use the `*_ROW` table pattern
  (`docs/firmware-table-pattern.md`, reference `API/pid.c`).
- G2 New firmware logic goes in small pure-C modules (no FreeRTOS/HAL includes) with host unit tests compiled by
  gcc; the RTOS files only call them. Keil build must end with 0 errors, 0 new warnings; report RW+ZI of main
  SRAM and CCM from `OBJ/JX_FLY.map` before and after.
- G3 Never write a number as measured unless measured in this task; thresholds are PROPOSED constants in one
  table with a comment saying so.
- G4 Protected set (Q10b) is changed only by the tasks that are explicitly allowed to (Tasks 1-2); each protected
  region in a mixed file is wrapped in `/* PROTECTED BEGIN <name> */ ... /* PROTECTED END <name> */`.
- G5 No flying, arming, motor spin, flashing, or POSTs to the live service (port 8081) by any worker. Tests use
  fakes. The supervisor alone flashes.
- G6 Python: match the existing package style; pytest for every new module; run only the changed modules' tests
  plus the harnesses they touch (not the full tree). JS panels: extend the existing harness pattern.
- G7 Commit only your own files with explicit paths; message ends with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never commit OBJ/ build products.
- G8 Units: firmware position in the units the facts file states; every Python API names units in the argument
  name (`_m`, `_cm`, `_s`, `_deg`).
- G9 Minimum code for the task; nothing speculative; no duplicated logic (reuse existing resample, J, metrics,
  command sender, custody code).

## Lanes

Two lanes with disjoint file sets run in parallel, each strictly sequential inside:
- FW lane (worktree `.worktrees/wfb-fw`, branch `wfb-fw`): Tasks 1, 2, 3.
- GS lane (worktree `.worktrees/wfb`, branch `workflow-b`): Tasks 4-10. The FW lane merges into `workflow-b`
  after each reviewed task.

## Tasks

(filled in after the fact digests; see sections below)
