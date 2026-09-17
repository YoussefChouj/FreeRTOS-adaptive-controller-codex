# S11 — Analysis, automation, and agent API

## Outcome

S11 is complete. The agent API surface is implemented as REST endpoints on the
`ApiServer`. Python analysis modules provide session query, experiment run analysis,
and artifact indexing. An external analysis script can inspect state, history,
schemas, and artifacts without screen scraping.

## Implementation

### REST endpoints (`ground_station/service/api.py`)

The handler gained 10 new endpoints (7 GET, 3 POST). Circular imports were
avoided by moving `analysis` module imports inside handler methods.

**Session endpoints:**
- `GET /sessions` — list all sessions (id, started_ns, ended_ns, schema_id, source)
- `GET /sessions/<id>` — session detail (calls `store.session(id)`)
- `GET /sessions/<id>/records` — all records as a list (calls `store.iter_records(id)`)
- `POST /sessions/<id>/export` — export to CSV; body `{output_path, stream_id?}`
  → validates session existence via `store.session()` (raises `KeyError` → 404)

**Experiment endpoints:**
- `GET /experiments` — list active experiment runs (filters out `complete`/`aborted`)
- `GET /experiments/<name>` — experiment detail (state, tick, samples, events, params)
- `POST /experiments` — start experiment; body `{name, settle_ticks, measure_ticks,
  parameters}` → `201 {run_id}`
- `POST /experiments/<name>/abort` — abort by name; clears `experiment_runtime.active`
  so subsequent `GET /experiments` returns `[]`

**Analysis endpoints:**
- `GET /artifacts` — indexed artifacts from `ground_station/results/`
- `GET /analysis/compare?a=<id>&b=<id>&stream=<id>&key=<name>` — compare one
  telemetry key across two sessions

### `ground_station/analysis/session.py`

```python
query_telemetry(store, session_id, stream_id?, key?, since_ns?, until_ns?)
  → Iterator[dict]  # {time_ns, stream_id, sequence, source_time_ms, values}

telemetry_stats(store, session_id, stream_id?) → dict[int, StreamStats]
  # StreamStats(stream_id, count, rate_hz, loss_events)
  # Uses per-stream prev_row tracking to avoid cross-stream dt contamination
  # Loss: gaps > 5× median dt (requires 4+ records per stream for reliable median)

compare_sessions(store, sid_a, sid_b, stream_id, key) → dict
  # Returns {sid_a: {min,max,mean,std,n}, sid_b: {...}, diff_mean, diff_max, better_session}

export_session_csv(store, session_id, output_path, stream_id?) → None
  # Writes CSV with time_ns, stream_id, sequence, source_time_ms, <value keys>
  # Raises KeyError if session not found
```

### `ground_station/analysis/runs.py`

```python
summarize_run(run: ExperimentRun) → dict
  # {name, state, duration_ms, settle_ms, measure_ms, parameter_changes,
  #  event_markers, safety_aborted}

compare_runs(runs: list[ExperimentRun]) → list[dict]
  # [{name, state, duration_ms, parameter_deltas, events}, ...]

detect_settling(samples, key, window=10, threshold=0.01) → int
  # First index where rolling std of `key` drops below threshold; -1 if never
```

### `ground_station/analysis/artifacts.py`

```python
index_artifacts(root="ground_station/results") → list[dict]
  # [{path, session_id, schema_id, created, metrics: {...}}, ...]

compute_fingerprint(data: dict) → str  # SHA-256 of canonical JSON

find_similar(root, threshold=0.95) → list[tuple[path_a, path_b, score]]
  # Pairs of result files with similar fingerprints
```

## Agent verification

A script can verify the agent API without screen scraping:

```python
import urllib.request, json
base = "http://127.0.0.1:8080"

# List sessions
_, sessions = _get(f"{base}/sessions")

# Get session detail
_, detail = _get(f"{base}/sessions/{sessions[0]['id']}")

# Query telemetry
_, records = _get(f"{base}/sessions/{sid}/records")

# List artifacts
_, result = _get(f"{base}/artifacts")

# Start an experiment
status, body = _post(f"{base}/experiments", {
    "name": "sweep_kp", "settle_ticks": 50, "measure_ticks": 100,
    "parameters": {"kp": 2.0}
})
```

## Test evidence

```text
python -m pytest ground_station/analysis/tests/ ground_station/service/tests \
  ground_station/platform/tests ground_station/comm/tests/test_protocol_schema.py -q
67 passed
```

Coverage: 15 session tests (query, stats, compare, export CSV), 10 runs tests
(summarize, compare, detect_settling), artifact tests, 11 API endpoint tests.

## S12 entry

S12 can begin with the integrated hardware validation on the powered drone:
telemetry capture, command transactions, authority plugins, shadow-mode
experiments, resource metrics readback, build/flash cycle, and recovery
procedure. All sessions S1–S11 are complete and gate-passed.
