# Worker Guide — `.agent-ops/WORKERS.md`

Reference for planners before spawning a worker. Facts sourced from the listed files; quote nothing you did not read.

## 1. Worker kinds table

| Kind | Backend | When to use | Quota / rate limit | Cost |
|------|---------|-------------|-------------------|------|
| **agy** | Antigravity CLI (`agy` binary) | Default. Long-horizon / mechanical work on `flash` (low effort); escalate to `sonnet` for tricky debugging, `opus` only after sonnet fails | Two independent pools: Gemini (Flash/Pro, scarcer) and Claude+GPT (Sonnet/Opus/GPT). `flash` default = `gemini-3.8-flash-low` | Free (user's Google account) |
| **ark** | Volcengine Agent Plan (`claude` binary) | When agy quota is exhausted, or task needs it. Must start outside repo with `--add-dir` | `ARK_MAX_WORKERS=2`, 10 s stagger between spawns | Paid (AFP metered); `deepseek-v4-flash` (coeff 0.5) default; `glm-5.3` coeff 4.5 — use only when truly needed |
| **oc** | opencode (`opencode run`) | Free-model parallel work; OpenRouter can run in parallel with others | Hetzner: 10 req/min, 4M in/100k out per min. OpenRouter :free: 20 req/min, 1000 req/day | Free (experimental) |

## 2. oc aliases — 2026-09-23 trial record

| Alias | Model | Trial record |
|-------|-------|-------------|
| `qwen` | `hetzner/Qwen/Qwen3.6-35B-A3B-FP8` | **Proven** (3 real tasks). Default oc worker. ~6 min/task |
| `free` | `openrouter/openrouter/free` | Tested OK. Only OpenRouter id allowed without `:free` |
| `qwen27` | `hetzner/Qwen3.8-27B` | Tested OK (44 s) |
| `nemo` | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | **Failed** — edit loop on empty-oldString Edit, then upstream 503. Treat OpenRouter free as fallback only |
| `ultra` | `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | Tested OK (33 s) |
| `nex` | `openrouter/nex-agi/nex-n2.5-pro:free` | Tested OK (51 s) |
| `laguna` | `openrouter/poolside/laguna-s-2.1:free` | Tested OK (77 s) |

**Rule:** one Hetzner worker at a time (shared 10 req/min limit). OpenRouter can run in parallel with others and with Hetzner.

## 3. Guard rails

**Paid-model guard:** `oc-worker.sh` and the `agent-ops.ps1` preflight refuse any OpenRouter id without `:free`. Never touch paid credit.

**opencode deny rules (groups)** — side effects only; reads, tests and read-only livewatch are allowed:
- Commands matching: `8081`; `agent-keys`, `printenv`, bare `env`, echo of `*API_KEY*`; flashing
  (`rebuild_and_flash`, `safe_flash`, `flash-write`, `flash-erase`, `UV4 -f`, `pyocd flash|erase|reset`);
  drone-writing livewatch (`poke reset halt step resume bp wp rtt-write fault-erase`);
  `git commit/push/reset/checkout/stash/clean/restore`; `rm -rf`
- Edits under: `API`, `TASK`, `BSP`, `USER`, `FreeRTOS`, `stm32_lib`, `OBJ`, `.git`, `.agent-ops/*.sh|ps1`
- Best-effort only — `verify` re-checks the log and the paths deterministically.

**Serena (oc workers):** every oc worker has the `serena` MCP server (Windows `serena.exe` over WSL
interop, `~/.config/opencode/serena-mcp.sh`). Which checkout it binds to depends on the spawn:

| Spawn | Serena project | Editing |
| --- | --- | --- |
| plain | main repo | refused — `.serena/project.yml` has `read_only: true` |
| `-Worktree` | that worktree | allowed — `spawn-worker.sh` writes a `read_only: false` copy of `project.yml` into the worktree and exports `SERENA_PROJECT=<windows path>` |

The enforcement point is `read_only` in `project.yml`, which is server-side: the Serena process
itself refuses every edit tool, so a worker cannot talk its way past it. The opencode `tools` block
denies exactly one thing, `serena_execute_shell_command` — `read_only` does not cover the shell tool
and it would bypass opencode's own bash deny rules.

So a worktree worker should edit *through* Serena (`replace_symbol_body`, `insert_after_symbol`)
rather than by line-based search-and-replace: the symbol tools locate the edit by name and cannot
corrupt an unrelated part of the file. `spawn-worker.sh` appends a paragraph saying so to every
worktree task file. A non-worktree worker gets the read-only set: `find_symbol`,
`find_referencing_symbols`, `get_symbols_overview`, `get_diagnostics_for_file`.

**win.sh HW_WRITE guard:** `win.sh` refuses flash/reset/halt/poke commands with exit 126 unless `AGENT_OPS_ALLOW_HW=1`. Workers build; the supervisor flashes.

**Why git checkout/restore/stash are denied:** the main branch holds uncommitted work. `git checkout/restore/stash` would clobber it or lose it. Always work from the repo-root path or a worktree.

## 4. Lifecycle

**Spawn → wait → result.md → verify.**

- Spawn: `agent-ops.ps1 spawn "task"` (or `-File task.md`). Returns `spawned <TASK_ID>`.
- Wait: `agent-ops.ps1 wait <task_id>`. Exit codes: 0 success, 2 worker failed, 3 gave up, 4 needs attention, 5 idle death (rc=0 but no result file).
- **Result file** is at `.agent-ops/tasks/<TASK_ID>.result.md`. A task without it is a failure.
- **Verify via liveness only:** `pgrep -af "run-worker[.]sh [0-9]" | grep -v "bash -lc"` inside WSL. `pgrep` without `-f` reports every live worker as dead (false negative).

**Watchdog false positives:**
- `STALLED` measures stdout silence only (600 s), never file mtimes — a long quiet coding trip trips it.
- `pgrep` without `-f` reports every live worker as dead.
- Windows tmux windows stay open after worker exits (scrollback kept on purpose) — open does not mean running.
- `STARTED:` is written optimistically by the wrapper; a worker dying on its first token still logs it.
- **rc=0 is a claim**, not evidence — a stub result file (~470 B) means idle death, not success. Check the working tree before concluding nothing was done.

## 5. Planner checklist for accepting a result

Run `agent-ops.ps1 verify <id>` first. It does 1-4 below deterministically (exit 0 PASS / 1 WARN /
2 FAIL / 3 RUNNING): exit rc, result size, exact diff since spawn (`<id>.pre-tree` snapshot, so files
already dirty at spawn still count), forbidden paths, out-of-scope paths, forbidden commands in the
log, edit loops, key values in changed files, and the tests picked from the changed paths.
The planner reads only the verdict, then opens just the diff hunks it flags. Manual steps, for reference:

1. **Log grep:** read `.agent-ops/logs/<id>.out` (tail, piped through `tr -d '\007'`) — never trust the digest alone. Distinguish `UNAUTHENTICATED` 401 (re-login) from `RESOURCE_EXHAUSTED` 429 (switch pools) — both exit rc=3.
2. **Tests through win.sh:** run only the pytest files the change touches, output through `tail -5`. Never the whole ground_station suite unless the task says so.
3. **Diff scope:** `git status --short -- . ':(exclude)OBJ'` filtered against the pre-status snapshot. Never run bare `git status` — ~200 files are already dirty.
4. **Artifact verification:** check the real file location, not the worker's description. Re-run verification yourself.
5. **Worktree isolation:** a worktree task's test counts describe its own tree only. Never conclude main is broken from a worktree run; spawn without `-Worktree` when the deliverable must land in main.

---

Sources: `.agent-ops/agent-ops.ps1`, `.agent-ops/spawn-worker.sh`, `.agent-ops/oc-worker.sh`, `.agent-ops/win.sh`, `.agent-ops/STANDING-RULES.md`, memory files `oc-free-workers.md`, `ark-worker-model-choice.md`, `worker-failure-signatures.md`, `worker-liveness-signals.md`, `worker-verification-discipline.md`, `agy-model-budget.md`.
