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
Not installed: gcloud, rclone, jules, jupyter. Installed: node/npm, VS Code, Python, WSL Ubuntu.
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

- 2026-10-17: facts verified, user picked all four, no-spend rule saved. Nothing installed yet. NEXT: step 1.
