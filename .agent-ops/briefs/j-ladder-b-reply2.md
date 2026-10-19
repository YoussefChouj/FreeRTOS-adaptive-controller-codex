Plan approved as written (0x27 SUBSCRIBE_STREAM_NARROW_CMD, 0x0D schema reply, steps 1-6). Add 0x27 and 0x0D to the frame/command list in subscribe.h.

Go to the end now without further check-ins: implement, then run `python tools/host_tests.py`, pytest on every test file you changed or added, and `ruff check` on every Python file you changed. Then open the PR.

Only stop and ask if the brief contradicts the code, or a test you did not touch starts failing because of your change. Ignore GitHub "CI failed" messages: CI is already red on the base branch for reasons outside this task; do not try to fix it.

In the PR description list: files changed, each test command with its result, and the computed (not measured) bytes per sample saved for a typical narrowed frame.
