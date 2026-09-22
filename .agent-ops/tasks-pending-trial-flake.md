# Trial task: fix a flaky service test (2026-09-23)

`ground_station/service/tests/test_agent.py::test_mode_off_cancels_running_plan_and_returns_423`
fails intermittently with `ConnectionAbortedError: [WinError 10053]`. Cause: the HTTP
handler in `ground_station/service/` answers 423 (mode off) BEFORE reading the POST body,
so the server closes a socket that still has unread data and Windows resets it.

Fix: in the handler, read (drain) the request body up to Content-Length before sending
any early error response (423, 4xx), for every POST route that can return early. Put
the draining in ONE place (a helper used by all early returns), not copy-pasted.
Do not change route behaviour or status codes.

Verify by running that test 15 times through win.sh (Windows python), for example:
`python -m pytest "ground_station/service/tests/test_agent.py::test_mode_off_cancels_running_plan_and_returns_423" -q --count 15`
(or a shell loop, if pytest-repeat is missing), and then `python -m pytest ground_station/service -q`
once. Paste both result lines verbatim. Do NOT POST to the live service on 8081.
Touch only files under `ground_station/service/`. Exit cleanly when done.
