
# WP-1 brief (CEO -> Manager), 2026-10-01. Pilot of the full CEO -> manager -> worker chain.

Goal: make `make_shims` in `.agent-ops/gate.py` byte-safe, so a vendor header that is not UTF-8
(for example GBK comments) cannot crash the gate.

Facts the CEO measured on 2026-10-01:
- `make_shims` (gate.py lines 34-44) uses `read_text(encoding="utf-8")` and `write_text(encoding="utf-8")`.
  A header with non-UTF-8 bytes raises UnicodeDecodeError, and the gate dies with a traceback.
- The two headers shimmed today are us-ascii (`file -bi`), so it works by luck.
- `python -m pytest -q -p no:cacheprovider tests/agent_ops` on main: 14 passed, 1 skipped.

Wanted: read bytes, decode latin-1, `re.sub` as now, encode latin-1, write bytes.
Bytes outside the replaced regions must stay identical. No other behaviour change.
New test: a `portmacro.h` holding a GBK comment (`"中文".encode("gbk")`) and one `__asm { ... }`
block -> make_shims returns 1; the written bytes still contain the GBK bytes and no `b"__asm"`.

Acceptance (run by you):
- `python -m pytest -q -p no:cacheprovider tests/agent_ops` -> 15 passed, 1 skipped
- `python .agent-ops/gate.py --base main --allow .agent-ops/gate.py --allow tests/agent_ops/test_gate.py --allow ".agent-ops/out/*"` -> GATE PASS
  (your own task files and report are committed after this, see MANAGER.md step 9)

Scope (worker may edit): `.agent-ops/gate.py`, `tests/agent_ops/test_gate.py`, its digest `.agent-ops/out/wp1-r<n>.md`
Allow globs: `.agent-ops/gate.py` `tests/agent_ops/test_gate.py` `.agent-ops/out/*`
Worker lane: agy-vps, chain `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`   Max worker rounds: 3
Report to: `docs/agent/reports/WP-1.md`. Because this is a pilot, add two lines at the end:
- how many `wait` calls you needed per round;
- anything in MANAGER.md that was unclear or missing, or a command that was denied.
