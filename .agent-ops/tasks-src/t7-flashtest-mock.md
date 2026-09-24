# Task T7: stop `test_flash_retries_a_transient_rddi_dap_failure` from touching the real probe

File: `ground_station/flashtool/tests/test_rebuild_and_flash.py` (test near line 213).
Symptom: in a full-tree pytest run this test hangs (faulthandler at 120 s) because it mocks
`rf.sf._run_uv4` and `time.sleep` but NOT the post-flash reset / target check / any pyOCD or
livewatch call that `rebuild_and_flash` makes after a successful flash. With a real probe attached
it blocks on hardware.

Do:
1. Read `ground_station/flashtool/rebuild_and_flash.py` and trace every call made on the success
   path after the UV4 flash (reset, verify, target check, telemetry wait, subprocess, socket).
2. In that test (and any sibling test in the same file with the same gap), monkeypatch those calls
   the same way the file already patches others. Touch test code only; do not change production code
   unless a call is impossible to patch (then explain why in the result).
3. Prove it: run `python -m pytest ground_station/flashtool -q -p no:cacheprovider -o faulthandler_timeout=60`
   and paste the summary line. Also add a guard: patch any probe-opening function to raise
   `AssertionError("real probe touched")` inside the test so a regression fails fast instead of hanging.

HARD ORDER (a previous attempt ran these tests on Windows before patching and wedged the real
probe's USB interface): add the guard (patch `pyocd.core.helpers.ConnectHelper.session_with_chosen_probe`
and `ground_station.livewatch.transport` connect paths to raise) BEFORE running any test. Then run
ONLY this one file, never the whole `ground_station/flashtool` dir:
`.agent-ops/win.sh "python -m pytest ground_station/flashtool/tests/test_rebuild_and_flash.py -q -p no:cacheprovider -o faulthandler_timeout=60"`
(adjust the path to your worktree). Note: the pre-flash `elf_matches_target` (rebuild_and_flash.py ~line 250/447)
also opens the probe; patch it too.

Rules: no build, no flashing, no probe use, no contact with 127.0.0.1:8081. Commit on your branch
(message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`), then write the result
file with the diff stat and the pytest summary line.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>.
