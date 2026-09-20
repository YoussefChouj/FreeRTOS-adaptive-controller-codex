# Agent guide: driving the ground station and dashboard

This guide is for agents (LLM or scripted) that inspect, test or extend the dashboard. Humans should start at [README.md](README.md).

## 1. Ground rules

- The service (`python -m ground_station.service`) listens on **`http://127.0.0.1:8081`** by default.
- **Set `NO_PROXY=127.0.0.1,localhost`.** The workstation runs a Clash proxy, and without this, local requests go to the proxy and fail.
- **A live service is connected to the drone. Never POST to it.** Every POST route can reach the drone or the bus: `/commands`, `/subscribe`, `/experiments`, `/replay/<id>/play` and `/sessions/<id>/export`. Validate POST routes with unit tests that use mocked gateways and bridges (`ground_station/service/tests/`).
- GET routes are read-only and safe to call against the live service.

## 2. Discover before you guess

| Route | What it gives you |
|---|---|
| `GET /api/routes` | Every GET/POST route with a one-line description or its query params, plus the UI selector scheme. **Machine-readable source of truth.** |
| `GET /api/view-model` | The state the shell renders: streams, keys, freshness. Cheap by default. **`?stats=1` adds `session_stats` (per-stream sample counts and source rate) by scanning the whole session — on a long session that call takes tens of seconds.** Only the Firmware Resource Map's Refresh button asks for it. |
| `GET /api/contract` | The firmware contract: schema ID, command IDs and telemetry layout (`ground_station.platform.firmware_contract`). |
| `GET /api/diagnostics/bundle` | Recent frames, commands and faults, for bug reports. There is no bare `/api/diagnostics`; it returns 404 by design. |
| `GET /api/actions`, `/api/events`, `/api/faults` | Action journal, event log and fault log. |

Query strings are parsed with `urlsplit`, so `/sessions?limit=5` routes the same as `/sessions`.

### Paging: always page the record routes

`GET /sessions/<id>/records` and `GET /replay/<id>` take `?limit=N&offset=N`. Both **default to 1000 rows**; `limit=0` means unlimited and an unparseable `limit` falls back to the default. Both responses carry `count`, `offset`, `limit` and `truncated`, so a reader knows whether to fetch the next page:

```bash
curl -s "http://127.0.0.1:8081/sessions/$SID/records?limit=500&offset=0" | python -m json.tool | head
```

The cap is not cosmetic. Before it existed, one live flight session answered `?limit=5` with **181 MB in 6.9 s** — the `limit` was parsed by the shell, not the server — which stalled the replay panel and any agent that read it. Paging is pushed into SQL (`storage.iter_records(session_id, limit=None, offset=0)`), so a capped read never materialises the full session. Regression tests: `test_records_route_pages_and_caps_by_default`, `test_replay_route_pages_like_records_route`, `test_store_iter_records_offset_without_limit`.

## 3. UI selectors (stable for automation)

| Selector | Element |
|---|---|
| `[data-testid="tab-<workspace>"]` | Workspace tab. `<workspace>` is lowercase, e.g. `tab-replay` or `tab-diagnostics`. |
| `[data-testid="panel-<slug>"]` | Plugin card, e.g. `panel-session-replay`. |
| `#plugin-body-<slug>` | The plugin's content root. |
| `[data-testid="session-id"]` | Session id in the replay detail view. |
| `[data-testid="replay-play"]` | "Play to Live View" button. **It POSTs, so do not click it against a live service.** |

The same list is served at `GET /api/routes` → `ui_testids` / `ui_ids`.

## 4. Smoke checks

```bash
# Browser walk: all tabs, screenshots, console errors, HTTP >= 400, NaN/undefined text.
# Read-only: it clicks tabs and one replay session row, never a command/export/play button.
NO_PROXY=127.0.0.1,localhost python -m ground_station.service.browser_smoke \
    --out logs/smoke --no-replay-detail
# Exit code 0 means no console errors and no bad responses.

# Unit tests (mocked; safe)
python -m pytest -q -p no:cacheprovider ground_station/service/tests ground_station/comm/tests
```

Requirements: Playwright (`pip install playwright`) and a local Chrome install (`channel="chrome"`).

Pass `--no-replay-detail` against a live service with a long session: without it the walk opens one session row, and that record fetch is the heaviest request the shell makes. `--settle` (default 4.0 s) is the dwell per tab; raise it if panels are still loading when the screenshot is taken.

Last verified walk (2026-09-20) — 10 tabs, `nan=0 undef=0` everywhere, **ERRORS 0 / BAD RESPONSES 0**:

| Tab | Panels |
|---|---|
| Overview | `panel-flight-status`, `panel-safety-limits`, `panel-time-series` |
| Control | `panel-flight-status`, `panel-safety-limits`, `panel-command-panel` |
| Estimator | `panel-ekf-estimator` |
| MRAC | `panel-mrac-controller` |
| Telemetry | `panel-telemetry-explorer`, `panel-time-series`, `panel-fft-spectrum`, `panel-bandwidth-manager`, `panel-slot-manager` |
| Experiments | `panel-experiment-runtime` |
| Paths | `panel-path-planning` |
| Bench | `panel-motor-bench` |
| Replay | `panel-session-replay` |
| Diagnostics | `panel-rtos-resources`, `panel-fft-spectrum`, `panel-bandwidth-manager`, `panel-firmware-resource-map` |

### Running your own service instance

Do not start a second *real* service: it would bind UDP 14550 and could send a subscribe to the drone. For route-level work, build an in-process one on an ephemeral port instead — and note that **the shell is only served when `static_root` is passed**, otherwise `/` is 404 and the tabs never appear:

```python
from ground_station.service.api import ApiServer
api = ApiServer(service, host="127.0.0.1", port=0,
                static_root="docs/dashboard-platform/shell")
```

`make_handler(service, hub=None, static_root=None, experiment_runtime=None)` is the handler factory behind it.

## 5. Behaviours worth knowing

| Capability | Behaviour |
|---|---|
| **Arm gate (fail closed)** | `GroundStationService.arm_state()` returns `armed`, `disarmed` or `unknown`. If there is no arm sample, or the latest one is older than 2 s (`ARM_STALE_NS`), the result is `unknown`, and `submit_command` rejects motor-bench commands (0x16) unless the state is `disarmed`. A rejection is logged as a `SAFETY_INTERLOCK` fault. |
| **Command timeout** | A command still `SUBMITTED` 1.0 s after sending (`COMMAND_TIMEOUT_NS`) moves to `TIMED_OUT` in the action journal. |
| **Slot loss / freshness** | A slot not updated for 30 s is evicted from the `/state` snapshot. The shell also renders per-key staleness from `last_update_ns` / `_key_ts`. |
| **`WifiBridge.subscribe_slot(slot, divider, ranges)`** | Sends one 0x21 request for a single slot. `divider=0` stops the slot. `ranges` are DWARF names (resolved against `OBJ/JX_FLY.axf`) or `StreamRange` objects. Wire format: [docs/telemetry-protocol.md](../telemetry-protocol.md). |
| **Replay → live view** | `POST /replay/<id>/play` pushes stored telemetry onto the local bus so live panels render it. **Nothing is sent to the drone.** |
| **Analysis** | `GET /analysis/{jitter,gaps,effective-rate}?session_id=&stream=` and `GET /analysis/compare?a=&b=&stream=&key=`. |
| **Action journal ordering** | `/api/view-model` returns the **10 most recent** actions, newest first (`list(reversed(service.action_journal()[-10:]))`). `GET /api/actions` returns the journal in its natural oldest-first order. |
| **Zero is not missing** | View-model stats use `x if x is not None else None`, never a truthiness test — a legitimately zero rate or count must render as `0`, not as `—`. |

## 6. Where the code is

| Path | Role |
|---|---|
| `ground_station/service/api.py` | HTTP routes; `_ROUTE_MAP` feeds `/api/routes`. **Update it when you add a route.** `test_http_api_routes_endpoint` GETs every parameterless route in it. |
| `ground_station/service/core.py` | Service state, arm gate, command lifecycle, slot freshness. |
| `ground_station/service/storage.py` | SQLite session store. `iter_records(session_id, limit=None, offset=0)` pages in SQL. |
| `ground_station/service/browser_smoke.py` | The browser smoke walk. |
| `ground_station/comm/wifi_bridge.py` | UDP bridge and subscribe protocol. |
| `docs/dashboard-platform/shell/` | Shell (`index.html`) and plugins (`plugins/*.js`). See [shell/plugin-api.md](shell/plugin-api.md). |

## 7. The service owns the WiFi port — stream-log cannot run beside it

A running service holds **TCP 8081 and UDP 14550** in one process:

```
TCP    0.0.0.0:8081    LISTENING    <pid>
UDP    0.0.0.0:14550                <pid>
```

So `python -m ground_station.livewatch.stream_log --transport usart3` (whose data path defaults to `udp:14550`) **cannot bind while the dashboard service is up**. Find the owner with `netstat -ano | grep -E "14550|8081"`. Do not kill the service to make room: ask the operator to stop it, run the capture, then restart it.

The UART5 fallback is not an escape hatch in this build. `SUBSCRIBE_UART5_ENABLED` is never `#define`d, so it evaluates to 0: `Uart5_Subscribe_TxSend` is a no-op stub, and since 2026-09-20 `API/subscribe.c:537` explicitly rejects a UART5 subscribe with `E:UART5 disabled` rather than ACKing a slot whose frames would all be dropped. `stream_log --transport uart5` therefore gets no data from this firmware.

Probe reads have no such conflict — they go over SWD and work with the service running:

```powershell
python -m ground_station.livewatch read --transport swd <symbol>
python -m ground_station.livewatch verify   # always first: catches a stale ELF
```
