# P4 — /state speed-up + declutter (dashboard service + UI)
Read docs/dashboard-platform/AGENT_GUIDE.md first. No live service here: profile /state offline with a synthetic ingest load (TestClient / direct handler calls).
1. GET /state takes ~1 s per call (live, measured). Profile it offline (tests/fakes or a local service instance on
   ANOTHER port with a fake/replay source — never the live 8081 except GET). Find where the time goes, fix it
   (caching, avoiding per-request recomputation, lazy fields), target < 100 ms, and add a regression test that bounds
   the work (e.g. call counts), not wall time. Report before/after numbers you measured and how.
2. Declutter: list dead/duplicate API routes, unused UI panels/buttons and backend code paths with evidence (no
   references in UI + tests). Remove only clearly dead code; the flight-test workflow must be few clicks:
   connect -> preset -> REC -> notes -> stop -> report. Do NOT break: streaming, REC, notes, flight-test panel,
   reports, replay, presets (ground_station/service/e2e_validate.py checks these; keep its offline tests green).
Digest: .agent-ops/out/vp4.md

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
