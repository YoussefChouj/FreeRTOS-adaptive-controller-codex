<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. No scratch scripts at the repo root,
   nothing under `.agent-ops/served/`.
2. Write complete content. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. One statement per line; no `;`-joined statements. Do not reformat code you did not need to touch.
6. If a command or edit fails twice the same way, change approach; never repeat an identical call.
7. Write the digest `.agent-ops/out/wp6-r2.md` BEFORE printing DONE.
8. Output: no preamble, no summary prose.
9. Never POST to any HTTP endpoint, never arm, flash, or touch firmware, the schema or the dashboard.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your wp6-r1 commit). The supervisor ran the gate;
it failed on ruff, verbatim:
  FAIL ruff: 1 new findings
    ground_station/service/tests/test_flight_campaign_skill.py:8 [F401] `pytest` imported but unused
  GATE FAIL: ruff
Your 3 tests pass locally ("3 passed in 1.01s"); docs/skills/flight-campaign.md is accepted as is.
</context>

<allow-list>
ground_station/service/tests/test_flight_campaign_skill.py
.agent-ops/out/wp6-r2.md          (digest)
</allow-list>

<spec>
Remove the unused `import pytest` line (line 8) from ground_station/service/tests/test_flight_campaign_skill.py.
Change nothing else in that file and do not touch docs/skills/flight-campaign.md.
</spec>

<tests>
Run and paste the last line of each:
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_flight_campaign_skill.py
  ruff check ground_station/service/tests/test_flight_campaign_skill.py   (if ruff is missing, write "ruff: not installed")
</tests>

<digest>
`.agent-ops/out/wp6-r2.md`, at most 15 lines: files changed, each command + its last 3 output lines,
deviations from this spec (should be none).
</digest>
