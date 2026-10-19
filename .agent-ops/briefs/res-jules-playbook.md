# Research brief: the best way to drive Google Jules (read-only research, no edits, no commits)

## Why
We use Google Jules (free with Google AI Ultra) as a coding worker via its REST API v1alpha, driven by
`.agent-ops/jules.ps1` (PowerShell 5.1) and documented in `docs/agent/jules.md`. The CEO agent (Claude) reviews every PR.
We keep hitting friction and re-learning things. We want one verified playbook. Read both local files first.

## Friction we hit on 2026-10-17 (4 sessions)
1. With `requirePlanApproval=false` and `AUTO_CREATE_PR`, sessions still stop in AWAITING_USER_FEEDBACK asking
   "does this plan look complete?" or open design questions. Each round trip costs 10-30 min.
2. After `:sendMessage` the state stays AWAITING_* for a while; one reply was not picked up for 30+ min.
3. After the PR opens, GitHub CI failures arrive in the session as `userMessaged` "CI failed" and the session goes PAUSED.
   Our CI is red on the base branch already, so this is noise.
4. Jules works from a clean clone of the PUSHED branch: unpushed or untracked files are invisible.
5. Quality gaps in a PR: a binary test model committed at repo root, magic numbers, silent `#else` fallbacks.
6. Our side (fixed): PS 5.1 ConvertTo-Json expanded Get-Content notes into a 107 MB body; a VPN drop killed waits.
7. No `gh` CLI locally; we review PRs with `git fetch origin pull/N/head:pr-N`.

## Questions (answer each, cite sources)
A. Official facts (jules.google docs, developers.google.com/jules/api, Jules Tools CLI, changelog, Google blog):
   session states and what moves a session out of each (esp. PAUSED, COMPLETED: can a message to a COMPLETED/PAUSED
   session make Jules push follow-up commits to the SAME PR?); every request field of sessions.create
   (requirePlanApproval, automationMode, title, sourceContext, anything else); activity types; sendMessage/approvePlan
   semantics and latency; how to stop check-in questions; environment setup script / snapshot (install deps once);
   AGENTS.md support; memory/knowledge; repo-level config; internet and apt access on the VM; time and size limits;
   Ultra quotas (tasks per day, concurrent); which Gemini model runs; GitHub integration (issue label, @jules in PR
   comments, how CI results are fed back, can that be turned off).
B. Practice (community posts, GitHub repos that script the Jules API, forum threads): prompt structure that gets a
   finished PR in one shot; task sizing; parallel sessions; review loop; handling CI feedback.
C. Gaps: compare A and B with our `jules.ps1` and `docs/agent/jules.md`. List concrete changes (file:line, what, why).

## Rules
- Read-only. No edits, no commits, no Jules API calls, never open or read `D:\tools\jules\api-key.txt`.
- Every claim gets a source URL plus a short quote, tagged OFFICIAL, COMMUNITY or UNVERIFIED. Say "not found" rather than guess.
- Output `.agent-ops/out/argon-res-jules.md`, at most 2500 words: A, B, C, then a 10-line "playbook" summary.
