# P4 — /state speed-up + declutter (dashboard service + UI)
Read docs/dashboard-platform/AGENT_GUIDE.md first (GET /api/routes, UI selectors, browser_smoke).
1. GET /state takes ~1 s per call (live, measured). Profile it offline (tests/fakes or a local service instance on
   ANOTHER port with a fake/replay source — never the live 8081 except GET). Find where the time goes, fix it
   (caching, avoiding per-request recomputation, lazy fields), target < 100 ms, and add a regression test that bounds
   the work (e.g. call counts), not wall time. Report before/after numbers you measured and how.
2. Declutter: list dead/duplicate API routes, unused UI panels/buttons and backend code paths with evidence (no
   references in UI + tests). Remove only clearly dead code; the flight-test workflow must be few clicks:
   connect -> preset -> REC -> notes -> stop -> report. Do NOT break: streaming, REC, notes, flight-test panel,
   reports, replay, presets (ground_station/service/e2e_validate.py checks these; keep its offline tests green).
Digest: .agent-ops/out/win-p4.md
