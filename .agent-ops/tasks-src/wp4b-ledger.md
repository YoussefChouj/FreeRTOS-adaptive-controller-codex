# Task wp4b-ledger: flightlab ledger.py (+ tests), slice B of WP4b

Read `.agent-ops/tasks-src/wp4b-report.md` first: its Guardrails, Contracts, "ledger.py" and "Tests" sections
bind you. This slice implements ONLY the ledger. A parallel worker writes compare.py and report/: do not create them.

EDIT ONLY: `ground_station/analysis/flightlab/ledger.py`, `ground_station/analysis/flightlab/tests/test_ledger.py`,
`.agent-ops/out/wp4b-ledger.md` (your report, same content rules as the WP4b report section).

A previous attempt was rejected. Its defects, do not repeat them:
1. `upsert` called `r.get(...)` on `Recommendation` objects. They are dataclasses (`registry.py`): use attributes
   or `r.to_dict()`.
2. Broad `except Exception` / bare `except`. `rebuild` catches only `LoadError` per log; any other exception
   restores the backup and re-raises (use `try/except BaseException: restore; raise` or `try/finally` with a flag).
3. The backup restore used `shutil.move` onto an existing path, which fails on Windows. Restore with `os.replace`.
4. Four thin tests. Write at least these, each asserting concrete cell values:
   read_rows missing file -> []; upsert insert; upsert same (flight, started_at) replaces (row count unchanged);
   sort by started_at with empty started_at last, then flight; an extra column already in the file is kept in place;
   None -> "" (never "0" or "0.0"); floats "%.6g"; airborne_s: None when segments.airborne is None, 0 for [];
   n_warn / n_critical counted from recs by severity; `e_rms_steady_<loop>` columns follow `load_config()["loops"]`
   order; rebuild processes metas in started_at order (monkeypatch `pipeline.analyze` to record calls);
   rebuild skips a LoadError log and names it in the summary; rebuild restores the previous ledger bytes when
   analyze raises RuntimeError, and the RuntimeError propagates; rebuild deletes the backup on success.
5. Read `pipeline.REPORTS_DIR`, `pipeline.LEDGER_PATH`, `pipeline.REPO_ROOT` at call time (tests monkeypatch them).

Facts checked by the supervisor: the meta start key is `started_at` (`pipeline.flight_info` reads `log.meta`);
`LoadError` is importable from `.loaders`; conftest fixtures: build_log, make_log, hover_log, cfg.

Acceptance (put the verbatim tails in your report):
- `python -m pytest ground_station/analysis/flightlab/tests/test_ledger.py -q`
- `python -m pytest ground_station/analysis/flightlab/tests -q` (whole package; nothing else may break)
- End-to-end: NOT RUN on the remote host (logs are not there); the supervisor runs it.
