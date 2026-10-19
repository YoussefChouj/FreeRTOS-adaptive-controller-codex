# P2 — bug hunt (dashboard service + ground station + firmware task code)
Read docs/dashboard-platform/AGENT_GUIDE.md first. Review ground_station/service/, ground_station/comm/,
ground_station/livewatch/ (host side) and TASK/*.c, API/ekf.c for real bugs: wrong results, races, leaks,
unhandled errors that hide failures, unit mistakes, stale caches. Confirmed bugs you MUST fix (with a test each):
1. ground_station/service/e2e_validate.py `check_rec`: on any early failure it returns without POSTing
   /api/recording/stop, leaking a live recording (happened: 155 MB junk session). Fix: try/finally that always stops
   a recording it started; poll GET /api/recording up to ~3 s for `recording: true`; put the HTTP status and body
   excerpt in the failure detail. Also get_json/post_json swallow exceptions as (0, None): keep the exception text
   in the returned evidence so failures are diagnosable.
2. The service caches ELF symbol addresses at startup; after a reflash, subscribe slots decode wrong memory
   (measured: slot0.real_voltage read 0.0 until service restart). Make the service detect OBJ/JX_FLY.axf mtime/sha
   change and reload symbols (or refuse to decode and flag `elf_stale` in /state) — choose the smaller safe change.
For everything else: fix only if confirmed by a failing test you write first; otherwise list it.
Write findings doc docs/validation/p2-bug-hunt.md (each: file:line, severity, evidence, fixed?/test name).
Digest: .agent-ops/out/vp2.md

## Rules (VPS Linux workers)
- Work ONLY inside your git worktree (cwd). Commit on your branch when done; do not merge or touch other worktrees.
- There is no drone, no probe, no Keil and no dashboard here. Never flash, never run livewatch/pyOCD, never POST anywhere.
- Firmware: Keil ARMCC V5.06 C89 (declarations at block top, no VLAs, no C99). You cannot build with Keil; the
  supervisor builds. Check C with host tests: tests/firmware_host using `gcc -std=c89 -pedantic -Wall -Werror`.
- Many files are CRLF: preserve line endings.
- Tests: `PYTHONUTF8=1 python3 -m pytest -q -p no:cacheprovider <dirs you touched>`; paste the exact summary lines.
- Minimum code that solves the problem. Match surrounding style. No patch_*.py, no binaries in commits.
- Only report numbers you measured. Digest at the path named in the task, under 80 lines: what changed (file:line),
  how verified (exact test summary lines), open issues, anything the supervisor must verify live.
