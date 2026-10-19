Yes, open the PR, with one more small step first.

1. Reverting the global `ruff --fix --unsafe-fixes` was right. But do fix the 5 `E702` items in `live_tracker.py` by
   hand: one statement per line, no behavior change, only those lines. `ruff check` on every changed .py file must be
   clean (this was handed to your session on purpose; no other session touches `live_tracker.py`).
2. The `io.h` failure in `mrac_log_replay_host.c` is a Windows header on a Linux runner: list it in the PR body as
   pre-existing with its error line, do not change that file.
3. Open ONE PR against `overnight-2026-10-08` with each test and its result. Do not ask again.
