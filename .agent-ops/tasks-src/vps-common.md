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
