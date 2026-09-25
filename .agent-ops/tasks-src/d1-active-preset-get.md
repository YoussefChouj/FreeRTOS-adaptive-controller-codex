# D1 — Expose the active subscribe preset on GET (dashboard service, small)

The service (`ground_station/service/`, entry `__main__.py`) is started with `--preset <name>` and can switch
preset at runtime (look for the preset load / `_resubscribe_fn` / watchdog replay code). Today no GET route reports
which preset is active, so validators cannot check it.

## Do
1. Find the single place the active preset name is known (startup arg + runtime switches). Keep one source of truth.
2. Report it in `GET /health` as `"active_preset": "<name>"` (null if none) and in `GET /state` top level under the
   same key, plus `"preset_loaded_at"` (unix seconds, float) when it was last (re)applied.
3. Make `GET /api/routes` / any route manifest or agent map regeneration step still pass (look for a manifest test).
4. Tests: unit tests for both routes covering: startup preset, runtime switch updates it, no preset -> null.

## Constraints
- Do not POST to any live service. No firmware edits. Minimum diff, match style.
- Run `PYTHONUTF8=1 python -m pytest -q -p no:cacheprovider tests/` for the service tests you touched plus the
  manifest/route tests.

## Deliverable
Commit on your branch; digest `.agent-ops/out/<id>.md` (under 30 lines): files changed, test summary line.
