# Agent guidance: managing multi-session complexity

Written 2026-10-20 after a quota-hit and branch-sync incident that cost 3 hours of recovery.

## The core problem

Claude Code sessions on this account hit the "Individual quota reached" wall at ~3-5 hours. The lab
PC session runs from a shared `main` tree. Cloud sessions run from separate branches (`overnight-*`,
`claude/*`). When the lab PC session crashes and a cloud session picks up work, the two can diverge.
If the lab PC session then resumes without syncing, it overwrites or conflicts with the cloud work.

## The rules that prevent the problem

### 1. Always be strategic globally and tactical locally (operator 2026-10-20)

Every action is read by agents and humans in other sessions, accounts, and tools. Optimize for the
next agent, not the next reply. Persist cross-agent truth to files other agents actually read:
`docs/agent/HANDOFF.md`, `docs/agent/memory/`, `docs/lab/`. One ship point at a time.

### 2. Quota-hit survival (operator 2026-10-20)

- **Save at every subtask.** Update the HANDOFF and `git commit` after each closed subtask, even
  a 1-line doc edit. Never carry >1 unit of unsaved work into the next boundary.
- **Push after every verified step.** An unpushed commit is a lost commit. `git push` after
  every `git commit`, even for a one-liner.
- **Mirror artefacts to origin.** Anything you'd want to recover after a crash should be on
  `origin/<branch>`. Cloud sessions push to their own branch; lab PC sessions push to `main`.
  Both are reachable from everywhere.
- **At the quota wall, drop a resume file.** Create `.agent_state/last-cutoff.md` with:
  - next 3 actions
  - DO NOTs  
  - exact commands to resume (`git pull`, `git push`, `ah refresh`)
  One file, short. The next session reads it and the HANDOFF before doing anything else.

### 3. Branch hygiene

The golden rule: **`origin/main` is the source of truth.** Everything else is a worktree or a
short-lived branch that feeds into it.

| Branch type | Where it lives | Lifetime | Pushed to origin? |
|---|---|---|---|
| `main` | lab PC `main` worktree | permanent | always |
| `overnight-*` | mirror of `main` at end of lab day | temporary | always |
| `claude/<name>` | cloud agent session branch | per-session | always after each step |
| `feature/*` | long-lived named branch | per-feature | when PR is opened |
| worktrees under `.worktrees/` | named worktree directories | per-stream | never (local only) |
| temp scratch worktrees | `wt-*` in repo root | per-incident | never |

**Before starting a new session (lab PC or cloud):**
```
# Lab PC
git fetch origin
git log --oneline origin/main | head -3   # is it at the right tip?
git branch -vv                             # any unpushed commits?

# Cloud
git fetch origin
git log --oneline origin/main | head -3   # same check
git log --oneline origin/overnight-YYYY-MM-DD | head -3  # is mirror current?
```

**If `origin/main` is ahead of your local `main`:** fast-forward first, then merge your work on
top:
```
git pull --ff-only origin main   # abort if not fast-forward
git merge origin/main            # your work on top
git push origin main             # push the merge
```

**If your branch is ahead of `origin/main`:** it means your work has not been merged yet.
Keep working on your branch and push after each step. Do NOT fast-forward `main` over your branch
without merging it first.

## The incident that caused this document (2026-10-20)

| What happened | Why it happened | How it was fixed |
|---|---|---|
| `origin/main` was 242 commits behind the lab PC's `overnight-2026-10-08` tip | Lab PC session crashed at quota; cloud session worked on `overnight-2026-10-08` while the local `main` was 242 commits behind | Fast-forwarded local `main` to `origin/main` |
| `origin/claude/labday-1019-fixes` (13 commits) never merged to `origin/main` | Cloud session said "CEO merges" but never submitted a PR; local session didn't know it existed | Merged the branch into `main` (VAR_WRITE 56/57, landing fix, storage fix, abort fix, tracker fix, scorecard fix, nn_io seq rawof) |
| `origin/claude/friendly-hypatia-3ln6o5` (2 commits) never merged | Same as above | Merged into `main` |
| `wt-att3` untracked folder in `main` worktree root | Scratch worktree left in wrong location by a temp session | Removed |
| 20 worktrees with unpushed local commits | Cloud sessions committed but didn't push | Flagged for manual push by the operator |

## Worktree audit (2026-10-20)

### Legitimate worktrees (properly tracked, branch refs in `.worktrees/`)

| Worktree | Path | Branch | Upstream state | Action needed |
|---|---|---|---|---|
| `main` | `D:\...\FreeRTOS-adaptive-controller-codex` | `main` | `origin/main` synced ✅ | — |
| `angry-saha-12497c` | `.claude/worktrees/` | `claude/angry-saha-12497c` | `+` (local ahead of origin) | push: `git push origin claude/angry-saha-12497c` |
| `elated-cartwright-4fa5e7` | `.claude/worktrees/` | `claude/elated-cartwright-4fa5e7` | `+` | push |
| `mrac-next` | `.worktrees/mrac-next` | `mrac/next` | synced to origin ✅ | — |
| `workflow-b` | `.worktrees/wfb` | `workflow-b` | synced ✅ | — |
| `project-thread-tx1gbh` | `FreeRTOS-adaptive-controller-codex-tx1gbh` | `claude/project-thread-tx1gbh` | `+` (ahead of origin) | push or merge |

### Orphan worktrees (not tracked in `.worktrees/`, not in any `.claude/worktrees/`)

| Worktree | Path | State | Recommendation |
|---|---|---|---|
| `wt-pr8` | `D:\wt-pr8` | branch `pr-8`, ahead of origin | Remove if work is done and merged; else push |
| `wt-night` | `D:\wt-night` | belongs to `FreeRTOS-wt-night` repo | **Do NOT touch.** Belongs to a different git repo |
| `wt-flash` | `D:\wt-flash` | detached HEAD, very old commit | Remove if work is done |
| `wt-att3` | `D:\wt-att3` | previously removed ✅ | Gone |
| `camtrack` | `FreeRTOS-adaptive-controller-codex-camtrack` | detached HEAD, old commit | Remove if work is done |
| `driftnn` | `FreeRTOS-adaptive-controller-codex-driftnn` | detached HEAD, old commit | Remove if work is done |
| `labfix` | `FreeRTOS-adaptive-controller-codex-labfix` | detached HEAD, commit `1188eccd` (merged in `d4898337`) | Remove — work is already merged |
| `yawbuild` | `FreeRTOS-adaptive-controller-codex-yawbuild` | detached HEAD, old commit | Remove if work is done |
| `s5a-wt` | `C:\Users\Acer\AppData\Local\Temp\claude\...\s5a-wt` | detached HEAD, in temp dir | Will self-clean when temp is cleared |
| `prleague` | `C:\Users\Acer\AppData\Local\Temp\claude\...\wt-league` | branch `prleague`, no origin | Will self-clean when temp is cleared |
| `pr11` | `C:\Users\Acer\AppData\Local\Temp\claude\...\wt-pr11` | detached HEAD, in temp dir | Will self-clean when temp is cleared |

### Cleanup commands

```powershell
# Remove orphan worktrees (only the ones BELONGING TO THIS REPO)
# Check first:
git worktree list

# Remove only the ones that belong to this repo and are detached/old:
git worktree remove "D:\wt-flash" --force   # only if it's not needed
git worktree remove "D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex-camtrack" --force
git worktree remove "D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex-driftnn" --force
git worktree remove "D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex-labfix" --force
git worktree remove "D:\projects\UAV_lab\FreeRTOS-adaptive-controller-codex-yawbuild" --force
git worktree remove "D:\projects\UAV_lab\wt-pr8" --force   # only if PR is merged or abandoned

# DO NOT remove wt-night if it belongs to another repo (check first!)
```

## Branch lifecycle guide

### Starting a new cloud session

```
# 1. Start on a fresh branch from the latest main
git fetch origin
git checkout -b overnight-YYYY-MM-DD origin/main

# 2. Write the stream page
python -m ground_station.agent_handoff start overnight-YYYY-MM-DD --as claude

# 3. After every subtask:
git add <paths>
git commit -m "short message: what changed"
git push -u origin HEAD

# 4. At the end of the session:
#    - merge your branch into main if done
#    - OR update the HANDOFF and push your branch for the next session
```

### Resuming after a quota hit

```
# 1. Fetch first
git fetch origin

# 2. Check what changed on origin while you were gone
git log --oneline origin/main | head -10

# 3. If origin/main is ahead:
git pull --ff-only origin main

# 4. Read the HANDOFF and any last-cutoff.md
cat docs/agent/HANDOFF.md
cat .agent_state/last-cutoff.md 2>/dev/null

# 5. Continue from where it left off
```

### Merging a cloud branch into main (from the lab PC)

```
# 1. Fetch the branch
git fetch origin

# 2. Merge into main (on lab PC, in the main worktree)
git checkout main
git pull --ff-only origin main   # always fast-forward main first
git merge origin/claude/<name>  # merge the cloud branch

# 3. Resolve any conflicts (usually just the HANDOFF)
#    Take the more recent timestamp + add any local-only items

# 4. Push
git push origin main
```
