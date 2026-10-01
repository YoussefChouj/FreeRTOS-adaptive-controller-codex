STATUS: done

Files changed:
- .agent-ops/gate.py: Used latin-1 decode/encode in make_shims to preserve raw bytes.
- tests/agent_ops/test_gate.py: Added test_make_shims_non_utf8 to verify GBK bytes.

Verification:
$ python -m py_compile .agent-ops/gate.py
(no output)

$ python -m pytest -q -p no:cacheprovider tests/agent_ops
................                                                         [100%]
16 passed in 0.39s

$ ruff check tests/agent_ops .agent-ops/gate.py
ruff: not installed

Open questions / risks: none.

SUBSTITUTIONS: none
