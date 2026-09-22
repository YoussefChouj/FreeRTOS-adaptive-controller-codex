# Task: fix the key-leak bug in copilot's `_short_error`

Tier 2. Python only. Do NOT touch firmware, do NOT POST to the live 8081 service.

## The bug

`_short_error` in `ground_station/service/copilot.py` builds the operator-facing
error string for a failed LLM call. That string can contain the request URL or an
upstream error body, which is where an API key can appear.

It does two things in the wrong order:

1. It truncates to 117 chars **first**, then redacts. A key that straddles the cut
   is chopped down to a fragment shorter than the redaction threshold, so the
   fragment survives into the operator's chat.
2. The redaction regex is `[A-Za-z0-9_\-]{40,}`. Keys shorter than 40 chars are
   never redacted at all.

## What to do

Rewrite the body of `_short_error` so that:

- Redaction happens **before** truncation, never after.
- The threshold drops to 20 consecutive `[A-Za-z0-9_\-]` characters, and known key
  shapes are redacted regardless of length: a token starting `sk-` and anything
  after `Bearer `, `api_key=`, `api-key:`, `token=` or `key=` (case-insensitive).
- The replacement is the literal `REDACTED`.
- The result is still at most 120 chars and still only the first line.
- `import re` moves to the module's import block at the top of the file, where the
  other stdlib imports are. It is currently inside the function.

Keep the docstring accurate. Do not change the signature or the call site at
line ~232. Do not widen the scope: this is one function plus one import move.

## Tests

Add `TestShortError` to `ground_station/service/tests/test_copilot.py`, matching the
style already in that file. Cover at least:

- A long message with a 45-char key at the very end (past the 117-char cut): the
  key must not appear in the output, in whole or in part. Assert on a distinctive
  20+ char slice of the key, not just the whole string.
- A 24-char key: redacted.
- `Authorization: Bearer <short token>`: the token is redacted.
- A normal short error with no key: passes through unchanged.
- Multi-line input: only the first line is returned.
- Output is always <= 120 chars.

Each test must fail against the current implementation for the right reason before
your fix. Say in your report which ones you confirmed failing first, and paste the
failure output for the two leak tests.

## Verify

```bash
python -m pytest ground_station/service/tests/test_copilot.py -q
```

Report the exact pass count. Then run the whole service suite and report it too:

```bash
python -m pytest ground_station/service/tests -q
```

## Constraints

- Do not print, log or echo any real API key. The test keys you write are fake
  literals; make them obviously fake (`sk-fake-...`).
- No new dependencies.
- Do not reformat the rest of the file.
