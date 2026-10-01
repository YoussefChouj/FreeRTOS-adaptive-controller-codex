<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep every existing function name, signature and return shape. Add no new functions in gate.py.
7. Python 3.10+, standard library only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp1-r1.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose.
</guardrails>

<context>
Measured by the supervisor on 2026-10-01 (branch wp/1 = main at 7c45207):
- `make_shims` is at `.agent-ops/gate.py` lines 34-44. It reads each shimmed header with
  `src_path.read_text(encoding="utf-8")` (line 39) and writes it with
  `dest_path.write_text(text, encoding="utf-8")` (line 42).
  A vendor header holding non-UTF-8 bytes (e.g. a GBK comment) raises UnicodeDecodeError and the
  gate dies with a traceback.
- `python -m pytest -q -p no:cacheprovider tests/agent_ops` -> "14 passed, 1 skipped".
- The existing test for make_shims is `test_make_shims` at tests/agent_ops/test_gate.py:186.
</context>

<allow-list>
.agent-ops/gate.py
tests/agent_ops/test_gate.py
.agent-ops/out/wp1-r1.md          (digest)
</allow-list>

<spec>
Change only the body of `make_shims(root: str, dest: str) -> int` in `.agent-ops/gate.py`:
  - data = src_path.read_bytes()
  - text = data.decode("latin-1")
  - text = re.sub(pattern, repl, text, flags=re.S)        (unchanged)
  - dest_path.write_bytes(text.encode("latin-1"))
Every byte outside the replaced regions must stay identical (no newline translation, no re-encoding).
No other behaviour change anywhere in gate.py. Same return value (number of files written).
</spec>

<tests>
Add ONE new test to tests/agent_ops/test_gate.py (keep every existing test unchanged and passing):
  L. test_make_shims_non_utf8(tmp_path): create <tmp_path>/FreeRTOS/portable/RVDS/ARM_CM4F/portmacro.h
     with write_bytes; content = b"/* " + "中文".encode("gbk") + b" */\n" followed by one
     `__asm { ... }` block (ASCII). Call gate.make_shims(str(tmp_path), str(dest)) with dest a new dir.
     Assert: returns 1; out = (dest / "portmacro.h").read_bytes(); "中文".encode("gbk") in out;
     b"__asm" not in out.
Run, in this order, and paste the last 3 output lines of each into the digest:
  `python -m py_compile .agent-ops/gate.py`
  `python -m pytest -q -p no:cacheprovider tests/agent_ops`        (expected: 15 passed, 1 skipped)
  `ruff check tests/agent_ops .agent-ops/gate.py` if ruff exists (if not, write "ruff: not installed").
</tests>

<digest>
`.agent-ops/out/wp1-r1.md`, at most 25 lines: files changed, each command + its last 3 output lines,
deviations from this spec (should be none), open risks.
</digest>
