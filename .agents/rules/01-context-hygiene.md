---
trigger: always_on
description: Enforce Claude-Code-style ripgrep pattern search, bounded line reads, and Flash subagent context isolation
---
# Mandatory Context & Token Optimization Protocol

1. **NO BLIND FULL-FILE READS:** Never call `view_file` (or `cat`) on an entire file over 120 lines to check if it is relevant.
2. **RIPGREP / GREP FIRST:** Always locate symbols, classes, functions, or error strings first using `grep_search` or `run_command` with:
   `rg -n --max-count=15 --no-heading "<pattern>"`
3. **SURGICAL LINE SLICES ONLY:** Once `rg` returns exact file paths and line numbers, inspect ONLY a +/- 40 line window around the match (using bounded `view_file` start/end line parameters or `sed -n 'X,Yp' path/to/file`).
4. **3-FILE SUBAGENT ISOLATION RULE:** If a task requires searching or tracing logic across MORE THAN 3 FILES, STOP reading files in the main Gemini 4 Argon thread. Immediately call `invoke_subagent` with `codebase-scout` (or the built-in `research` subagent) so all multi-file scanning happens in an isolated context window.
5. **ISOLATE VERBOSE TERMINAL LOGS:** Never run full test suites, compilers, or verbose linters directly in the main thread without filtering. Delegate test/build execution to the `test-runner` subagent via `invoke_subagent`, OR pipe output through `| tail -n 45` or `| rg -C 5 "FAIL|ERROR|Exception|error:"`.
6. **PRE-EDIT GATE:** Before modifying more than 2 files, output a 4-bullet plan listing: (a) exact target files & line ranges, (b) proposed change, and (c) the single verification command.