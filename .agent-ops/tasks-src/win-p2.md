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
Digest: .agent-ops/out/win-p2.md
