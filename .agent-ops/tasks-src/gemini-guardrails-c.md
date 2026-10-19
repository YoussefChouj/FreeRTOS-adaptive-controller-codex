<guardrails>
1. Edit only the files in the task's allow-list. Every other path is read-only, including API/wfb_types.h,
   docs/ and every existing source and test file.
2. Make every check pass by fixing implementation code. A test that was written to the brief stays as strict
   as the brief states it.
3. Write complete implementations. Every function body does real work; no TODO, no stub, no "rest of code"
   comment.
4. After each file edit run the acceptance command; fix compiler errors and warnings before anything else.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "PASS" in the output.
6. Every function that can fail returns its error code; callers in the tests check it.
7. Compare floats in tests with an explicit tolerance (fabsf(a - b) <= tol); test NaN with isnan().
8. Check an index against the array length before every array write.
9. Keep every function signature, type name, enum value and struct field exactly as interfaces.md specifies.
10. Include only <stdint.h>, <stddef.h>, <math.h>, <string.h> in the module; tests may add <stdio.h>.
11. If a command or edit fails twice the same way, change approach; never repeat an identical call.
12. Write the digest file BEFORE printing DONE; the supervisor rejects a run whose digest file is missing,
    regardless of exit code.
13. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>
