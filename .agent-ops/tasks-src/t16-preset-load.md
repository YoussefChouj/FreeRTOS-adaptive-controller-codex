# T16: make `python -m ground_station.service --preset flight_test_adaptive` actually load, and fix stream_log `SYM:N`

Do not contact 127.0.0.1:8081, do not touch the probe or UDP 14550, and do not flash. Work offline: use the ELF (`OBJ/JX_FLY.axf`) and fake sockets or bridges, like the existing tests in `ground_station/comm/tests/` and `ground_station/service/tests/`. Commit with LF line endings on your branch.

## Evidence (supervisor, live, 2026-09-25 15:30, measured)

The supervisor has already made two fixes in the main working tree. They are **uncommitted**, so first they are committed on main (your branch includes them).

1. `ground_station/service/__main__.py` `apply_startup_preset` called `bridge.subscribe_slot(..., schema_timeout=1.0)`. That kwarg does not exist, so `--preset` **never** loaded: the service logged `WARNING: preset ... failed to auto-load` and fell back to the 2-slot dashboard layout. The kwarg is now removed.
2. The bridge reboot watchdog (`wifi_bridge.py` `_check_resubscribe`) re-sent the dashboard layout over the preset. Two changes fix that:
   - `bridge._resubscribe_fn`: when a preset is active, the watchdog replays the preset instead.
   - `apply_startup_preset` sets `_resubscribe_layout = None` before the pre-clear.

With those two fixes the next failure is:

```
[service] WARNING: preset 'flight_test_adaptive' failed to auto-load: stream: 93 ranges exceeds the firmware limit of 62 -- widen a range's count instead of adding entries
```

The manifest var counts are:

| Manifest | Vars |
|---|---|
| `dashboard_frame_a` | 93 |
| `inner_loops` | 9 |
| `flight_test_outer` | 24 |
| `flight_test_position` | 9 |

The firmware limit is `SUBSCRIBE_MAX_STREAM_RANGES = 62` ranges per slot (`API/subscribe.h`, `ground_station/livewatch/stream.py:50`). A range is (addr, size, count), which is why the bridge's own dashboard layout fits (54 ranges on slot 0 and 25 on slot 1).

## Task A: preset loading

Make `apply_startup_preset` load every slot of `flight_test_adaptive`, and also `flight_comprehensive`.

- Preferred approach: in the path that `subscribe_slot(ranges=[names])` uses, coalesce names whose addresses are **contiguous and have the same element size and format**, like `x[0..5]` or adjacent struct floats, into one range with count > 1.
- Decoded channel names must stay **exactly** the per-name keys they are today:
  - `slot0.mrac_state.roll.Theta[3]` and so on.
  - The recorder CSV `key` column and `ground_station/analysis/flight_signals.yaml` depend on them.
- Read how the 0x08 schema reply is decoded and named (`_pending_schema_ranges`, `_handle_schema_frame`) and keep that consistent.
- If, after coalescing, a manifest still has more than 62 ranges, raise a clear error that names the slot and the count.
- Tests:
  - offline, with the real ELF (skip if absent) and a fake socket that captures the 0x21 request;
  - `flight_test_adaptive` and `flight_comprehensive` produce 62 or fewer ranges per slot;
  - decoding a synthetic 0x08 schema plus a data frame yields the exact per-name keys;
  - `apply_startup_preset` with a fake bridge subscribes all 4 slots;
  - the watchdog replays the preset.
- Also log `[service]   slot N -> manifest @ Hz (divider, vars, ranges)` for each slot.

## Task B: stream_log `SYM:N`

Measured bug: `python -m ground_station.livewatch.stream_log --group "20:rpm_dbg_period_cyc:4"` writes a CSV with one column, `rpm_dbg_period_cyc`, and the frame is 12+4 bytes, so `:4` was ignored. The same happens inside a comma list: `"20:rpm_dbg_period_cyc:4,rpm_dbg_edges:4"` gives 2 values and 20 B/frame. (`rpm_dbg_period_cyc` is `volatile uint32_t[4]` in `BSP/rpm.c:35`.)

- Fix the parsing so it logs N elements as columns `name[0]`..`name[N-1]`.
- Add an offline test.

## Finish

- Run `python -m pytest ground_station/comm ground_station/service ground_station/livewatch -q`.
- Then run the whole tree once: `python -m pytest ground_station -q`.

Report in `.agent-ops/out/t16-report.md`:

- the root cause at file:line;
- the design;
- per-slot range counts for both presets (computed offline);
- the verbatim test counts;
- what was NOT done.
