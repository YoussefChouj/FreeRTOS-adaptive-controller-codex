Status: BLOCKED (one line short of PASS: the gate fails only on scope, for a one-line test change the brief's allow-list did not foresee)
Commits: (on wp/5)
  c2c4810 - worker wp5-r1 (agy/gemini-3.1-pro-high, OK)
  cca85b6 - worker wp5-r2 (agy/gemini-3.1-pro-high, OK)
  1e9eb80 - worker wp5-r3 (agy/gemini-3.1-pro-high, OK)
  + this report commit (task files r1-r3 + report)
Gate: GATE FAIL: scope   (PASS size: 732/1000 lines; scope: outside allow-list: ground_station/platform/tests/test_capability_manifest.py; PASS ruff: 3 files clean; PASS pytest: 2 passed in 3.62s)
Verification (run by me, Windows laptop):
  base pytest set (manifest, all_panels_audit, campaign_api, motor_bench) on 894e681 -> 1 failed, 16 passed, 1 warning in 36.09s
  same set on HEAD -> 1 failed, 16 passed, 1 warning in 27.27s (same failure: test_all_panels_audit, see below)
  pytest test_campaign_panel.py -> 1 passed in 3.24s (wrapper asserts rc 0, ALL CHECKS PASSED, tags a-l each at a line start)
  node campaign_panel_harness.js / node node_harness.js -> NOT RUN by me: plain `node ...` was denied (see last line).
    Harness evidence on Windows is the pytest wrapper above, which runs node itself. Worker (VPS) reports node_harness ALL GREEN.
  git diff --stat workflow-b...HEAD -> 10 files, 822 insertions(+), 1 deletion(-); capability_manifest.json +11 only
Worker rounds: 3/3, lane agy/gemini-3.1-pro-high every round
  r1 -> r2: gate FAIL size 7026 (manifest JSON rewritten LF over the CRLF blob) + scope (worker changed the panel count
     18->19 in test_capability_manifest.py); review: unescaped innerHTML, timer leak on remount, silent poll errors,
     arm toggle not reverted on POST failure, double-click Go; harness checks d/j vacuous, c/e/h too loose, f poked private state.
  r2 -> r3: gate FAIL size 1183 + scope (4 scratch fix_*.js at repo root) + pytest (restored test: assert 19 == 18);
     review: each 1 s poll called showError('') and wiped a 409/503 Go error within a tick.
  r3: scratch files removed, count line re-applied (my decision, see below), poll vs action errors split. Gate: scope only.
Deviations / open questions:
  - CEO decision needed: adding a panel makes test_capability_manifest.py::test_capability_manifest_structure
    (`assert len(panels) == 18`) fail, and the gate's pytest step runs it. Without the 18->19 line the gate fails pytest;
    with it, it fails scope. I kept the line (r3) so the code is correct. To accept it, re-run the gate with an added
    `--allow ground_station/platform/tests/test_capability_manifest.py`; this then passes unless something else is out of scope
    (diff is the one line shown by `git diff workflow-b...HEAD -- ground_station/platform/tests/test_capability_manifest.py`).
  - Pre-existing, not WP-5: test_all_panels_audit fails on Windows on base and HEAD with UnicodeDecodeError
    ('charmap' codec, byte 0x8f) because its subprocess.run has no encoding=; worker reports that on Linux it fails on
    preset-picker.js ("sandbox.pluginInit is not a function") on base too. So the audit gives no evidence for campaign-panel.js on
    either host; the worker states campaign-panel itself passes it (claim, not verified by me).
  - The worker cannot see `workflow-b` on the VPS; it used 894e681 (same commit).
  - The harness uses real setTimeout(50) chains to settle promises, not fully fake timers; the panel's 1 s poll is faked.
  - No submitCommand/gatedCommand in campaign-panel.js (check k; grep-able).
MANAGER.md: `node <file>` (plain, one command) and env-prefixed commands (`PYTHONUTF8=1 python ...`) were denied, so the brief's
  node acceptance commands cannot be run by the manager as written; the rules allow pipes into tail, which I used.
