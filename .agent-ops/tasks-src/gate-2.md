<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep every existing function name, signature and return shape. Add only the functions listed below.
7. Python 3.10+, standard library only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/gate-2.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your gate-1 commit). The supervisor ran the gate on
the Windows laptop with the real clang-tidy and planted two bugs in USER/main.c (an `if (a > 1);` and a
division by zero). Measured results:
- The gate printed "PASS clang-tidy: 1 files clean". FALSE PASS.
- Cause 1: compile_commands.json is untracked, so it only exists in the MAIN checkout, not in a
  `git worktree`. Without it clang-tidy cannot find includes and stops.
- Cause 2: even with the right database, clang reports 6 compile errors from vendor headers:
  5 x "unsupported architecture 'thumbv7em' for MS-style inline assembly" in
  FreeRTOS/portable/RVDS/ARM_CM4F/portmacro.h (Keil `__asm { ... }` blocks), and 1 x
  "unknown register name 'vfpcc' in asm" in stm32_lib/core_cmFunc.h:614
  (`__ASM volatile ("VMSR fpscr, %0" : : "r" (fpscr) : "vfpcc");`).
  Compile errors silently disable clang-analyzer and bugprone-suspicious-semicolon. They are in headers,
  not on changed lines, so filter_to_changed() dropped them and the step said "clean".
- Verified fix: copies of those two headers with `re.sub(r"__asm\s*\{.*?\}", ";", text, flags=re.S)` and
  `re.sub(r'\s*:\s*"vfpcc"', "", text)` in a temp dir, passed as `--extra-arg-before=-I<tempdir>`,
  gave "0 errors" and both planted bugs were reported (bugprone-suspicious-semicolon, clang-analyzer-core.DivideZero).
- Your test file also fails your own gate's ruff step:
  tests/agent_ops/test_gate.py:3 [F401] `os` imported but unused
  tests/agent_ops/test_gate.py:135 [E701] Multiple statements on one line (colon)
</context>

<allow-list>
.agent-ops/gate.py
tests/agent_ops/test_gate.py
.agent-ops/out/gate-2.md          (digest)
</allow-list>

<spec>
Add to `.agent-ops/gate.py`:
  CLANG_SHIMS = [
      ("FreeRTOS/portable/RVDS/ARM_CM4F/portmacro.h", r"__asm\s*\{.*?\}", ";"),
      ("stm32_lib/core_cmFunc.h", r'\s*:\s*"vfpcc"', ""),
  ]
  find_compdb(root: str) -> str | None
      root if <root>/compile_commands.json exists; else the main checkout = parent directory of
      git("rev-parse", "--path-format=absolute", "--git-common-dir").strip() if it contains
      compile_commands.json; else None.
  make_shims(root: str, dest: str) -> int
      for each CLANG_SHIMS entry whose <root>/<path> exists: write re.sub(pattern, repl, text, flags=re.S)
      to <dest>/<basename>. Return the number of files written.

Change step 3 (clang-tidy), keeping everything else:
  - compdb = find_compdb(root); None -> SKIP "clang-tidy: no compile_commands.json".
  - Inside `with tempfile.TemporaryDirectory() as shim:` call make_shims(root, shim), then run
    [exe, "-p", compdb, f"--extra-arg-before=-I{shim}", f"--checks={CLANG_TIDY_CHECKS}", "--quiet", *files].
  - all_findings = parse_clang_tidy(out, root).
    errors = [f for f in all_findings if f.code == "clang-diagnostic-error"] (NOT filtered to changed lines).
    If errors: FAIL "clang-tidy: <n> compile errors (analysis incomplete)", details = the errors.
    Else findings = filter_to_changed(all_findings, changed_lines) as before.
Also: to_rel must strip a leading "./" from relative paths.
Fix the two ruff findings in tests/agent_ops/test_gate.py.
</spec>

<tests>
Add to tests/agent_ops/test_gate.py (keep all existing tests passing):
  H. make_shims on a tmp_path tree whose portmacro.h has two `__asm { ... }` blocks (one containing a
     /* comment */) and whose core_cmFunc.h has the vfpcc line -> returns 2; the written portmacro.h
     contains no "__asm", the written core_cmFunc.h contains no "vfpcc".
  I. find_compdb: returns root when root/compile_commands.json exists; in a real `git worktree add`
     of a tmp repo whose main checkout has compile_commands.json, called with the worktree root,
     returns the main checkout path (compare with os.path.samefile).
  J. End-to-end like G1 but a 3-line .c change, clang-tidy lookup stubbed to a fake exe path,
     find_compdb stubbed to return tmp_path, and gate.run stubbed so the clang-tidy command returns
     "<tmp_path>/inc/x.h:5:2: error: unsupported architecture [clang-diagnostic-error]"
     -> main returns 1, output contains "compile errors (analysis incomplete)".
  K. to_rel("./USER/main.c", root) == "USER/main.c".
Run: `python -m pytest -q -p no:cacheprovider tests/agent_ops` and `ruff check tests/agent_ops .agent-ops/gate.py`
if ruff exists (if not, write "ruff: not installed" in the digest).
</tests>

<digest>
`.agent-ops/out/gate-2.md`, at most 25 lines: files changed, each command + its last 3 output lines,
deviations from this spec (should be none), open risks.
</digest>
