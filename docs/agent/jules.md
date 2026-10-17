# Google Jules as a worker (free with Ultra)

Written 2026-10-17. Facts are from the official docs that day (jules.google/docs/usage-limits, /changelog, /api/reference,
developers.google.com/jules/api/reference/rest). "UNVERIFIED" marks the rest. Setup history: `docs/agent/ultra-setup.md`.

## Why use it

| | Jules | Claude-plan workers |
|---|---|---|
| Quota | Ultra: 300 tasks per rolling 24 h, 60 at once, counted in tasks, not tokens | session limits of each account |
| Claude tokens | none for the work; only the CEO's PR review | all of it |
| Model | Google picks it (Ultra = "priority access to the latest model"). No per-task choice in the UI, API or CLI | chosen per run |
| Output | a GitHub PR on the repo | local commits |

What counts as one "task" (does a follow-up message count?) is not defined (UNVERIFIED).

## Rules (read first)

- **No spend** (memory `ultra-only-no-spend`): if anything asks for credits, a plan upgrade or a card, stop and tell the user.
- **Key:** only the user makes it (jules.google -> Settings -> API keys, max 3) and saves it to `D:\tools\jules\api-key.txt`
  (one line) or `$env:JULES_API_KEY`. Agents never type, print, paste or commit the key. That path is outside the repo.
- **Fit:** Jules runs on Google's Ubuntu VM from a clean clone of the **pushed** branch. It has no Keil, no lab, no WSL tools,
  no Drive, and no untracked logs or videos. Give it: Python in `ground_station/`, host C tests (`tests/firmware_host`,
  `API/tests`), docs, small refactors. Never: `USER/` (Keil), flashing, lab or capture runs, anything that needs `logs/`.
- **Small briefs:** one narrow change per task, with the test to run. The CEO reviews every PR (`gh pr diff --stat`, then
  targeted reads) before merging. A PR is a claim, not proof (memory `worker-verification-discipline`).
- Jules reads `AGENTS.md`. `jules.ps1` adds a short rule footer to every prompt (stay in the brief, run tests, list them in the PR).

## Use

```powershell
# write the brief to .agent-ops/briefs/<id>.md, commit it and push the branch first (Jules sees origin, not local HEAD)
powershell -File .agent-ops/jules.ps1 -Id t-foo -Brief .agent-ops/briefs/t-foo.md      # start + wait (run in background)
powershell -File .agent-ops/jules.ps1 -Id t-foo -Session <id>                          # re-poll after a timeout
powershell -File .agent-ops/jules.ps1 -Sources                                         # is the repo connected?
```

- Options: `-Branch` (default: current branch), `-NoPr` (no PR, the work stays in the session), `-TimeoutMin 90`, `-PollSec 60`.
- Read only the digest `.agent-ops/out/jules-<id>.md` and the one status line. `jules-<id>.json` has the raw session and
  activities, for debugging only. `jules-<id>.session` holds the session id.
- If the branch on origin differs from local HEAD, the status line and digest say `WARN: Jules sees origin/...`.
- To answer Jules or approve a plan, use the session URL from the digest in a browser (the user), or the API
  `POST sessions/<id>:sendMessage {"prompt": ...}` / `:approvePlan` (not wrapped in the script yet).

| Exit | Meaning |
|---|---|
| 0 | PR opened or session COMPLETED |
| 1 | FAILED (reason in the digest), or a bad argument |
| 2 | still running at the timeout: re-poll with `-Session` |
| 3 | API error (401 = bad or missing key, ask the user) |
| 4 | no key |
| 5 | branch not on origin |
| 6 | stopped for input (AWAITING_USER_FEEDBACK / AWAITING_PLAN_APPROVAL / PAUSED) |

## API facts (v1alpha, may change)

Base `https://jules.googleapis.com/v1alpha`, header `x-goog-api-key`. `POST /sessions` with `prompt`, `title`,
`sourceContext {source: "sources/github/<owner>/<repo>", githubRepoContext {startingBranch}}`,
`automationMode: "AUTO_CREATE_PR"` (default is no PR), `requirePlanApproval` (default false = auto-approve).
Session `state`: QUEUED, PLANNING, AWAITING_PLAN_APPROVAL, AWAITING_USER_FEEDBACK, IN_PROGRESS, PAUSED, FAILED, COMPLETED.
`url` = web link. PR = `outputs[].pullRequest {url, title, description}`. `GET /sessions/<id>/activities` gives
`progressUpdated`, `agentMessaged`, `planGenerated`, `sessionFailed.reason`, and so on.
The CLI (`D:/tools/npm/jules.cmd`, `jules remote new|list|pull`) does the same but prints nothing without a TTY here, so use the API.
