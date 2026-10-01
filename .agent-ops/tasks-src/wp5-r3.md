<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Create NO other files anywhere: no scratch
   scripts (no fix_*.js, no helper .py) at the repo root or elsewhere; edit files directly. Nothing under `.agent-ops/served/`.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. One statement per line; never join statements with `;` on one line (JS statement-terminating `;` is fine).
4. After each Python edit run `python -m py_compile <file>`; after each JS edit run `node --check <file>`.
5. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" / "ALL CHECKS PASSED" in the output.
6. Do not reformat code you did not need to touch.
7. If a command or edit fails twice the same way, change approach; never repeat an identical call.
8. Write the digest `.agent-ops/out/wp5-r3.md` BEFORE printing DONE.
9. Output: no preamble, no summary prose.
</guardrails>

<context>
FIX round for your own previous work (HEAD = your wp5-r2 commit). Last round. The supervisor ran the gate:
```
FAIL size: 1183/1000 lines
FAIL scope:
  outside allow-list: fix_end.js
  outside allow-list: fix_h.js
  outside allow-list: fix_h2.js
  outside allow-list: fix_p.js
SKIP clang-tidy: no C changes
PASS ruff: 2 files clean
FAIL pytest:
  >       assert len(panels) == 18
  E       AssertionError: assert 19 == 18
  FAILED ground_station/platform/tests/test_capability_manifest.py::test_capability_manifest_structure
GATE FAIL: size,scope,pytest
```
- The four fix_*.js files at the repo root (474 lines) are your scratch scripts; they must be deleted.
- The supervisor decided: the panel-count line IS needed. Re-apply your round-1 one-line change in
  test_capability_manifest.py (`assert len(panels) == 18` -> `== 19`), nothing else in that file.
- Review finding P6 in campaign-panel.js: fetchState's success path calls `showError('')` on every 1 s poll, so a
  409/503 error from Go (or a Pause/Land/Abort/allow_agent_arm error) disappears within one poll tick.
</context>

<allow-list>
docs/dashboard-platform/shell/plugins/campaign-panel.js
ground_station/service/tests/campaign_panel_harness.js
ground_station/platform/tests/test_capability_manifest.py      (only the 18 -> 19 line)
fix_end.js fix_h.js fix_h2.js fix_p.js                         (delete only: `git rm`)
.agent-ops/out/wp5-r3.md                                       (digest)
</allow-list>

<spec>
1. `git rm fix_end.js fix_h.js fix_h2.js fix_p.js`.
2. test_capability_manifest.py: change only `assert len(panels) == 18` to `assert len(panels) == 19`.
3. P6: keep poll errors and action errors apart. Poll (GET state) errors go to their own element `cp-poll-error`,
   which a successful poll clears. Action errors (go, pause, land, abort, agent/control POST) stay in `cp-error`
   until the next action's reply; a poll never clears `cp-error`.
4. Harness check h: after the 409 and after the 503, fire one poll tick with a 200 state reply, let promises settle,
   and assert the exact error string is still shown in `cp-error`. Add under tag g or h: a non-200 GET state reply
   shows its error in `cp-poll-error`, and the next 200 poll clears it. Keep the 12 tags a-l.
</spec>

<tests>
Run and paste last lines:
  node ground_station/service/tests/campaign_panel_harness.js
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_panel.py ground_station/platform/tests/test_capability_manifest.py ground_station/service/tests/test_motor_bench_panel.py
  python ground_station/platform/capability_manifest.py --check
  git status --short        (must list nothing untracked)
  git diff --stat 894e681   (no fix_*.js; capability_manifest.json at most 20 lines)
</tests>

<digest>
`.agent-ops/out/wp5-r3.md`, at most 25 lines: files changed, each command + its last 3 output lines,
deviations from this spec (should be none), open risks.
</digest>
