# T12: the dashboard terminal is never wired, so /api/terminal/ws returns 503

Do not contact 127.0.0.1:8081, do not touch the probe, and do not flash. Work on your branch and add unit tests. Commit with LF line endings.

## Evidence (measured live by the supervisor on 2026-09-25 at 11:17)

- `GET http://127.0.0.1:8081/api/terminal/ws` returns **503 {"error": "terminal unavailable"}**. See `ground_station/service/api.py:1670-1674`.
- `api.py:2245` accepts `terminal_manager=None`. No caller anywhere in `ground_station/service/` ever passes one, so it stays `None` on the live service. Grep `terminal_manager=` to confirm.
- `ground_station/service/terminal.py:62`: `_DEFAULT_STATE_DIR = Path(__file__).resolve().parents[3] / ".agent_state"`. That is one directory above the repo root. The docstring at line 9 says the token lives in the repo's `.agent_state/terminal-token`, so it should be `parents[2]`.
- `terminal.py:72` makes the auth token with `random.getrandbits(128)`. Auth tokens must come from `secrets` (`secrets.token_hex(16)`).

## Tasks

1. Construct a `TerminalManager` wherever the service is built and started, and pass it through so `/api/terminal/ws` is live. Read `ground_station/service/__main__.py` and the class around `api.py:2240`.
   - Keep it loopback-only, the way the rest of the service is bound.
   - If `TerminalManager()` can raise on this platform (for example no PTY backend on Windows), catch the error, log one line, and keep the current 503. Don't crash the service.
   - Say in the report which PTY backend is used on Windows.
2. Fix `parents[3]` to `parents[2]`, and switch the token to `secrets.token_hex(16)`.
3. Print the token-file path, not the token itself, once to stderr at service start, so the operator knows where to read it.
4. Add `process_rss_mb` (the service's own resident memory, rounded to 1 decimal) to the `GET /health` JSON.
   - Reuse whatever `/api/debug/memory` already uses to get `process.rss`. Do not add a new dependency.
   - It must be cheap: no gc walk and no tracemalloc.
5. Tests:
   - The service builder passes a manager.
   - The token path resolves inside the repo.
   - The token is 32 hex chars and comes from `secrets` (monkeypatch it).
   - `/health` has `process_rss_mb`.
   - Then run the whole tree once as the last step: `python -m pytest ground_station -q` via win.sh.

Write `.agent-ops/out/t12-report.md` with: the root cause at file:line, the diff summary, the PTY backend, and the verbatim test counts.
