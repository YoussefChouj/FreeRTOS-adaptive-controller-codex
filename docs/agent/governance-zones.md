# Governance zones (machine-checked)

A compact summary of where an agent may edit without operator review vs.
where an operator must look at the diff. The mechanical split lives in
`tools/agent_push_guard.py`; this file is the rule it implements, so
humans and agents agree on it.

| Zone | Coverage | Pre-push gate | Override |
| --- | --- | --- | --- |
| **Red** — flight-critical | `API/pid.c`, `API/mrac.c`, `API/mrac_math.c`, `API/ekf.c`, `API/ekf_of.c`, `API/rc_input.c`, `API/land_profile.h`, `API/land_gate.c`, `API/land_xy_gate.c`, `API/prearm.c`, `TASK/StabilizerTask.c`, `TASK/AutoflyTask.c` | Always refused unless `TIER0_OVERRIDE=1` is exported. Stream page's `Allowed-Paths:` is irrelevant for these paths. | Operator sets `TIER0_OVERRIDE=1` after reviewing the diff |
| **Yellow** — core control / firmware / sim | The rest of `API/`, `TASK/`, `USER/`, `sim/bench/`, `ground_station/livewatch/`, `ground_station/comm/`, `ground_station/flashtool/`, `ground_station/analysis/`, `OBJ/JX_FLY.axf` | Diff must match `Allowed-Paths:` on the stream page; `tools/check.sh` must show `CHECK PASS` | Standard PR review |
| **Green** — sandbox | `docs/context/inbox/`, `docs/context/*.md` (shard edits that do not change a tombstone or invariant), `docs/agent/HANDOFF.md` (stream-page refresh), `.agent_memory/`, `.agent_state/`, `logs/` (gitignored) | None beyond `tools/check.sh doc-paths` | n/a |

## Why this matters

`tools/agent_push_guard.py` is the *only* thing that knows whether a
diff belongs to the current task. A prose rule ("don't touch the EKF")
is rubber-stamped under context pressure; a `git diff --name-only
origin/main...HEAD` against `API/ekf.c` is not. The list above is the
source of truth; update it only when the policy genuinely changes.

## How to extend the Red list

1. Add the path in `tools/agent_push_guard.py` `TIER0 = [...]`.
2. Add a one-line note here with the date and the operator instruction
   that prompted it. **Do not** edit the prose in `AGENTS.md` without
   mirroring the change here — the prose is descriptive; this file is
   normative.
