# SESSION_DATA — where streamed telemetry samples go

Answer to the operator question ("where do streamed telemetry samples go, and is a
session's data saved?").

## Before this change (the gap)

When the dashboard service ran as `python -m ground_station.service`, incoming
samples were only ever kept in-process:

* Live counters / ring-style recent state live in `GroundStationService._streams`
  and the bounded `deque`s in `ground_station/service/core.py` (`ingest`,
  `ingest_decoded`, `inject_external_stream`).
* Every sample was also written to a `SessionStore`, but the service constructed it
  as `SessionStore()` — i.e. **SQLite `:memory:`** (`core.py` line ~227). So the
  session, events, telemetry and raw frames existed only in RAM and vanished on
  process exit. Replay/`/sessions/<id>/records` worked **within** a run but there
  was no on-disk, per-session artifact an operator could retrieve or back up.

So: samples went to RAM, period. Nothing survived a restart.

## What exists now

A minimal per-session recorder writes every adapted sample to CSV, one file per
service run:

```
logs/sessions/<YYYYmmdd-HHMMSS>/telemetry.csv
```

**Long format** (robust to the key set changing between frames):

```csv
received_ns,slot,key,value
...
```

where

| column      | meaning                                                        |
|-------------|----------------------------------------------------------------|
| `received_ns` | wall-clock ns at ingest (same value the live snapshot uses)  |
| `slot`        | source slot (0/1/3, sidebar `a`, or `rtos` for the SWD bridge) |
| `key`         | channel name, e.g. `status.arm`, `mrac.alt`, `xTickCount`      |
| `value`       | the scalar value                                               |

Rows are buffered and flushed at most every 1 s from a background daemon thread
(`CsvRecorder` in `ground_station/service/storage.py`), so the ingest path never
blocks on disk I/O. A write error is counted, not raised. On shutdown the service
flushes and joins the writer, so `stop()` returns with the CSV complete.

### Status

`GET /health` gains a `recorder` block:

```json
"recorder": {"enabled": true, "started": true,
             "path": "logs/sessions/20260922-060001/telemetry.csv",
             "rows": 1234, "errors": 0}
```

* `enabled`/`started` — recording is on and the writer thread is running.
* `path` — the CSV for the current service run.
* `rows` — data rows written so far.
* `errors` — swallowed disk/enqueue errors (a nonzero count means data was dropped).

### Recording is opt-in

Recording is **off by default**: the service writes nothing and creates no
session directory until recording is started explicitly — from the dashboard's
Record control or over the HTTP API. Env vars:

```
GS_RECORD=1   # auto-start recording at service boot
GS_RECORD=0   # forbid starting recording at all
(unset)       # stopped until an agent/operator starts it
```

The output directory is configurable with `GS_RECORD_DIR=<dir>` (default
`logs/sessions`).

### HTTP routes

| Route | Purpose |
|---|---|
| `GET  /api/recording` | `{recording, session_dir, started_at, rows, bytes, reason, enabled}` |
| `POST /api/recording/start` | Body `{reason?, requested_by: "operator"\|"agent:<name>", label?}`. Start while recording is a no-op returning current state. |
| `POST /api/recording/stop` | Idempotent; finalises the manifest. |
| `POST /api/session/note` | Body `{text, kind: "note"\|"goal"\|"marker", source?}`. Buffered (last 50) when not recording, flushed into the next recording's `events.jsonl`. |
| `GET  /api/session/notes` | Buffered notes held while not recording. |

### What a recording directory contains

```
logs/sessions/<YYYYmmdd-HHMMSS>[-label]/
  telemetry.csv     long-format telemetry rows (see below)
  events.jsonl      rare session events, one JSON object per line
  manifest.json     session metadata (below)
```

`manifest.json` (`schema_version: 1`):

```json
{
  "schema_version": 1,
  "started_at": "<ISO>", "started_at_epoch": 1.0,
  "stopped_at": "<ISO>", "stopped_at_epoch": 1.0,
  "requested_by": "operator", "reason": "…", "label": "…",
  "subscribe_layout": {"schema_id": "…", "slots": [ … ]},
  "context": {"started_commit": "abc1234" | null,
              "firmware_elf": {"path": "OBJ/JX_FLY.axf", "size": 1004352,
                               "mtime": 1.0} | null},
  "rows": 1234, "files": [ "events.jsonl", "manifest.json", "telemetry.csv" ],
  "errors": 0
}
```

`events.jsonl` — one JSON object per line:
`{"t": <epoch s>, "iso": "<ISO>", "kind": "<kind>", "source": "service|operator|…", "data": {…}}`.
Kinds: `recording_start`, `recording_stop`, `command` (data carries `id`, `idx`,
`value`, `transaction_id`, `lifecycle` = SUBMITTED/ACKNOWLEDGED/APPLIED/
REJECTED/TIMED_OUT/VERIFIED, `reason`), `arm_state`, `stream_stall`,
`stream_recover`, `note`/`goal`/`marker`. Telemetry rows stay in `telemetry.csv`;
events are rare by design (hooked at the existing single points, never polled
row-by-row).

## Loading it in pandas

```python
import pandas as pd
df = pd.read_csv("logs/sessions/20260922-060001/telemetry.csv")
# one row per (slot, key) per received_ns:
pivot = df.pivot_table(index="received_ns", columns=["slot", "key"],
                       values="value", aggfunc="last")
```

## Relationship to the in-memory store

The CSV is a durable, operator-facing export. The `SessionStore` (SQLite,
`ground_station/service/storage.py`) still owns session lifecycle, command events,
raw frames and deterministic replay. Those stay in memory for the default service
run; they are **not** yet written to disk — the CSV recorder is the on-disk
persistence. Wiring `SessionStore` to a file path (instead of `:memory:`) is
generalized persistence beyond this change.

## Code sites

* Recorder implementation: `ground_station/service/storage.py` — `CsvRecorder`.
* Wiring / env vars / flush-on-stop: `ground_station/service/core.py` —
  `__init__`, `start`, `stop`, `_note_recorder`, and the three ingest paths.
* `/health` recorder block: `ground_station/service/api.py` — `_recorder_status`.
* Ignore rule: `.gitignore` covers `logs/sessions/`.