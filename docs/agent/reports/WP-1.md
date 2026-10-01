Status: DONE
Commits: 8071e17 - worker wp1-r1 (agy/gemini-3.1-pro-high, OK); d79a18f - agent-ops: WP-1 report and r1 task; + this fix-up (on wp/1)
Gate: GATE PASS   (PASS size 20/200, PASS scope 3 files, SKIP clang-tidy no C changes, PASS ruff 2 files clean, PASS pytest 15 passed)
Verification: python -m pytest -q -p no:cacheprovider tests/agent_ops -> 15 passed, 1 skipped in 10.71s
Verification: python .agent-ops/gate.py --base main --allow .agent-ops/gate.py --allow tests/agent_ops/test_gate.py --allow ".agent-ops/out/*" -> GATE PASS
Worker rounds: 1/3, lane agy-vps, model agy/gemini-3.1-pro-high (from the worker commit subject
  "worker wp1-r1 (agy/gemini-3.1-pro-high, OK)"); no bounces
Diff check (git diff main...HEAD): make_shims now read_bytes -> decode latin-1 -> re.sub (unchanged) ->
  encode latin-1 -> write_bytes. Nothing else in gate.py changed. New test test_make_shims_non_utf8 matches the brief
  (GBK comment + one __asm block -> returns 1, GBK bytes kept, no b"__asm").
Deviations / open questions:
- Worker digest reports "16 passed" on the VPS (no skip there) against 15 passed, 1 skipped on the laptop; the laptop numbers are the evidence.
- The new test has whitespace-only blank lines (trailing spaces). The gate's ruff passes them; cosmetic only, not bounced.
- Side effect, by design: no newline translation any more (read_text/write_text used to turn CRLF into LF and then into the OS
  newline). Shim bytes now match the source except inside the replaced regions.
- wait calls per round: r1 = 1 (DONE inside the first 540 s window).
- MANAGER.md gaps: (a) step 4 merges vps/wp<id>-r<n>, but no step says how that ref gets fetched. My
  `git fetch -q vps worker/wp1-r1:refs/remotes/vps/wp1-r1` was denied ("requires approval"), but the merge still worked,
  so `wait` seems to fetch on DONE. MANAGER.md should say so. (b) The report template asks for "the model that ran". Only the worker
  commit subject has it (not the wait output or the digest), and MANAGER.md should point there.
