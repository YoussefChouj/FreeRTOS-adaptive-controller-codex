<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only.
2. Write complete implementations. Every function body does real work; no "...", TODO, `pass` stubs or
   "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, ...).
   Bare `except:` and `except Exception: pass` are forbidden.
6. Keep every function name, signature and return shape exactly as specified below.
7. Python 3.10+, standard library only (argparse, subprocess, shutil, fnmatch, json, re, pathlib, dataclasses).
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/gate-1.md` BEFORE printing DONE; a run without the digest is rejected.
10. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
This repo holds STM32F407 C firmware (USER/, HARDWARE/, FreeRTOS/, ...) and a Python ground station
(`ground_station/`, tests under `tests/`). Cheap worker models write diffs; a supervisor reviews them.
We want a DETERMINISTIC quality gate script the supervisor runs on a worker branch BEFORE reading the diff.
Its output is pasted back to the worker as-is, so it must be short and precise.
The gate runs on the supervisor's Windows laptop (Git Bash + Python 3.11) where clang-tidy, ruff and pytest
exist. On your machine clang-tidy and ruff are probably missing: that is fine. Unit tests must NOT call the
real clang-tidy or ruff; feed fake tool output to the parse functions and stub the runner (see Tests).
Key fact: compile_commands.json contains absolute Windows paths like
`C:/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/main.c`, and clang-tidy prints paths
like `C:\Users\...\USER\main.c:12:5: warning: ... [bugprone-foo]`. Note the drive-letter colon.
</context>

<allow-list>
.agent-ops/gate.py                (new)
tests/agent_ops/__init__.py       (new, empty)
tests/agent_ops/test_gate.py      (new)
.agent-ops/out/gate-1.md          (digest)
</allow-list>

<spec>
File `.agent-ops/gate.py`, runnable as `python .agent-ops/gate.py [options]` from anywhere inside the repo.
The repo root = `git rev-parse --show-toplevel`. The diff is always `<base>...HEAD` (merge-base, committed only).

CLI options:
  --base REF          default "main"
  --max-lines N       default 200
  --allow GLOB        repeatable; if given, every changed path must match at least one (fnmatch on the
                      repo-relative posix path). If absent, the scope step only applies the forbid list.
  --tests PATH        repeatable; extra pytest targets, added to the mapped ones.
  --strict            a SKIP counts as a failure.

Constants:
  EXCLUDE_FROM_SIZE = ["OBJ/*", "*.md", "*.hex", "*.axf", "*.htm", "*.lnp", "*.dep", "*.crf", "*.o", "*.d",
                       "*.png", "*.jpg", "*.pdf"]
  FORBID = ["OBJ/*", "*.hex", "*.axf", ".mcp.json", ".claude/*", ".env*", "*secret*", "*.pem", "*.key"]
  CLANG_TIDY_CHECKS = "-*,bugprone-*,clang-analyzer-*,-bugprone-easily-swappable-parameters"
  (fnmatch's "*" matches "/", so "OBJ/*" covers all of OBJ/.)

Data type:
  @dataclass(frozen=True) class Finding: path: str; line: int; code: str; msg: str
  `path` is always repo-relative posix (forward slashes, no leading "./").

Functions (exact names/signatures):
  git(*args: str) -> str                          run git in the repo root, return stdout; raise on rc != 0
  changed_files(base: str) -> list[str]           `git diff --name-only <base>...HEAD`, sorted
  diff_size(base: str) -> int                     sum added+deleted from `git diff --numstat <base>...HEAD`,
                                                  skipping binary rows ("-\t-") and EXCLUDE_FROM_SIZE paths
  parse_hunks(diff_text: str) -> dict[str, set[int]]
                                                  from `git diff -U0` text: for each "+++ b/<path>" file, the
                                                  set of NEW-file line numbers covered by "@@ -a,b +c,d @@" hunks:
                                                  range(c, c+d); d omitted means 1; d == 0 means none.
                                                  "+++ /dev/null" (deleted file) is skipped.
  changed_lines(base: str) -> dict[str, set[int]] parse_hunks(git("diff", "-U0", f"{base}...HEAD"))
  check_scope(paths: list[str], allow: list[str]) -> list[str]
                                                  violation strings: "forbidden: <p>" for FORBID matches, and
                                                  "outside allow-list: <p>" when allow is non-empty and p matches
                                                  none. Order = input order, forbidden checked first per path.
  to_rel(path: str, root: str) -> str             normalise an absolute or relative path (either slash style,
                                                  any drive-letter case) to repo-relative posix.
  parse_clang_tidy(text: str, root: str) -> list[Finding]
                                                  lines matching
                                                  ^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+): (?P<sev>warning|error): (?P<msg>.*?)(?: \[(?P<code>[^\]]+)\])?$
                                                  code defaults to "clang-diagnostic-error" for errors without a
                                                  bracket, "unknown" otherwise. Ignore "note:" lines and all others.
                                                  Deduplicate (same Finding once), keep first-seen order.
  parse_ruff_json(text: str, root: str) -> list[Finding]
                                                  ruff `--output-format=json`: list of objects with "filename",
                                                  "location": {"row"}, "code", "message". Empty text -> [].
  filter_to_changed(findings: list[Finding], changed: dict[str, set[int]]) -> list[Finding]
                                                  keep findings whose line is in changed[path].
  map_tests(paths: list[str], root: str) -> list[str]
                                                  changed files under tests/ named test_*.py are kept; for every
                                                  other changed *.py file "x/y/foo.py", every file
                                                  tests/**/test_foo.py that exists is added. Sorted, unique.
  run(cmd: list[str]) -> tuple[int, str]          subprocess.run in the repo root, capture stdout+stderr merged,
                                                  text mode, timeout 600 s. Module-level so tests can stub it.
  main(argv: list[str] | None = None) -> int

main() steps, each printing EXACTLY one status line "<PASS|FAIL|SKIP|WARN> <step>: <detail>",
FAIL lines followed by at most 10 indented detail lines ("  path:line [code] msg", or the violation string):
  1. size      PASS/FAIL "size: <n>/<max> lines"
  2. scope     PASS "scope: <k> files" / FAIL with violations
  3. clang-tidy  only if changed *.c files exist (else SKIP "clang-tidy: no C changes").
               Tool lookup: shutil.which("clang-tidy"), then "C:/Program Files/LLVM/bin/clang-tidy.exe" if it
               exists; none -> SKIP "clang-tidy: not installed".
               Command: [exe, "-p", root, f"--checks={CLANG_TIDY_CHECKS}", "--quiet", *changed_c_files]
               Findings = filter_to_changed(parse_clang_tidy(out, root), changed_lines). Any -> FAIL
               "clang-tidy: <n> new findings", else PASS "clang-tidy: <files> files clean".
  4. ruff      same pattern for changed *.py files: [ruff, "check", "--output-format=json", "--exit-zero",
               *files]; which("ruff") or SKIP.
  5. pytest    targets = map_tests(changed) + --tests. None -> WARN "pytest: no tests mapped"
               (counts as a failure only with --strict). Else run [sys.executable, "-m", "pytest", "-q",
               "-p", "no:cacheprovider", *targets]; rc 0 -> PASS "pytest: <last summary line>",
               else FAIL with the last 10 output lines as details.
  Final line: "GATE PASS" or "GATE FAIL: <comma list of failed steps>". Return 0 on pass, 1 on fail,
  2 if a git command fails (print "GATE ERROR: <msg>").
</spec>

<tests>
`tests/agent_ops/test_gate.py`. Load the module with importlib.util.spec_from_file_location from
`<repo>/.agent-ops/gate.py` (the dot directory is not importable by name). Required cases (one test each,
more allowed):
  A. parse_hunks: "@@ -10,0 +11,3 @@" -> {11,12,13}; "@@ -5 +5 @@" -> {5}; "@@ -7,2 +6,0 @@" -> {}; two
     files in one diff text keep separate sets; "+++ /dev/null" skipped.
  B. parse_clang_tidy on this text with root "C:/Users/Acer/repo":
       C:\Users\Acer\repo\USER\main.c:12:5: warning: narrowing conversion [bugprone-narrowing-conversions]
       C:\Users\Acer\repo\USER\main.c:12:5: warning: narrowing conversion [bugprone-narrowing-conversions]
       c:/users/acer/repo/HARDWARE/spi.c:40:1: error: unknown type name 'u8'
       C:\Users\Acer\repo\USER\main.c:13:1: note: expanded from macro
     -> [Finding("USER/main.c",12,"bugprone-narrowing-conversions","narrowing conversion"),
         Finding("HARDWARE/spi.c",40,"clang-diagnostic-error","unknown type name 'u8'")]
  C. parse_ruff_json on a 2-item JSON list -> 2 Findings with relative paths; "" -> [].
  D. filter_to_changed keeps only findings on changed lines (one kept, one dropped, one in an unchanged file).
  E. check_scope: ["OBJ/main.o","USER/main.c","tests/x.py"] with allow ["USER/*"] ->
     ["forbidden: OBJ/main.o", "outside allow-list: tests/x.py"]; with allow [] -> ["forbidden: OBJ/main.o"].
  F. map_tests with a tmp_path tree containing tests/a/test_foo.py and tests/test_bar.py:
     ["ground_station/foo.py","tests/test_bar.py","README.md"] -> ["tests/a/test_foo.py","tests/test_bar.py"].
  G. End-to-end in a temporary git repo (tmp_path, `git init -b main`, set user.email/user.name locally,
     commit a base, `git checkout -b work`, commit changes), with monkeypatch.chdir(tmp_path):
       G1 a 250-line .c change -> main(["--base","main"]) returns 1 and the output contains "FAIL size: 250/200".
       G2 a 5-line .py change with gate.run stubbed to return (0, "[]") for ruff and (0, "1 passed") for pytest,
          and shutil.which stubbed to find "ruff" -> returns 0 and prints "GATE PASS".
       G3 a commit touching OBJ/x.o -> returns 1, output contains "forbidden: OBJ/x.o".
       G4 --strict with no tests mapped -> returns 1.
     Stub clang-tidy lookup in G1 so it is SKIP (which -> None and the LLVM path absent).
Run: `python -m pytest -q tests/agent_ops` and paste the output.
</tests>

<digest>
`.agent-ops/out/gate-1.md`, at most 30 lines: files changed, the exact pytest command + its last 3 output
lines, any deviation from this spec (should be none), open risks.
</digest>
