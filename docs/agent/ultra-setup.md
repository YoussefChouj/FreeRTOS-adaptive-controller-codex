# Google AI Ultra setup (started 2026-10-17)

Rule (user, 2026-10-17): Ultra is the ONLY thing paid for. Nothing that needs extra money: no AI credits,
no Colab compute units, no Antigravity overages, no paid tiers. Memory `ultra-only-no-spend`.

## Verified facts (2026-10-17, official pages)

| Service | Fact | Source |
|---|---|---|
| Google Cloud credit | Ultra = $100/month, activated once in the Developer Program portal and applied to a Cloud Billing account | blog.google gdp-premium-ai-pro-ultra (2026-01) |
| Colab | Ultra = priority accelerators + Premium GPU + uninterrupted background execution; rolling out from 2026-09-22 in Colab countries. NO compute-unit number is published (the pasted "1,000-2,000 CCU" is unverified) | developers.googleblog.com colab-is-now-part-of-your-google-ai-plan |
| Colab MCP server | googlecolab/colab-mcp, runs locally, drives a Colab browser session from Claude Code / Gemini CLI | developers.googleblog.com announcing-the-colab-mcp-server (2026-03) |
| Colab CLI | `google-colab-cli` (uv tool / pip), Linux + macOS only -> install in WSL Ubuntu; ships COLAB_SKILL.md | letsdatascience.com news (2026-06), verify on PyPI first |
| Jules | Ultra = 300 tasks / rolling 24 h, 60 concurrent | jules.google/docs/usage-limits |

Local state: Drive for desktop running as `G:` (`G:\My Drive`). C: has 12.6 GB free (the tight disk); D: 183 GB free.
Not installed: gcloud, rclone, jupyter. Installed: node/npm, VS Code, Python, WSL Ubuntu, plus the tools below.

## Installed tools (2026-10-17, all on D: or in WSL, never C:)

| Tool | Where | Notes |
|---|---|---|
| Jules CLI `@google/jules` 0.1.42 | `D:/tools/npm/jules.cmd` (binary `D:/tools/npm/bin/jules.exe`), npm cache `D:/tools/npm-cache` | not on PATH: call the full path. No `--version` flag; with no TTY it hangs, so the user runs `jules login` in a real terminal |
| Colab MCP (googlecolab/colab-mcp @ b9ab3899, v1.0.1) | venv `D:/tools/colab-mcp` (Python 3.13), logs `D:/tools/colab-mcp/logs` | Claude Code user MCP `colab-mcp`, connected. The PyPI package `colab-mcp` is NOT Google's: never install it |
| Colab CLI `google-colab-cli` 0.7.4 | WSL `~/.local/bin/colab` (uv tool; uv at `~/.local/bin/uv`) | Linux only. Repo googlecolab/google-colab-cli. No `login` command: the first API call prints a Google URL and asks to paste the code back (needs a real TTY). `colab usage` = read-only balance check. NEVER run `colab pay` |
| pip cache (Windows) | `D:/tools/pip-cache` (`%APPDATA%\pip\pip.ini`) | the old 348 MB C: cache was purged |
| Temp for installs | `D:/tools/tmp` | set `TMP`/`TEMP` to it for big installs |
Sims (`sim/sil`, `sim/bench`, `sim/adaptive_compare`) are numpy + scipy, single process (no multiprocessing):
CPU cores help, GPUs do not. GPU candidates: `sim/bench/ctrl_nn2.py`, `ctrl_p2_neuro.py`, ground-side `nn_io.py` GRU/TCN, tracker vision.

## Plan (order) and who does what

1. **Drive archive** (no account step). Find the big data first: the 60 s `du` on the repo top dirs timed out before
   ground_station/logs/sim/scratch (run it in the background), and find what fills C:. Then `scripts/archive_to_drive.ps1`:
   moves only UNTRACKED/ignored data (check `git ls-files`), older than N days, to `G:\My Drive\UAV_lab\archive\<same path>`
   with robocopy /MOVE after a count+size check, leaving an `ARCHIVED.txt` pointer. Dry run by default.
2. **Jules** (free). User: jules.google -> sign in -> connect GitHub, approve OAuth, pick
   YoussefChouj/FreeRTOS-adaptive-controller-codex. CEO: install the Jules CLI (verify the npm package name), user runs the login,
   test one small task (e.g. a missing unit test) on a pushed branch. Every Jules PR is reviewed by the CEO
   (worker-verification-discipline); Jules never touches firmware flashing or lab steps.
3. **Colab** (free with Ultra). User: open colab.research.google.com and confirm the Ultra benefit shows (do NOT buy units).
   CEO: install colab-mcp (pip, uvx is broken here) and add it to Claude Code; install google-colab-cli in WSL;
   user signs in once. First job: a GPU notebook for the NN drift model or ctrl_nn2.
4. **Google Cloud** (needs decision). A Cloud Billing account normally needs a payment method on file, even when the
   $100 credit covers the usage (UNVERIFIED for credit-only accounts: check during activation). If the user will not
   add a card -> skip; Colab covers compute. If yes: gcloud in WSL, project `uav-sim`, budget alarm at $80 (alarms do not
   cap), hard cap per VM = spot + `--max-run-duration=4h --instance-termination-action=DELETE`,
   `scripts/cloud_sweep.sh` (create a 32-core spot VM -> rsync sim/ -> N parallel shards -> pull results -> delete).
   Needs a shard runner first (none exists).
5. Skip: Veo/Flow/Genie (defense visuals only), Firebase Studio. Keep: NotebookLM (`.agent-ops/research/nb/`), Deep Research
   for thesis literature next to Argon.

## Status

- 2026-10-17: step 1 built (99ab323c, `lab archive`, dry run only so far: 27 GB movable at --days 0). User 10-17: move all but floor scans; d427a174 = Drive fallback `video_truth.video_file()` + index `docs/lab/video-archive.csv` + `docs/agent/video-archive.md`. Move DONE 10-17: `lab archive --days 0 --yes` rc=0, 1044 videos / 26.99 GB on Drive, 0 kept, 24 floor scans stay, 1044 rows in the index. D: frees as Drive uploads its D: cache. Tools go on D: (C: ~13 GB free). Installed and checked 10-17 (table above): Jules CLI, Colab MCP (registered, connected), Colab CLI in WSL, pip cache moved. C: unchanged at 13 GB free. User still to do: `D:\tools\npm\jules.cmd login` + connect GitHub at jules.google; open Colab once and sign in (first colab-mcp use opens it); `colab` login in WSL. Waiting: GCP card go/no-go. 10-17 sign-ins: `jules login` started in a Terminal-panel tab (node waits on Google). The Terminal tool Ctrl-C's `wsl ...` lines (slow start), so the user types `wsl -d Ubuntu -- bash -lc '~/.local/bin/colab usage'` and pastes the code. colab-mcp tools load only in a NEW Claude session (registered after this one started). GCP default = skip until the user says yes.
