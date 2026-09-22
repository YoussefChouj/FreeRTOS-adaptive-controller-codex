# Task: Implement Agent Map Step 4 (Tier Enforcement)

## Instructions
1. Implement step 4 of the `AGENT_MAP_SPEC.md` tier enforcement in the ground station service (`ground_station/service/agent.py` or similar).
2. Read the tiers from `docs/agent-map/modules.yaml` (which is already confirmed).
3. Ensure that plans in the agent approval queue that touch a tier-0 file (e.g. `TASK/StabilizerTask.c`, `API/mrac.c`, etc.) require operator approval in EVERY autonomy mode.
4. Flag tier-1 to tier-0 data flow (e.g. if a plan wires `s_ekf` into a control path).
5. Enforce this in code. Run any relevant tests to ensure it passes.
6. Exit cleanly when done.
