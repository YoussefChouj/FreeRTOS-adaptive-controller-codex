# S14 — API Completion and Shell State Integration

**Date:** September 17, 2026
**Status:** ✅ Complete
**Gate:** All 63 service + analysis tests pass

---

## Changes

### 1. `ServiceState` extended with transaction result fields

**File:** `ground_station/service/core.py`

The `ServiceState` dataclass (served as JSON by `GET /state`) now exposes two fields the browser shell polls for command feedback:

```python
@dataclass(frozen=True)
class ServiceState:
    schema_id: str
    session_id: str | None
    connected: bool
    samples: int
    last_update_ns: int | None
    streams: dict[int, dict[str, Any]]
    last_transaction_result: dict[str, Any] | None = None   # NEW
    command_results: list[dict[str, Any]] = ()               # NEW
```

**`last_transaction_result`** — the most recent command result, shaped as the shell expects:

| Key | Source | Example |
|-----|--------|---------|
| `transaction_id` | `result.transaction_id` | `42` |
| `command_id` | `result.command_id` | `14` |
| `index` | `result.index` | `0` |
| `status` | `result.outcome.name.lower()` | `"applied"` |
| `reason` | `result.reason.name` | `"SAFETY_INTERLOCK"` |
| `detail` | `result.detail` | `"authority granted"` |
| `time_ns` | `time.time_ns()` | `1726550400000000000` |

**`command_results`** — bounded list of the last N results (default N=10), ordered oldest→newest. Dropping the oldest entry when the deque fills is handled automatically.

**`record_command_result(result)`** — new public method for unit tests and synthetic bridges that bypass the gateway. Production code uses `poll_command()` which calls this automatically.

```python
# Unit-test example
from ground_station.platform.transactions import Outcome, Result
r = Result(1, Outcome.APPLIED, 0x0E, 0, detail="applied")
service.record_command_result(r)
assert service.snapshot().last_transaction_result["status"] == "applied"
```

### 2. `poll_command()` updated to auto-record results

`GroundStationService.poll_command()` now records each observed result into `_command_results` before returning, so any caller that polls the gateway automatically feeds the shell state.

### 3. `Snapshot()` includes the new fields

`snapshot()` now passes `self._last_transaction_result` and `list(self._command_results)` to the `ServiceState` constructor. The `__dict__` export (used by `GET /state`) therefore includes both fields.

### 4. `GET /artifacts` — already implemented, verified

**Endpoint:** `GET /artifacts`
**Response:** `200 {"artifacts": [...]}`

The endpoint was already complete. It calls `index_artifacts()` from `ground_station/analysis/artifacts.py`. Tests verify it returns a list under the `"artifacts"` key.

### 5. `GET /analysis/compare` — already implemented, verified

**Endpoint:** `GET /analysis/compare?a=<session_a>&b=<session_b>&stream=<int>&key=<name>`
**Response:** `200 {<sid_a>: {min, max, mean, std, n}, <sid_b>: {...}, diff_mean, diff_max, better_session}`

Requires all four query parameters. Returns 400 if any are missing. The underlying `compare_sessions()` in `ground_station/analysis/session.py` computes per-session statistics for the requested telemetry key and returns a diff structure.

### 6. Tests added

**File:** `ground_station/service/tests/test_service.py` (+7 new tests)

| Test | What it checks |
|------|---------------|
| `test_service_state_includes_command_result_fields` | Fields present, default to `None`/`[]` |
| `test_record_command_result_populates_service_state` | Synthetic result maps to correct shell fields |
| `test_command_results_respects_maxlen` | Oldest entries dropped at N=10 |
| `test_http_state_includes_command_result_fields` | JSON response has both new fields |
| `test_http_artifacts_endpoint_returns_list` | `/artifacts` → 200 with `"artifacts"` list |
| `test_http_analysis_compare_returns_400_on_missing_params` | Missing params → 400 |
| `test_http_analysis_compare_returns_valid_diff_structure` | Valid params → 200 with diff keys |

**File:** `ground_station/analysis/tests/test_artifacts.py` (+12 new tests)

Tests `index_artifacts()`, `compute_fingerprint()`, and `find_similar()` with temporary directories, covering empty roots, nested files, malformed JSON, deduplication, and the similarity threshold.

### 7. `COMMAND_RESULTS_HISTORY` constant

`ground_station/service/core.py` exports `COMMAND_RESULTS_HISTORY = 10`. This can be overridden at construction time via the `command_history` keyword argument to `GroundStationService`.

---

## No breaking changes

- `ServiceState` fields are added with defaults; all existing callers (API server, listener callbacks) continue to work unchanged.
- The shell receives additional fields it previously polled for (`last_transaction_result`, `command_results`). Since these were missing before, shell panels that accessed them would have received `undefined` — they now receive populated data.
- `GET /artifacts` and `GET /analysis/compare` behave identically to before.
- No changes to the `CommandGateway` protocol layer.

---

## Curl examples

```bash
# Poll state (now includes command results)
curl http://localhost:8081/state | python -m json.tool

# Submit a command
curl -X POST http://localhost:8081/commands \
  -H "Content-Type: application/json" \
  -d '{"command_id": 14, "index": 0, "value": 0}'

# List indexed artifacts
curl http://localhost:8081/artifacts | python -m json.tool

# Compare two sessions (requires real session IDs)
curl "http://localhost:8081/analysis/compare?a=<session_a>&b=<session_b>&stream=0&key=altitude" \
  | python -m json.tool
```

---

## Test results

```
ground_station/service/tests/test_service.py       10 passed  ✅
ground_station/analysis/tests/test_api.py          13 passed  ✅
ground_station/analysis/tests/test_session.py      17 passed  ✅
ground_station/analysis/tests/test_runs.py         14 passed  ✅
ground_station/analysis/tests/test_artifacts.py    12 passed  ✅
                                                    ─────────
                                                    63 passed
```

---

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — System overview, data flow, REST API reference
- [STATE.md](STATE.md) — Current system state, schema ID, session summary
- [COMMAND_SPEC.md](COMMAND_SPEC.md) — Command IDs, result codes, safety restrictions
- [TELEMETRY_SPEC.md](TELEMETRY_SPEC.md) — Telemetry schema, stream/slot mapping
- `ground_station/service/core.py` — `ServiceState`, `GroundStationService`
- `ground_station/service/api.py` — All HTTP endpoint handlers
- `ground_station/analysis/session.py` — `compare_sessions`, `query_telemetry`
- `ground_station/analysis/artifacts.py` — `index_artifacts`, `find_similar`
