# livewatch

Non-intrusive **live variable watch** for the STM32F407 flight controller.

Reads RAM off the *running* target over the wireless CMSIS-DAP probe (pyOCD, attach
mode — never halts, resets, or writes the core) and resolves **any** variable, struct
field, or array element to its address *by name* straight from the firmware's DWARF
debug info. No frame to choose, no firmware change, no offset tables to maintain.

This is the "Keil live-watch window, but scriptable" tier: the bench/agent path for
diagnosing the running firmware. The rationed UART telemetry is the separate
*beyond-radio-range in-flight* tier.

## Safety contract (do not weaken)

`reader.py` opens the probe with `connect_mode="attach"`, `target_override="cortex_m"`,
`resume_on_disconnect=False`, and only ever calls read paths. There is **no**
write/halt/reset code in this package. Reading RAM cannot change the ARM flag, so a
disarmed drone stays disarmed and motors cannot spin. Safe to run with props on.

## Usage

Offline (pure DWARF, no hardware — works any time `OBJ/JX_FLY.axf` exists):

```
python -m ground_station.livewatch groups            # curated watch groups
python -m ground_station.livewatch names --filter mrac
python -m ground_station.livewatch fields mrac_state.pitch
```

Live (opens a read-only attach session to the running target):

```
# one-shot read
python -m ground_station.livewatch read group:ekf group:health

# stream at 20 Hz for 10 s, log to CSV; Ctrl-C stops
python -m ground_station.livewatch watch \
    system_monitor.stabilizerTask_cnt s_ekf.x[0] s_ekf.nis \
    --hz 20 --secs 10 --csv run.csv
```

Names are DWARF paths: base symbol, then `.member` and `[index]` (e.g. `s_ekf.x[3]`,
`mrac_state.pitch.Whatf`). `group:<name>` expands a registry group.

## Programmatic

```python
from ground_station.livewatch import LiveReader
with LiveReader("OBJ/JX_FLY.axf") as lr:
    plan = lr.plan(["s_ekf.x[0]", "s_ekf.x[3]", "s_ekf.nis"])
    print(lr.sample(plan))               # {'s_ekf.x[0]': ..., ...}
    for row in lr.stream(["s_ekf.nis"], hz=50, duration=5):
        ...                              # {'t': ..., 's_ekf.nis': ...}
```

## Design notes

- **symbols.py** — DWARF resolver. `SymbolResolver.resolve(path) -> Symbol(address,
  size, fmt)`. Strips typedef/const/volatile, walks struct members and array
  elements, maps base-type encodings to `struct` format chars. Offsets stay correct
  across rebuilds because they come from the same ELF you flash.
- **reader.py** — `build_plan` coalesces watched symbols into the minimum number of
  contiguous block reads (one CMSIS-DAP transaction each; per-transaction latency
  dominates, so watching a whole struct costs ~1 transaction). `build_plan` and
  `Plan.decode` are pure — unit-tested offline against synthetic bytes. Merge
  threshold `_GAP_MERGE_BYTES` trades unused bytes for round-trips (efficiency only,
  never correctness); re-benchmark once real wireless-probe throughput is known.
- **registry.yaml / registry.py** — named groups of important variables. Adding an
  entry is free; cost is paid only for the group you actively poll.

## The registry

`registry.yaml` holds curated groups (`ekf`, `attitude`, `health`, `mrac_pitch`,
`mrac_roll`). Add variables freely — a definition is a DWARF lookup, effectively
zero cost. `tests/test_livewatch.py::test_registry_groups_resolve` fails if any
registry entry goes stale against the current firmware, so a bad rename is caught.

## Tests

```
python -m pytest ground_station/livewatch/tests -q
```

11 offline tests: path parsing, DWARF resolution vs golden `.map` addresses, scalar
typing, coalescing, decode round-trip, registry integrity. No hardware needed.
