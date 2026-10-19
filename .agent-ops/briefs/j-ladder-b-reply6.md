Yes, proceed, with one change: the setting is `wire_format` with the values `"f32"` (default) and `"i16"`, not a
boolean `narrow`. Use the same name in the plan (`plan["wire_format"]`), the manifest and the helper arguments, so
that a third format later needs no rename. Import `stream_var_bytes` / `stream_frame_bytes` from `stream.py`; no copy.
New i16 tests derive their expected counts from `stream_frame_bytes`, `HOST_SAFE_WIRE_PCT` and `GUARD_SEND_HZ` (100);
the existing f32 tests stay unchanged. Revert the whitespace-only lines in `API/subscribe.c` (around L505, L523).
Then run the scoped pytest, ruff on changed .py (clean), `python tools/host_tests.py` (26/26), CRLF, and open ONE PR
against `overnight-2026-10-08`. In the PR body list each test file with its result. Do not ask again.
