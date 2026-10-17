Ignore it. `test_mrac_log_replay.py::test_replay_synthetic_hover` (`#include <io.h>` in `mrac_log_replay_host.c`) is a
pre-existing, platform-dependent failure outside your scope: do not edit that test or that C file. Name it in the PR
body as pre-existing, with the one error line.

Then open ONE PR against `overnight-2026-10-08` now:
1. Scoped tests: `python -m pytest ground_station/analysis/tests/test_live_tracker.py` plus the test file of every
   module you touched, and `ruff check` on the changed .py files only.
2. Another open session fixes the 5 x E702 lines in `ground_station/analysis/live_tracker.py`. Do not touch lines you
   did not need to change there; keep your diff in that file minimal so the two PRs merge cleanly.
3. CRLF line endings in .py like the existing files. No new dependencies.
4. PR body: each test with its result, and every timing number you report marked measured (with the replay file it
   came from) or computed.

Do not ask again before the PR unless a test you own fails and you cannot fix it.
