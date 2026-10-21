# Stream nightly-1022 — handoff page

(Overwrite at every task boundary; small.)

Tree: main checkout | Branch: `nightly-1022` = local main after the 10-21 lab day, pushed so Jules can base PRs on it.
The operator lands it (`bash tools/land.sh nightly-1022`) in the morning; agents do not.

## Goal

The 2026-10-21 -> 10-22 autonomous nightly run: plan and status in docs/lab/open-items.md "NIGHTLY PLAN" and
.claude_state.md "Nightly progress". Jules PRs target this branch, the CEO reviews and merges them here.

Allowed-Paths: docs/**, ground_station/**, tools/**, sim/**, API/**, TASK/**, tests/**, AGENTS.md, .claude_state.md, .agent-ops/briefs/**
Tier0-Approved: API/land_profile.h, TASK/StabilizerTask.c - operator 2026-10-21 "as i hard that the manual landing i did"
Tier0-Approved: API/wfb_safety.c, API/pid.c - operator 2026-10-21 "Help me disable all the gates just leave the flight time gate and me as the manual overide supervisor for all the remaining flights" / "make it the default from now on"
Note on that line: the words were said for the 10-21 flights; the 10-22 scope and the flash are PENDING the operator's morning OK. HEARTBEAT stays on as the link-loss net.

The tier-0 pair is commit 48700fcb (operator-style two-stage auto-land): the operator asked for landings like their own
(09:29 reply above, to "what's the hardest touchdown you'd accept?"), the CEO built it, the operator flashed and flew it
on 10-21 (landing-auto-fast-mode3-1/-2).
