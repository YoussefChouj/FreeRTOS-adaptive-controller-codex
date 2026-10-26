# z-vz-cf Progress
| Piece | Status | Commit | Test Result |
|---|---|---|---|
| 4. Bench/flight procedure (A1-A3, F1-F2) | DONE | d71ef9e4 | doc_paths.py OK, test_commands_doc.py 2 passed |
| 1. S1 closed-loop sim (gates gains) | DONE | 6aa93199 | k1=0.21, k2=0.45, k3=0.10, AccMax=20.0; dU p99 19.9x lower, p90 5.31x lower, settle -0.7% |
| 2. Firmware (Alt_UpdateFB CF mode, ALT_CF_ROW, g_alt_vz_mode, alt_cf.*) | DONE | 44873f0d | armcc 0 warnings/errors, row_meta.py 0 errors, test_wfb_var.py passed |
| 3. Host tests U1-U5 + U6 (test_alt_cf.c, row_meta.py, check.sh --changed) | DONE | d7a90df2 (+ Alt_CfReset Mode-0 isolation fix) | U1-U5 15/15 checks PASS, host_tests.py 35/35 PASS, all-loop-equiv PASS, check.sh --changed PASS |
| 5. Final Verification & Hand-back Report (report.md) | DONE | pending orchestrator commit | report.md written to .worktrees/z-vz-cf/.agent-ops/out/z-vz-cf/report.md and root .agent-ops/out/z-vz-cf/report.md |
| U6. Full check.sh --changed (all-loop-equiv + 35 host tests + row-meta) | DONE | 9f8004c3 | 10/10 check.sh steps OK, 35/35 host_tests OK, 38 row-meta tables 0 errors |
