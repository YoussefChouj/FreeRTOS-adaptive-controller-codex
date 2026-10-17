# Google Colab for agents (GPU jobs)

Written 2026-10-17. Facts were checked that day; "UNVERIFIED" marks the rest. Setup history: `docs/agent/ultra-setup.md`.

## Rules (read first)

- **No spend, ever** (user rule, memory `ultra-only-no-spend`). The user pays for Google AI Ultra only.
  Never run `colab pay`. Never buy compute units. Never open a checkout page. If any step asks for a card, stop and tell the user.
- **Nothing on C:** (C: is nearly full). The CLI lives in WSL Ubuntu, which is on D:. Local data and results go on D:
  (`logs/nn/` etc.), never `%TEMP%` on C: for big files.
- **Always stop what you start.** Use `colab run` (stops itself) or end with `colab stop -s <name>`. Then check
  `colab sessions` shows nothing left running.
- Only the user can re-auth (it needs a browser and a pasted code). When a call fails with 401 or 403, ask the user. Don't loop.

## What this account gets (2026-10-17)

| Item | State |
|---|---|
| Account | user's Google AI Ultra Gmail (`colab whoami` shows it) |
| Compute units | **100.00** on 2026-10-17 (`colab usage`), bought once by the user. Spend them by `docs/agent/colab-budget.md` (read it before any Colab job) |
| Free T4 GPU | **only at 0 CU** (FAQ: free runtimes need no positive balance; with units, a T4 costs ~1.6 CU/h). At 0 CU it worked: `colab run --gpu T4` ran torch 2.11.0+cu130 on a Tesla T4 15 GB and stopped itself |
| L4 / A100 / H100 / G4, TPU | need units or an entitlement. A `400` on `colab new --gpu X` = not allowed: fall back to `--gpu T4` |
| Free VM life | Google can reclaim a free VM at any time (idle or long runs; exact limits UNVERIFIED). Save checkpoints and download them often |

## Where the tools are

| Tool | Path | Use |
|---|---|---|
| Colab CLI `google-colab-cli` 0.7.4 | WSL `~/.local/bin/colab` | the main tool, scriptable |
| Bundled guide | `colab skill`, `colab readme` | upstream agent guide (88 lines). Read it with `\| head`, don't dump it |
| Auth token | WSL `~/.config/colab-cli/token.json` | default `--auth` is `oauth2` in 0.7.4 (the skill text says `adc`, which is wrong for this install) |
| Session state | WSL `~/.config/colab-cli/sessions.json` | don't edit it. Use `colab --config <file>` to isolate parallel agents |
| Colab MCP | Claude Code user MCP `colab-mcp`, venv `D:/tools/colab-mcp` | drives a Colab tab in a browser. Its tools load only in a NEW Claude session. The PyPI `colab-mcp` package is NOT Google's: never install it |

## Calling it from Windows (Claude Code Bash tool)

```bash
timeout 600 wsl -d Ubuntu -- bash -lc 'cd /mnt/d/projects/UAV_lab/FreeRTOS-adaptive-controller-codex && ~/.local/bin/colab sessions' </dev/null 2>&1 | grep -v systemd
```

- `</dev/null` is required: without it some commands wait on stdin. `grep -v systemd` drops a harmless WSL warning.
- A cold WSL start takes more than 30 s, so give every call a long tool timeout (120 s or more).
- The repo is `/mnt/d/projects/UAV_lab/FreeRTOS-adaptive-controller-codex` inside WSL.
- The Terminal-panel tool sends Ctrl-C to slow `wsl ...` lines. Use the Bash tool. A human uses a real terminal only for the auth paste.
- Never start `repl`, `console`, `auth`, `drivemount` or `ssh` without piped input: they need a TTY and hang.
  (`repl`/`console` accept piped stdin and exit at EOF.)

## Read-only checks (safe, free)

| Command | Shows |
|---|---|
| `colab usage` | balance, burn rate per hour, active VMs |
| `colab whoami` | signed-in email, scopes, token expiry (hidden debug command) |
| `colab sessions` | live VMs on the server (`[?]` = orphan, so stop it) |
| `colab status -s N` / `colab log -s N -n 20` | hardware, IDLE/BUSY, recent events (use these first when a job fails) |

## Recipe 1: a short one-shot job (`colab run`)

```bash
colab run --gpu T4 --timeout 1800 job.py --arg 1 > out.txt
```

- This does new + exec + stop. It stops the VM even when the script crashes. The script's exit code comes back.
- **The default `--timeout` is 30 s.** Always pass it, in seconds, a bit more than the job needs.
- Only `job.py` is sent. Repo imports and data files do NOT exist on the VM. Make the script self-contained, or use recipe 2.
- Script stdout goes to stdout. `[colab]` chatter goes to stderr.

## Recipe 2: a long job with data (new / upload / exec / download / stop)

```bash
colab new --gpu T4 -s drift                       # always name it (-s)
colab install -s drift some_pkg                   # torch, numpy are already there
colab upload -s drift /mnt/d/.../data.npz /content/data.npz
colab upload -s drift ground_station/analysis/nn_io.py /content/nn_io.py
colab exec -s drift -f train_colab.py             # local script is sent to the kernel; kernel state persists between exec calls
colab download -s drift /content/out/model.pt logs/nn/model.pt   # check arg order with: colab download --help
colab stop -s drift
```

- Pack the data locally on D: into one `.npz` and upload it. That beats many small files, and the VM has no repo checkout.
- A repo module that imports other repo modules (`nn_io.py` imports `video_truth`) needs those uploaded too, or a small stub.
- Write outputs under `/content/out/` and download them as you go. A reclaimed VM loses everything on it.
- `colab url -s drift` gives a browser link to the same VM (handy for the user to watch). `colab log -s drift -o run.ipynb` exports the history.
- After a 404 or "session not found", the VM was pruned: `colab sessions`, then `colab new` again.
- Google Drive inside the VM needs `colab drivemount` (interactive, so a human runs it). Prefer upload and download.

## Good GPU jobs here

The sims (`sim/sil`, `sim/bench`, `sim/adaptive_compare`) are numpy + scipy and single-process: a GPU does not help them.
GPU candidates: ground-side drift net `ground_station/analysis/nn_io.py` (GRU/TCN), `sim/bench/ctrl_nn2.py`,
`ctrl_p2_neuro.py`, tracker vision. First job (user, 2026-10-17): the drift net. Plan is in `docs/agent/ultra-setup.md` Status.

## Zero compute units: why, and what the user can do

Official sources (2026-10-17): the Colab FAQ <https://research.google.com/colaboratory/faq.html>, the Google blog
"Colab is now part of your Google AI plan" (rollout from 2026-09-22), and colabtools issue #6127.

| Possible cause | Check | Fix |
|---|---|---|
| Benefit not linked yet | Colab web -> **Settings -> Subscription** | FAQ: click **"Colab paid products"** at the bottom of a notebook, then **"Upgrade to Google One"**. With Ultra already paid, this should only confirm and charge nothing. **If it shows a price or asks for a card, stop.** |
| Ultra is a free trial / promo / student offer | Google One -> plan page | FAQ: benefits are "not included during a free trial". They start only once the plan is paid |
| Country | Google payments profile country (UNVERIFIED that this is the deciding field) | FAQ: benefits are "only honored in countries where Colab offers its paid option" |
| Rollout or bug | issue #6127 (open since 2026-10-05: AI Pro/Plus users with 0 or unrefreshed units) | Colab team (2026-10-09): send **Help -> Send feedback** in Colab and mention #6127. One user got units back on 10-11 |
| Units arrive monthly | FAQ: deposit date follows the plan start (Asia rollout date 2026-09-17) | wait for the next monthly deposit, then re-check `colab usage` |

The unit count per plan is not on any official page that was fetched. Pages that repeat "Ultra = 1,000-2,000" give no
source (UNVERIFIED). Until units show up, use the free T4 only.
