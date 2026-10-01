<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. No new files outside it: no scratch
   scripts at the repo root, nothing under `.agent-ops/served/`.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. One statement per line; never join statements with `;` on one line (JS statement-terminating `;` is fine).
4. After each Python edit run `python -m py_compile <file>`; after each JS edit run `node --check <file>`.
5. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" / "ALL CHECKS PASSED" in the output.
6. Do not reformat code you did not need to touch. Keep every existing function name and signature.
7. If a command or edit fails twice the same way, change approach; never repeat an identical call.
8. Write the digest `.agent-ops/out/wp5-r2.md` BEFORE printing DONE.
9. Output: no preamble, no summary prose.
</guardrails>

<context>
FIX round for your own previous work (HEAD = your wp5-r1 commit). The round-1 spec (your digest
`.agent-ops/out/wp5-r1.md` lists what you built) still holds; this round only fixes what is below. The supervisor ran the gate on the Windows laptop:
```
FAIL size: 7026/1000 lines
FAIL scope:
  outside allow-list: ground_station/platform/tests/test_capability_manifest.py
SKIP clang-tidy: no C changes
PASS ruff: 3 files clean
PASS pytest: 2 passed in 2.09s
GATE FAIL: size,scope
```
- Size cause: `docs/dashboard-platform/capability_manifest.json` is stored in git with CRLF line endings on the
  base; your regenerated file is LF, so every line changed. `git diff --stat --ignore-cr-at-eol workflow-b...HEAD --
  docs/dashboard-platform/capability_manifest.json` shows only `11 insertions(+)`.
- Scope cause: `ground_station/platform/tests/test_capability_manifest.py` is not in the allow-list. Restore it
  exactly: `git checkout 894e681 -- ground_station/platform/tests/test_capability_manifest.py`. Do NOT work around
  its panel-count assertion in any other file; the supervisor reports it upward.
Review findings on `campaign-panel.js` and the harness (supervisor read the diff):
- P1 renderState builds flight rows with innerHTML from server strings (abort_reason etc.) without escaping.
- P2 the mount function calls setInterval without clearing a previous timer, so a second mount leaks a timer.
- P3 fetchState swallows non-200 replies silently; the spec says any non-200 reply shows its `error` text.
- P4 handleAllowArmChange leaves the checkbox in the new state when the POST fails; it must revert to the
  previous value on a non-200 or network error.
- P5 a double click on Go sends two POSTs; disable Go while its POST is in flight.
- H1 check d is vacuous: it polls via the fake timer but tests the buttons synchronously, before the async render.
- H2 check f resets the panel's private `qPack.dataset.touched` from the harness; the harness must only drive the
  panel through DOM events, fake fetch replies and fake timers.
- H3 check c accepts any 6-key checklist; it must compare the whole body to
  `{campaign_path, pack_id, checklist: {all 6 ids: true}, source: 'operator'}` and count exactly one go POST.
- H4 check e uses `find`; it must count exactly one POST per route (3 POSTs in total).
- H5 check j only inspects the timer variable; after destroy it must also fire any captured interval callback and
  any pending promise, then assert zero new fetch calls.
- H6 check h must assert the exact `error` string from the fake 409 and 503 bodies is shown.
- `test_campaign_panel.py` line 30 has trailing whitespace.
</context>

<allow-list>
docs/dashboard-platform/shell/plugins/campaign-panel.js
ground_station/service/tests/campaign_panel_harness.js
ground_station/service/tests/test_campaign_panel.py
docs/dashboard-platform/shell/index.html
ground_station/platform/capability_manifest.py
docs/dashboard-platform/capability_manifest.json               (regenerated only, never hand-edited)
ground_station/platform/tests/test_capability_manifest.py      (ONLY the git checkout restore above)
.agent-ops/out/wp5-r2.md                                       (digest)
</allow-list>

<spec>
1. Restore test_capability_manifest.py from 894e681 (command above).
2. Regenerate capability_manifest.json with `python ground_station/platform/capability_manifest.py`, then convert it
   to CRLF line endings (e.g. `python -c` reading bytes, replacing b"\r\n" with b"\n" then b"\n" with b"\r\n").
   Required result: `git diff --stat workflow-b -- docs/dashboard-platform/capability_manifest.json` shows at most
   20 changed lines, and `python ground_station/platform/capability_manifest.py --check` prints "in sync".
   If `workflow-b` is not a known ref on your host, use 894e681.
3. Fix P1-P5 in campaign-panel.js (P1: escape &, <, >, ", ' in every server value put into innerHTML).
4. Fix H1-H6 in the harness; add checks for P1 (a flight with abort_reason `<b>x</b>` renders as text, no `<b>`
   element/markup in the row HTML) and P4 (POST failure reverts the toggle), printed on lines tagged under the
   existing letters (P1 under g, P4 under i). Keep the 12 tags a-l.
5. Remove the trailing whitespace in test_campaign_panel.py.
</spec>

<tests>
Run and paste last lines:
  node ground_station/service/tests/campaign_panel_harness.js
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_panel.py ground_station/service/tests/test_all_panels_audit.py ground_station/service/tests/test_motor_bench_panel.py
  python ground_station/platform/capability_manifest.py --check
  git diff --stat workflow-b
  (test_capability_manifest.py::test_capability_manifest_structure is expected to fail on the 18-vs-19 panel count
   after the restore; report its result, do not fix it.)
</tests>

<digest>
`.agent-ops/out/wp5-r2.md`, at most 25 lines: files changed, each command + its last 3 output lines,
deviations from this spec (should be none), open risks.
</digest>
