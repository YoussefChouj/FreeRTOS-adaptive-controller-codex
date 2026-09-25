# T12b: /api/terminal/ws hangs and never answers the handshake

Do not contact 127.0.0.1:8081, do not touch the probe, and do not flash. Instead, reproduce the problem in a test that starts its own `ApiServer` on an ephemeral port (port 0), with a fake service like the existing tests in `ground_station/service/tests/`. Commit with LF line endings.

## Evidence (live, merged main at commit cd81637, Windows 10, Python 3.11, measured 2026-09-25 12:12)

- Service stderr: `[terminal] token file: ...\.agent_state\terminal-token`. So `TerminalManager()` was constructed and passed through (T12, `ground_station/service/__main__.py` → `platform/shell.py` → `ApiServer(terminal_manager=...)`).
- A python `websockets` client (proxy=None) with no token, a bad token, and the good token each ended in `TimeoutError: timed out during opening handshake` after 15 s.
- `curl -i` with the Upgrade headers and the good token got no bytes back within 8 s. A plain `curl -i .../api/terminal/ws?token=bad` also got no bytes (it was expected to get 401).
- The rest of the service answers normally (`/health` is fine).

## Tasks

1. Find why the route blocks before it writes any response. Suspects:
   - The route dispatch in `api.py` around the `/api/terminal/ws` branch (search it).
   - The token check order.
   - `TerminalManager.handle_*` blocking on PTY spawn in the request thread.
   - A lock.
   - `winpty`/`pywinpty` or a ConPTY call blocking.
   - `ThreadingHTTPServer` not being used, which would make one hung request block others.
   - Read `ground_station/service/terminal.py`.
2. Fix it so that:
   - a missing or bad token gets **401 immediately**;
   - a good token completes the RFC 6455 handshake (101 with a correct Sec-WebSocket-Accept);
   - raw PTY bytes flow both ways;
   - `{"type":"resize","rows","cols"}` JSON is applied;
   - the shell is killed when the socket closes.
   - On Windows, use whichever PTY backend `terminal.py` intends. If none is available, fall back to a pipe-backed `cmd.exe`, or `powershell -NoLogo`, and say so.
3. Add a regression test that runs a real server on port 0 with a real `TerminalManager` using a temporary state dir:
   - no token → 401;
   - bad token → 401;
   - good token → 101;
   - send `echo t12b-ok` plus a newline and read it back within 10 s.
   - Use the `websockets` package already installed, or a raw socket.
4. Run `python -m pytest ground_station/service -q`, then the whole tree once: `python -m pytest ground_station -q`.

Report in `.agent-ops/out/t12b-report.md`: the root cause at file:line, the fix, the PTY backend, and the verbatim test counts.
