# wp4b-ledger result (slice B: ledger.py + tests)

## 1) STATUS: done

## 2) Files changed
- `ground_station/analysis/flightlab/ledger.py` (new): `read_rows`/`upsert`/`rebuild` per spec section 8 + Contracts. Loop columns follow `load_config()["loops"]` yaml order at call time; reads `pipeline.LEDGER_PATH` / `REPO_ROOT` at call time. Backup restore via `os.replace`; `rebuild` catches only `LoadError` per log, any other exception restores backup and re-raises (except BaseException), backup deleted on success. `upsert` writes tmp + `os.replace`, `os.replace` also used for ledger->backup. No broad except. Recs are dataclasses, read via `.severity` attributes.
- `ground_station/analysis/flightlab/tests/test_ledger.py` (new): 15 tests covering the required cases (missing file -> [], insert, same-key replace, sort empty-started-at-last then flight, extra column kept in place, None -> "", floats "%.6g", airborne_s None/0, n_warn/n_critical, loop-column order, rebuild order via monkeypatched `pipeline.analyze`, LoadError skip + name, backup restore + re-raise on RuntimeError, backup deleted on success).
- `.agent-ops/out/wp4b-ledger.md`: this report.

## 3) Verification (foreground, pass counts)
- `python -m pytest ground_station/analysis/flightlab/tests/test_ledger.py -q` -> `15 passed`
- `python -m pytest ground_station/analysis/flightlab/tests -q` -> `128 passed` (baseline 113 + 15 new; nothing else broke)
- End-to-end: NOT RUN — logs not on this host (no `logs/vofa/`); supervisor runs it.

## 4) Open questions / risks
- `started_at` empty-string sort: empty last via `\uffff` sentinel, then flight name; ties (both empty) sort by name. Spec silent on empty-started_at intra-group order.
- Backup filename is `ledger.csv.bak` (next to the ledger); spec does not name it.
- `_meta_started_at` reads the same `started_at` key the vofa loader reads (`log.meta`); a meta without `started_at` (or unparseable JSON) falls back to filename order for that file.
- Not verified on the remote host (no hardware/logs): rebuild over real logs, upsert through a full `analyze()`.
- `test_loop_columns_follow_config_loop_order` compares the full header incl. `analyzed_at` position; if `loops.yaml` order changes the test follows config, so it stays aligned.

SUBSTITUTIONS: none
