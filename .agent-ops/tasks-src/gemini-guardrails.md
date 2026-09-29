# Gemini worker guardrails (paste the block below into every Gemini brief)

Sources: `.agent-ops/research/gemini_codegen_quirks.md` (gq1, 2026-09-29; 21 URLs, HTTP-checked by the
worker; arXiv 2603.28592, 2608.20167, 2511.18782 re-checked by the supervisor via arXiv API). Items marked
[general] come from studies of LLM coding agents in general, not Gemini specifically.

Brief layout (Google prompting-strategies doc, Gemini 3):
- Put the GUARDRAILS block first (behavioural constraints belong at the start or in the system instruction).
- Then all context: contracts, file excerpts, fixture ground truth.
- Then the task instructions LAST, opened with "Based on the contracts above, ...".
- Use XML-style tags (`<guardrails>`, `<context>`, `<task>`) as delimiters. Phrase rules positively.
- Leave temperature at the default 1.0; lower values can cause looping.

```text
<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including all existing tests,
   conftest.py, model.py, registry.py, pipeline.py, config/*.yaml and schema/*.json.
2. Make every assertion pass by fixing implementation code. Keep existing tests and assertions exactly
   as they are. [ImpossibleBench, arXiv 2510.20270]
3. Write complete implementations. Every function body does real work; no "...", TODO, `pass` stubs or
   "rest of code" comments.
4. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
6. Catch only specific exceptions (ValueError, KeyError, ...) and re-raise or record them in "warnings".
   Bare `except:` and `except Exception: pass` are forbidden. [general, arXiv 2603.28592]
7. Compare floats with `np.isclose`/`pytest.approx`; test NaN with `np.isnan`/`math.isnan`.
8. State array axes explicitly (`axis=0`); write interval ends as half-open [t0, t1) unless told otherwise;
   check lengths before slicing. [general, arXiv 2511.18782]
9. Keep every function signature, name and return shape exactly as the brief specifies.
10. Import only: python stdlib, numpy, scipy, pandas (loaders only), matplotlib, jsonschema, yaml.
    Use only functions you have seen documented; when unsure, check with `python -c "help(...)"`.
11. If a command or edit fails twice the same way, change approach; never repeat an identical call.
12. Write the digest `.agent-ops/out/<id>.md` BEFORE printing DONE; the supervisor rejects a run whose
    digest file is missing, regardless of exit code.
13. Research tasks: cite canonical URLs only (no vertexaisearch / google.com/url redirects); quote <= 25 words.
14. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>
```

Supervisor-side checks (observed in this project; do not rely on the worker's word):
- rc=0 / DONE / "tests pass" are claims. Re-run the acceptance command yourself.
- Diff the protected files: `git diff --stat base/<id>...vps/<id>` must list only allow-listed paths.
- `git diff base/<id>...vps/<id> -- '*test*'` must be empty except for the worker's own new test file.
- Grep the diff for `except Exception:\s*$`, `pass$`, `TODO`, `== np.nan`, `...` bodies.
- Quality gate: if the acceptance fails after one respawn with the failure pasted in, the supervisor
  takes the package over inline.
