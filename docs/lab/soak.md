# Lab in a box: the soak

`ground_station/lab/soak.py` flies whole lab days through `labday` against the fake drone: the real 8081 service,
the real fly, the real recordings, and no hardware. Nothing is armed for real; the RC arm is `fake_drone ctl arm`.

## Run it

    python -m ground_station.lab.soak                          # 3 days back to back, 8 flights a day (~1 h 15 min)
    python -m ground_station.lab.soak --days 1 --flights 2     # quick check: one day, two flights (~6 min)

| flag | default | what |
|---|---|---|
| `--days` | 3 | days flown back to back on one service |
| `--flights` | all in the day file | first N flights of each day (with 5 or fewer, the swap is the one before the last) |
| `--day-src` | `docs/lab/days/2026-10-19.yaml` | the day the fixture is copied from |
| `--out` | `logs/soak/<stamp>` | run folder |

Exit code 0 only if every check passed. The run folder has `report.md` (per-flight table + checks), the service
and fake-drone logs, and each day's labday transcript.

One soak per machine: the fake drone binds 127.0.0.2:14550 (telemetry) and 14551 (control). Stop any real 8081
or fake_drone first; the soak refuses to start if either port is taken. 8081 itself goes on a spare port. It also
wants 3 GB free: a soak day writes about 0.5 GB of recordings into the tree copy.

## What it runs

1. Copies the working tree to `<run>/tree`, without `docs/flights/`, `docs/dashboard-platform/`, `OBJ/`, `logs/`,
   `scratch/`, `.agent-ops/`. Everything the soak writes (recordings, labday state, outputs, `catalog.yaml`)
   lands in that copy, never in the real `logs/` or `docs/lab/catalog.yaml`. Builds the symbol-image ELF there.
2. Starts `ground_station.lab.fake_drone` at its real rates and the service with
   `--lab --wifi-host 127.0.0.2 --relay-port 0`, cwd = the tree copy.
3. Per day: writes a fixture from the day file (camera off, carry-over off, its own day name) and runs
   `labday <fixture> --port P --cooldown 0 --no-camera --pack 1`.
4. A Driver reads the labday transcript and answers like the operator: Enter at each flight, `ctl arm` at
   "preflight OK", `ctl disarm` after the landing, `ctl vbat 16.4` (a charged pack) between flights.
5. The 5th flight of each day (8 in the 10-19 plan) is the mid-day pack swap: `ctl reboot` (12 s silent) then `ctl vbat 16.4`. The fly
   must see the link drop, see it come back, ask for the new pack, pass preflight again, and fly; the rest of the
   day must still complete.

A prompt the Driver does not know, a refused GO (the fly would wait for a new arm forever), a dry-run failure or
15 min with no labday output ends the day and fails the run with the reason in `report.md`.

`SILENT_S = 12`: the fly calls the link lost only after the streams are `LINK_LOST_S` old for another
`LINK_LOST_S` (over 6 s in all). A shorter silence never shows the swap path; a real swap takes longer still.

## Checks

Each reads the outcome, not an exit code.

| check | passes when |
|---|---|
| every flight complete | per flight, the `debrief` event, the labday "done" line and `metrics.json` all say `complete`, abort level 0 |
| zero stale_telemetry aborts | no `ABORTED ... stale_telemetry` in the transcript, none in metrics, no mention in the events or service log |
| ingest lag per flight | `/health/ingest_lag?since_s=` around each flight: p50/p99/max in the table; fails on any frame over 200 ms, and on a flight with no samples |
| ingest lag, whole run | the same histogram over the run, ground time included |
| pack swap | link lost, link back, new pack asked, 2 preflights, swap flight complete, every later flight complete, day done |
| days back to back | every day reaches "day done" on the one service (same pid and `started_at` throughout) |
| no backlog or drops | `/health` after each flight: ingest queue <= 10, dropped 0 |
| no memory leak | service RSS at the end of the last day minus day 1 <= 64 MB |
| SessionStore cap | `/health/store` after each flight: telemetry and raw_frames rows < `max_rows + prune_every` (10000 + 1000) |

The store bound is 11k, not 10k: the store prunes once every `prune_every` inserts, so a table can hold up to
`max_rows + prune_every - 1` rows between prunes. More than that means the cap stopped working. `/health/store`
reports `prune_every` so the soak does not hard-code it.

## Found by the soak: the second flight never armed (fixed)

The first quick soak flew mem1 and then hung on mem2 at `NO ARM SIGNAL`, the same symptom as the 10-18 mem1 hang
(`campaign_fly.NO_ARM_MSG`, cc4784b).

Cause: in lab mode slot 0 carries the lab sidebar frame at 10 Hz, which has `DroneStatus.ARM_Status`, the flag
`wait_for_arm` waits on. `live_capture_hooks.begin()` shifts the plan's slots up by one only when the plan has
fewer than 4; a 4-slot plan (mem1's) takes slot 0. `end()` then stopped every plan slot, slot 0 included, so
after the first 4-slot flight nothing carried the arm flag until 8081 restarted. `prime_status_stream` would put
it back, but it runs only at GO, after the arm wait.

Fix (no rate or feature cut): `end()` gives slot 0 back to the lab frame (`WifiBridge.restore_lab_slot0()`, slot 0
only, divider 10) and stops only slots 1-3. The lab watchdog still never re-sends a layout.

Tests: `test_lab_capture_end_gives_slot0_back_to_the_arm_flag`,
`test_restore_lab_slot0_requests_only_slot0_at_10hz` in `ground_station/service/tests/test_lab_mode.py`.

## Found by the soak: the fake drone's heartbeat clock ran on the ground (fixed)

Full soak 3 lost d2 z2 and d3 z1 to `firmware:HEARTBEAT` (operator_needed, L3) 0.2 s into the air with a healthy
link. The fake drone aged its heartbeat through the whole ground wait and tripped the moment TAKEOFF set
`gs_flight_active`. The firmware sets `hb_ref_ms = now` on TAKEOFF accept (`API/wfb_glue.c`), so its timeout runs
from take-off. The fake drone now does the same (2b0093a); `hb_timeout_s` stays 1.0. A bug in the test drone, not
in 8081 or the firmware. Test: `test_8b_heartbeat_clock_starts_at_takeoff` in
`ground_station/service/tests/test_fake_drone.py`.

## Results

(filled in from the runs below)
