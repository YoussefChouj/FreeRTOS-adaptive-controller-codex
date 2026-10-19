Yes, push the PR now, against `overnight-2026-10-08`.

Put the output of these in the PR body: `python tools/host_tests.py` (all pass, including fw_att_ctrl),
the scoped pytest for the att_ctrl.py parity tests and campaign_capture, and `ruff check` on changed
.py files. If any of them fails, fix it before pushing.
