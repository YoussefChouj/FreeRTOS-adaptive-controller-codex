---
name: stream-log
description: >
  Log firmware variables from the running drone to CSV over serial or WiFi, at up to four
  independent rates, by editing a Markdown table — no firmware change and no reflash.
  Use when the user asks to log/record/capture variables to CSV, mentions "telemetry frame",
  "log X at N Hz", "subscribe", or "slots". Also covers the build/flash pipeline for cases
  where firmware C actually changed.
---

# stream-log — variable-rate telemetry to CSV

The firmware carries a runtime **subscription** protocol: the host names a set of RAM
ranges, the firmware replies with a schema once, then pushes values-only frames at the
requested rate. Four slots run concurrently at independent rates.

**Read this first, because it decides whether you need the dangerous half of this skill:**

> Changing *which variables* are logged, or *at what rate*, needs **no rebuild and no
> flash**. It is a wire message. Only changing the subscribe protocol itself needs firmware work.

## 1. Logging (no firmware change — the common case)

The frame definition lives in `ground_station/livewatch/log_frames.md`:
a Markdown table of `| Slot | Rate (Hz) | Variables |`. That file **is** the default frame.

```bash
# log the default frame
python -m ground_station.livewatch.stream_log --seconds 30 --out logs/run.csv

# override for one run, without touching the file
python -m ground_station.livewatch.stream_log ^
    --group "40:mrac_state.roll.Theta:6" --group "5:imu_data.rol:3" ^
    --seconds 30 --out logs/run.csv
```

One CSV **per slot** (`run.slot0.csv`, `run.slot1.csv`, …) — slots sample at different
rates.

### Before you promise a rate

Two hard limits:

- **Link budget.** The firmware accepts up to **2304 B/s** on UART5. Measured drop-free
  ceiling is ~1600 B/s. Cost of a slot is `(7 + payload_bytes) * 100 / divider`.
- **Slots.** Four. A fifth `--group` is an error.

### Gotchas that have actually bitten

- **`t_s` is host arrival time**, not source time. Fine for "is it adapting"; **wrong for
  system identification**.
- **The ELF must match the flashed image.** Every name becomes an address via DWARF in
  `OBJ/JX_FLY.axf`. If you did not just flash, run
  `python -m ground_station.livewatch verify` first — if it says `STALE ELF`,
  **report nothing you read.**
- **`:N` is not bounds-checked.** Confirm array lengths from the header, never from "it resolved".

---

## 2. Rebuild + flash (only when firmware C changed)

Run the pipeline. Do not reassemble it by hand:

```powershell
python -m ground_station.flashtool.rebuild_and_flash              # build only
python -m ground_station.flashtool.rebuild_and_flash --yes        # build + flash
```

`--yes` is the operator's consent to flash. **The drone being powered on is not consent.**

Exit codes: 2 uVision GUI resident, 3 build failed, 4 `uvoptx` not restored byte-exact,
5 target dark, 6 arm gate refused, 7 flash failed after retries, 8 target did not come back up.
**Never work around a non-zero exit** — each one means a specific guard fired.

### What it does, and why each step is there

1. **Refuses if a uVision GUI is resident** — it holds `OBJ/` handles and breaks the build.
2. **Snapshots `OBJ/JX_FLY.{axf,hex,map}`.** A rebuild relinks and RAM symbols move.
3. **Builds with `<pMon>` neutralised.** Prevents the build from claiming the probe over SWD.
4. **Runs the two-oracle arm gate.** Telemetry is primary (firmware-packed Frame A
   `status_arm`). SWD read is secondary, voted 9× requiring unanimity, and only counts if
   the ELF matches the running image.
5. **Flashes, retrying transient failures.**
6. **On any failure, restores the snapshot**, so `OBJ/` never describes an image the drone
   isn't running.

Then prove it end-to-end with a short logging run — live frames at `0 dropped` is the
acceptance test.

---

## Live plot: PlotJuggler (WiFi only)

PlotJuggler 3.17.2 is installed at `D:\PlotJuggler\PlotJuggler.exe`. `stream_log --plot` sends one flat JSON
object per row: `{"t": <drone s>, "slot<k>/seq": .., "<struct>/<field>": ..}`. PlotJuggler builds the series tree.

1. In PlotJuggler: Streaming > UDP Server > Start. Address 127.0.0.1, port 9870, protocol json, tick
   "use field as timestamp if available", field name `t`. Buffer: 60 s or more.
2. `camera_truth.sh fusion` sends to 127.0.0.1:9870 by default (`PLOT=` turns it off). Manual run:

```bash
python -m ground_station.livewatch.stream_log --frames ground_station/livewatch/log_frames.md --plot 127.0.0.1:9870 --out logs/<stem>.csv
```

- Drag series from the tree onto a plot (dropping on a plot adds a curve). Select two series and drag with the
  right mouse button for an XY plot (e.g. fix_x vs fix_y).
- Layout > save keeps the plots for the next session. NaN values arrive as null and are skipped.
- Post-flight: Data > load the slot CSVs. VOFA+ FireWater (`--vofa`) stays as the fallback.

---

## Available Commands Reference

| Command | Purpose |
|---|---|
| `python -m ground_station.livewatch verify` | Check ELF matches flash |
| `python -m ground_station.livewatch names --filter <term>` | Symbol search offline |
| `python -m ground_station.livewatch fields <struct>` | Struct members |
| `python -m ground_station.livewatch groups` | Variable groups |
| `python -m ground_station.livewatch.read <var>` | One-shot read |
| `python -m ground_station.livewatch.watch <var> --hz N --secs S` | Stream read |
| `python -m ground_station.livewatch log <var> --secs S --out <path>` | Log to CSV |
| `python -m ground_station.flashtool.rebuild_and_flash --yes` | Build + flash |
| `python -m ground_station.flashtool.safe_flash` | Flash only |

## Where things live

| What | Where |
| --- | --- |
| Wire contract | `API/subscribe.h` |
| Firmware scheduler, budget guard | `API/subscribe.c` |
| Host encode/decode | `ground_station/livewatch/stream.py` |
| CLI + CSV writer | `ground_station/livewatch/stream_log.py` |
| **The frame you are logging** | `ground_station/livewatch/log_frames.md` |
| Firmware build | `ground_station/flashtool/rebuild_and_flash.py` |
