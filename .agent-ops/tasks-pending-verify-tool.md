# Tool: deterministic worker-result verifier (2026-09-23)

Repo root (WSL): /mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex  (call it R)
Create exactly two files and touch nothing else:
- `R/.agent-ops/verify_task.py`   (Python 3.10+, stdlib only)
- `R/.agent-ops/tests/test_verify_task.py`  (pytest, tests the pure functions only)

The script is run on WINDOWS by the planner: `python .agent-ops/verify_task.py <task_id> [--no-tests]`.
It replaces the planner reading logs and diffs by hand. Output: at most ~40 lines, one line per
check `[OK|WARN|FAIL] <check>: <detail>`, last line `VERDICT: PASS|WARN|FAIL|RUNNING`.
Exit code 0 PASS, 1 WARN, 2 FAIL, 3 RUNNING. Repo root = parent of the script's directory.

## Pure functions (unit-test every one of them)
- `scan_log(text) -> list[(level, msg)]`. Strip ANSI (`\x1b\[[0-9;]*m`) and `\x07` first.
  opencode log line formats: bash commands start with `$ `, reads `→ Read <path>`,
  edits/writes `← Edit <path>` / `← Write <path>`, MCP calls `⚙ <tool> {...}`.
  Only look at `$ ` lines for commands (the task prompt is not in the log; other text is output).
  FAIL on a `$ ` line matching (case-insensitive):
  `8081`, `agent-keys`, `printenv`, `^\$ env\s*$`, `rebuild_and_flash|safe_flash|flash-write|flash-erase`,
  `UV4(\.exe)?.* -f`, `pyocd\s+(flash|erase|reset)`,
  `livewatch.*\s(poke|reset|halt|step|resume|bp|wp|rtt-write|fault-erase)(\s|$)`,
  `git\s+(commit|push|reset|checkout|stash|clean|restore)\b`, `rm\s+-rf`.
  WARN "loop" when the same `$ ` or `← Edit` line occurs 5+ times.
  WARN with the count when lines contain `denied` or `permission` rejections (count only).
- `check_paths(paths, task_text) -> list[(level, msg)]`. paths are repo-relative with `/`.
  FAIL if any path starts with `API/ TASK/ BSP/ USER/ FreeRTOS/ stm32_lib/ OBJ/ .git/`, or matches
  `.agent-ops/*.sh` or `.agent-ops/*.ps1`, or its name is `modules.yaml`, or it contains `agent-keys`.
  WARN "outside task scope" listing each path whose full path AND basename are both absent from task_text.
- `pick_tests(paths) -> list[list[str]]` argv lists, deduplicated, stable order:
  `ground_station/<pkg>/**.py` -> `["python","-m","pytest","-q","-x","ground_station/<pkg>"]`;
  any `docs/dashboard-platform/shell/**.js` or `ground_station/service/tests/*.js` ->
  `["node","ground_station/service/tests/node_harness.js"]`;
  `.agent-ops/**.py` -> `["python","-m","pytest","-q",".agent-ops/tests"]`.
- `verdict(findings) -> (word, exit_code)`.
- `ignore(path) -> bool` for noise: `.agent-ops/tasks/`, `.agent-ops/logs/`, `.agent-ops/state.log`,
  `.agent-ops/inbox/`, `logs/`, `__pycache__/`, `.pyc`.

## main() checks, in order (subprocess, text mode, cwd = repo root)
1. running: `wsl -e bash -c "pgrep -af 'run-worker[.]sh'"`; if the task id appears -> print
   `VERDICT: RUNNING`, exit 3.
2. exit: last line in `.agent-ops/state.log` containing `[<id>] EXIT: rc=N`. rc!=0 FAIL; missing WARN.
3. result: `.agent-ops/tasks/<id>.result.md` must exist and be >= 600 bytes (smaller = stub/idle death) else FAIL.
   Print its first 8 non-empty lines indented by 4 spaces.
4. changed files: if `.agent-ops/tasks/<id>.pre-tree` exists (a git tree hash): build the current tree
   the same way the spawn did — copy `.git/index` to a temp file, set env `GIT_INDEX_FILE` to it (only in
   the subprocess env), `git -c core.safecrlf=false add -A -- . :(exclude)OBJ`, `git write-tree`, delete
   the temp file — then `git diff --name-status <pre> <now>`. Drop ignore() paths. Print up to 25 as
   `    M path`, plus the line `    diff: git diff <pre> <now> -- <path>`.
   Else fall back to `git status --short` minus the lines in `<id>.pre-status` (strip a UTF-8 BOM) and WARN
   "no pre-tree: files already dirty at spawn are invisible".
   Then run check_paths(changed, task text from `.agent-ops/tasks/<id>.md`).
   Note in the output that other workers running at the same time also show up here.
5. log: scan_log on `.agent-ops/logs/<id>.out` (missing = WARN).
6. secrets: for the changed files plus result.md run in WSL, passing Windows paths converted to
   `/mnt/c/...`:
   `bash -c 'env -i bash -c "set -a; . ~/.config/agent-keys.env; for v in \$(compgen -e); do case \$v in *KEY*|*TOKEN*) [ -n \"\${!v}\" ] && printf \"%s\\n\" \"\${!v}\";; esac; done" | grep -lFf - -- "$@"' _ <files...>`
   Any listed file -> FAIL "key value found in <file>". NEVER print, log or store key values; only file
   names. Also FAIL on regex `sk-or-v1-[0-9a-f]{16,}` in those files (Python side).
   If you cannot make the quoting work, implement it as a tiny helper string written to a temp .sh file
   and run `wsl -e bash /mnt/c/.../tmp.sh <files>`; delete it after.
7. tests (skip with --no-tests): run each pick_tests argv (timeout 900 s); for pytest keep the last
   summary line, FAIL if it contains `failed` or `error`; for node FAIL unless output contains `ALL GREEN`.

## Rules
- Never open or cat `~/.config/agent-keys.env` yourself; never write any key.
- Do not POST anywhere, do not touch 8081, no git commit/checkout/stash/restore/reset.
- Run your unit tests with: `R/.agent-ops/win.sh "python -m pytest -q .agent-ops/tests/test_verify_task.py"`
  and make them pass. Then run `R/.agent-ops/win.sh "python .agent-ops/verify_task.py 20260923-005819 --no-tests"`
  once and paste its output in your result file.
- Write your result to the result file path given by the wrapper, with the test summary line.
