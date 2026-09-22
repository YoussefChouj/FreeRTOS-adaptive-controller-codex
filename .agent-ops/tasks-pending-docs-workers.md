# Docs: worker guide (2026-09-23)

Write ONE new file, `.agent-ops/WORKERS.md` (max ~150 lines). It is the reference a planner reads
before spawning a worker. Sources to read (facts only from these, quote nothing you did not read):
- `.agent-ops/agent-ops.ps1` (header usage, `$ocModelMap`, Test-WorkerBackend, the command map)
- `.agent-ops/spawn-worker.sh` (worker kinds, launch dir, worktree flag -w)
- `.agent-ops/oc-worker.sh`, `.agent-ops/win.sh` (HW_WRITE guard), `.agent-ops/STANDING-RULES.md`
- `/mnt/c/Users/Acer/.claude/projects/C--Users-Acer-Desktop-UAV-lab-FreeRTOS-adaptive-controller-codex/memory/`
  files: oc-free-workers.md, ark-worker-model-choice.md, worker-failure-signatures.md,
  worker-liveness-signals.md, worker-verification-discipline.md, agy-model-budget.md
- `~/.config/opencode/opencode.json`: the `permission` block ONLY. Never open or quote
  `~/.config/agent-keys.env` and never write any key or key value.

Sections:
1. Worker kinds table: agy / ark / oc, backend, when to use, quota or rate limit, cost.
2. oc aliases table (qwen, free, qwen27, nemo, ultra, nex, laguna) with the 2026-09-23 trial
   record: qwen proven (3 real tasks), nemo failed (edit loop, then provider 503).
   Rule: one Hetzner worker at a time (10 req/min); OpenRouter can run in parallel.
3. Guard rails: the paid-model guard; the opencode deny rules (summarise them in groups); win.sh HW_WRITE;
   why git checkout/restore/stash are denied (main holds uncommitted work).
4. Lifecycle: spawn -> wait -> result.md -> verify (liveness only via WSL pgrep; watchdog
   false positives such as NET_DOWN on code line numbers "502:"; rc=0 is a claim).
5. Planner checklist for accepting a result (log grep, tests through win.sh, diff scope).

Touch nothing else. Exit cleanly when done.
