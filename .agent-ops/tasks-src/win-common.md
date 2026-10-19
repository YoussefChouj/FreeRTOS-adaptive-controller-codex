## Rules (all Windows workers)
- You work ONLY inside your git worktree (the cwd). Commit on your branch when done; do not merge, push, or touch other worktrees.
- NEVER: POST/PUT to http://127.0.0.1:8081 (GET is OK); run `ground_station.livewatch` or any pyOCD/probe command; flash
  (`rebuild_and_flash`, `UV4 -f`); send any drone command; restart the dashboard service; hard-delete files.
- Firmware: Keil ARMCC V5.06 C89 (declarations at block top, no VLAs, no `//`-only C99 idioms beyond what the file uses).
  You MAY build to check compile: from USER/: `C:\Keil_v5\UV4\UV4.exe -b -t JX_FLY -j0 JX_FLY.uvprojx -o build_log.txt`
  (exit 1 is normal; grep build_log.txt for "Error(s)" and `C4051E`). Do not commit OBJ/ or build_log.txt changes.
- Many files are CRLF: preserve line endings (edit with tools that keep CRLF).
- Tests: `set PYTHONUTF8=1` then `python -m pytest -q -p no:cacheprovider <dirs you touched>`. Host C tests:
  tests/firmware_host with `C:\MinGW\bin\gcc -std=c89 -pedantic -Wall -Werror`.
- Minimum code that solves the problem. Match surrounding style. No patch_*.py, no binaries in commits.
- Only report numbers you measured. Digest at the path named below, under 80 lines: what changed (file:line),
  how verified (exact test summary lines), open issues, anything the supervisor must verify live.
