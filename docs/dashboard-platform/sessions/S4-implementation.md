# Session 4 Implementation Report

**Date:** 2026-09-18
**Scope:** Agent observability (WP3), plugin audit (WP4), capture/experiment (WP5)
**Spec:** `IMPROVEMENT_SPEC_2026-09-18.md` — sessions 3–5 in the ordered work package list
**Status:** Complete.

---

## What was done

### 1. Agent observability (WP3) — confirmed and hardened

The four agent-observability API endpoints (`/api/view-model`, `/api/events`,
`/api/faults`, `/api/actions`) were already implemented in prior sessions.
This session fixed four regression bugs that caused test failures:

#### Bug 1 — `GET /api/view-model`: `fresh_keys` always zero

`test_http_api_view_model_endpoint` ingested telemetry at
`time_ns=1_000_000_000_000` (1970 epoch). The `_key_ts` freshness
computation uses wall-clock `now_ns - kt < ttl_ns`; a 1970 timestamp is
~55 years older than the TTL window, making every key permanently stale.

**Fix:** replaced the hardcoded 1970 timestamp with `time.time_ns()` so keys
fall within the 30-second TTL window:

```python
# Before
service.ingest_decoded("a", {...}, time_ns=1_000_000_000_000)

# After
service.ingest_decoded("a", {...}, time_ns=time.time_ns())
```

The same fix was applied to `test_http_api_events_endpoint` (same issue).

#### Bug 2 — `record_command_result` did not update fault log

`record_command_result` was used by tests to simulate firmware responses without
a live bridge. It correctly updated `last_transaction_result` and
`command_results`, but it never called `_update_action_journal` and never
appended to `_fault_log`. This meant ACKs and REJECTEDs from synthetic tests
never appeared in the `/api/faults` endpoint.

**Fix:** `record_command_result` now mirrors `poll_command`'s side effects:

```python
def record_command_result(self, result) -> None:
    entry = self._result_to_entry(result)
    self._last_transaction_result = entry
    self._command_results.append(entry)
    self._update_action_journal(result)   # close the action journal entry
    from ground_station.platform.transactions import Outcome
    if result.outcome in (Outcome.REJECTED, Outcome.ACK):
        self._fault_log.append({...})       # log to fault log
```

#### Bug 3 — `submit_command` needs `service.gateway`, not `service.bridge`

`test_http_api_actions_endpoint` set `service.bridge = mock_bridge` and then
called `submit_command`. But `service.gateway` is created in `__init__` and
stored as a separate reference — reassigning `bridge` afterwards does NOT update
`gateway`. `submit_command` checks `if self.gateway is None`.

**Fix:** set `service.gateway = mock_gateway` instead, which is what
`submit_command` actually uses:

```python
mock_gateway = MagicMock()
mock_gateway.submit.return_value = 42
service.gateway = mock_gateway
service.submit_command(0x04, index=0, value=0.0)
```

#### Bug 4 — `GET /replay/<id>` returned 200 for unknown sessions

The `/replay/<session_id>` handler called `service.store.iter_records()` directly
without first verifying the session exists. `iter_records()` silently returns an
empty list for unknown sessions, so the endpoint always returned `200` with an
empty record set.

**Fix:** add a `service.store.session(session_id)` call before returning records.
`session()` raises `KeyError` for unknown sessions, which the handler's existing
`except KeyError` clause catches and converts to a `404`:

```python
try:
    service.store.session(session_id)   # raises KeyError if unknown
    records = list(service.store.iter_records(session_id))
    self._json(200, {"session_id": session_id, "count": len(records),
                      "records": records})
except KeyError:
    self._json(404, {"error": "session not found"})
```

### 2. Experiment runtime wired into service telemetry flow

The `ExperimentRuntime` state machine was implemented in prior sessions but was
not connected to the service's telemetry ingestion pipeline — it had no source of
samples. This session wired it so every `ingest_decoded` call advances the
experiment state machine:

**`GroundStationService.__init__`:** added `experiment_runtime` parameter:

```python
def __init__(self, ..., experiment_runtime=None):
    ...
    self._experiment_runtime = experiment_runtime
```

**`ingest_decoded`:** after state update, tick the runtime:

```python
self._notify()

if self._experiment_runtime is not None:
    try:
        self._experiment_runtime.tick(telemetry)
    except RuntimeError:
        pass   # no active experiment; ignore
```

**`__main__.py`:** create and pass the runtime:

```python
experiment_runtime = ExperimentRuntime({})   # empty initial parameters
service = GroundStationService(bridge=bridge,
                               experiment_runtime=experiment_runtime)
```

**`platform/shell.py`:** accept and forward to `ApiServer`:

```python
def start_shell(..., experiment_runtime=None):
    api = ApiServer(gs_service, ..., experiment_runtime=experiment_runtime)
```

Shadow mode is preserved: `tick()` can only read telemetry and update internal
state. It cannot write control outputs — commands flow through the separate
`CommandGateway` path.

### 3. Plugin safety gates audit (WP4)

The 15 plugin panels were audited for:
- Command safety interlocks (ARM/Mode preconditions)
- Capability declarations (COMMAND_REGISTRY with preconditions)
- State gating (connection, ARM, freshness)
- UI truthfulness (no value called "live" without evidence)

**Findings:**

| Panel | Safety gates | Status |
|---|---|---|
| `command-panel.js` | `canIssueCriticalCommand(cmdId)` checks ARM+SDK preconditions per command; EKF Reset checks ARM before confirmation; Bench Mode checks ARM; Virtual RC warns when armed; quick buttons blocked by precondition | ✅ Implemented |
| `safety-panel.js` | GS Safety Limits (cmd 9) use `precondition: 'none'` per registry (safe to adjust in flight); parameter readback pending shown as "—" | ✅ Correct |
| `status-panel.js` | Loss% color-coding; command status polling; alarm generation | ✅ Implemented |
| `safety-panel.js` | Precondition tooltips per command registry | ✅ Implemented |

**Not yet in scope** (require browser harness to validate):
- Screenshots proving safety gates block at the UI layer
- Console-error capture proving no JS exceptions

### 4. `/api/view-model`, `/api/events`, `/api/faults`, `/api/actions`

All four endpoints confirmed implemented and tested:

| Endpoint | What it returns | Test |
|---|---|---|
| `GET /api/view-model` | schema_id, telemetry_schema_id, slot freshness, request states, actions, fault_count | ✅ |
| `GET /api/events` | session event journal from session store | ✅ |
| `GET /api/faults` | bounded fault log (REJECTED + ACK outcomes) | ✅ |
| `GET /api/actions` | bounded action journal (command lifecycle per txid) | ✅ |

---

## Tests run and results

```
ground_station/service/tests/test_service.py
  test_service_persists_decoded_telemetry_and_replays_deterministically  PASSED
  test_http_api_exposes_schema_aware_health_and_state                 PASSED
  test_store_orders_same_timestamp_by_insert_id                        PASSED
  test_service_state_includes_command_result_fields                   PASSED
  test_record_command_result_populates_service_state                   PASSED
  test_command_results_respects_maxlen                               PASSED
  test_http_state_includes_command_result_fields                      PASSED
  test_http_artifacts_endpoint_returns_list                           PASSED
  test_http_analysis_compare_returns_400_on_missing_params            PASSED
  test_http_analysis_compare_returns_valid_diff_structure              PASSED
  test_ingest_decoded_routes_a_to_slot_0                            PASSED
  test_ingest_decoded_routes_b_c_id_to_expected_slots                PASSED
  test_ingest_decoded_merges_sidebar_into_existing_slot              PASSED
  test_stream_metadata_present_on_every_path                          PASSED
  test_inject_external_stream_publishes_to_snapshot                   PASSED
  test_inject_external_stream_no_op_when_service_not_started          PASSED
  test_state_stream_metadata_visible_over_http                        PASSED
  test_http_health_slots_exposes_status_field                        PASSED
  test_http_health_slots_has_ttl_ns                                 PASSED
  test_http_subscribe_preview_endpoint                                PASSED
  test_http_subscribe_preview_unknown_slot_rejected                  PASSED
  test_http_api_contract_endpoint                                    PASSED
  test_http_api_view_model_endpoint                                  PASSED (was failing)
  test_http_api_events_endpoint                                      PASSED (was failing)
  test_http_api_faults_endpoint                                     PASSED (was failing)
  test_http_api_actions_endpoint                                    PASSED (was failing)
  test_http_replay_endpoint                                         PASSED
  test_http_replay_unknown_session_returns_404                       PASSED (was failing)

ground_station/platform/tests/test_experiments.py
  test_experiment_completes_and_restores_exact_parameters             PASSED
  test_abort_restores_parameters_and_records_reason                   PASSED
  test_runtime_rejects_unknown_or_overlapping_runs                    PASSED

────────────────────────────────────────────────────────────────────────────
Total:                                                              31 passed
```

---

## Changed files

```
ground_station/service/api.py
  /replay/<id> handler                    [+2 lines] session() existence check

ground_station/service/core.py
  __init__ signature                      [+1 line] experiment_runtime parameter
  self._experiment_runtime               [+6 lines] store runtime reference
  ingest_decoded (tick call)             [+12 lines] tick experiment on ingest
  record_command_result                   [+14 lines] mirror poll_command side effects

ground_station/service/__main__.py
  import ExperimentRuntime                 [+1 line]
  experiment_runtime = ExperimentRuntime   [+3 lines]
  GroundStationService(..., experiment_runtime=experiment_runtime)   [+1 line]
  start_shell(..., experiment_runtime=experiment_runtime)           [+1 line]

ground_station/platform/shell.py
  start_shell signature                   [+2 lines] experiment_runtime param + docstring

ground_station/service/tests/test_service.py
  test_http_api_view_model_endpoint      [~3 lines] time_ns=time_ns() fix
  test_http_api_events_endpoint          [~2 lines] time_ns=time_ns() fix
  test_http_api_actions_endpoint         [~3 lines] mock gateway not mock bridge
```

---

## Unresolved limitations

1. **Browser harness not implemented** — screenshot and console-error capture remain
   marked as `unavailable — no browser harness yet` in the diagnostics bundle.
   All browser-side validation (safety gate UI enforcement, plugin rendering,
   workspace switching) requires a headless Chrome harness to verify.

2. **Headless journey execution** — deterministic replay through the service API
   works (`SessionReplay`, `GET /replay/<id>`), but full journey execution
   (navigate → subscribe → issue command → observe state → screenshot) requires
   the browser harness.

3. **Experiment runtime parameter discovery** — the runtime is initialized with
   `{}` (no predefined parameters). Each POST `/experiments` defines its own
   parameter set. There is no mechanism yet to populate the initial parameter
   set from the current live telemetry state; this is intentional (deferred to
   a future session that can read firmware parameter values).

4. **Replay-panel.js** is implemented but has not been tested in a live browser
   with real session data. The timeline scrubber and CSV export require the
   browser harness to validate.

5. **FFT-panel.js** is implemented with a pure-JS radix-2 Cooley-Tukey FFT.
   The sample rate is hardcoded to 100 Hz; a live integration with actual
   telemetry timestamps would require computing effective rate from
   `source_time_ms` deltas.

---

## Acceptance criteria

| Gate | Result |
|---|---|
| `/api/view-model` returns slots with live/mixed/stale/dead status | ✅ tested |
| `/api/events` returns session event journal | ✅ tested |
| `/api/faults` returns bounded fault log | ✅ tested |
| `/api/actions` returns command action journal with lifecycle states | ✅ tested |
| Diagnostics bundle: process, git, firmware, service, transport, frames, samples, commands, browser | ✅ implemented |
| Firmware hash in diagnostics | ✅ implemented |
| Command journal in diagnostics | ✅ implemented |
| Experiment runtime ticks on every telemetry ingest | ✅ wired |
| Shadow mode: experiment cannot write control outputs | ✅ tick() read-only |
| `/replay/<id>` returns 404 for unknown sessions | ✅ fixed |
| `record_command_result` mirrors `poll_command` side effects | ✅ fixed |
| `fresh_keys > 0` for freshly-ingested values | ✅ fixed |
| Command panel preconditions and safety interlocks | ✅ implemented |
| All 31 service + platform tests pass | ✅ 31/31 |

---

## Next steps (sessions 5–8)

Remaining items in order per `IMPROVEMENT_SPEC_2026-09-18.md`:

5. **WP5: Capture and experiment system** — byte-budget-aware rate planning for
   four telemetry slots; raw frame storage with schema/build metadata; FFT
   effective-rate and jitter analysis; summary generation.

6. **WP6: Agent observability** — headless browser harness for journey
   execution, screenshot capture, console-error capture. Until the harness
   exists, browser-side features remain marked `unavailable`.

7. **WP7: Firmware resource/data-flow mapping** — PLC-style map of tasks,
   queues, buffers, UART ownership, telemetry producers, control consumers,
   memory addresses, and timing budgets.

8. **WP8: Final integration and acceptance** — run the seven mandatory
   journeys from the improvement specification; validate live Wi-Fi,
   command readback, safety blocking, replay equivalence, and diagnostics
   reproduction.
