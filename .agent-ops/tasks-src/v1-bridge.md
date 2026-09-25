# Task v1-bridge: show VPS workers in the laptop monitor window

Context: `.agent-ops/monitor-window.ps1` (Windows PowerShell 5.1, WinForms) lists local workers by
parsing `.agent-ops/state.log` lines of the form
`2026-09-25T22:08:04+08:00 [20260925-220444] KIND: msg` (regex near line 93 accepts only ids
`\d{8}-\d{6}`) and tails `.agent-ops/logs/<id>.out`. Kinds in use: SPAWNED STARTED PROGRESS DONE
EXIT(msg `rc=N`) STALLED QUIET MEM NET_DOWN LOOP FAILED; `$alertKinds` (line 19) triggers balloons.

Remote workers run on a VPS reached by `ssh oc-agent`. Per worker <id> the server has
`~/runs/<id>.out` (log; lines `STARTED <iso> model=<m>`, `FALLBACK after <ST> on <m>`,
`DONE rc=<n> status=<OK|FAIL|TIMEOUT|QUOTA|AUTH|SETUP> <iso>`), `~/runs/<id>.status`, `~/runs/<id>.rc`.
See `.agent-ops/vps-worker.sh` for how the laptop talks to it.

Deliver:
1. `.agent-ops/vps-bridge.sh` (bash; runs in Git Bash on Windows and in WSL). Loop every 30 s:
   ONE ssh call (`-o BatchMode=yes -o ConnectTimeout=10`) that prints, per run, its id, newest
   STARTED/FALLBACK/DONE line and the last 300 log lines (design a simple delimiter protocol).
   Locally: mirror each log to `.agent-ops/logs/vps-<id>.out`; append to state.log only on
   transitions (remember the last seen line per id in `.agent-ops/logs/vps-bridge.seen`):
   STARTED -> `STARTED: vps model=...`, FALLBACK -> `PROGRESS: fallback ...`, DONE ->
   `DONE: status=...` then `EXIT: rc=N`; status QUOTA/AUTH/FAIL/TIMEOUT -> additionally
   `FAILED: <status>` before EXIT. Use ids `vps-<id>`. Timestamps `date -Iseconds` style
   (`+08:00` form, as existing lines). ssh failure: skip the cycle silently. Exit after
   `--once` if given (for testing). Pure-function parsing so it can be tested offline.
2. `monitor-window.ps1`: accept ids matching `[\w-]+` (keep everything else identical), and start
   `vps-bridge.sh` hidden while the window is open (kill it on close); find bash via
   `C:\Program Files\Git\bin\bash.exe`, skip silently if absent.
3. A test: `tests/agent_ops/test_vps_bridge.sh` or a pytest under an existing tests dir that feeds a
   canned ssh output (fake `ssh` on PATH) through `vps-bridge.sh --once` and asserts the
   state.log lines and mirrored files. Run it and report the result.

Keep it small (bridge < 80 lines). Do not touch other files.
