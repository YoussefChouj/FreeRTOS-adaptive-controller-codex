---
name: capture-multislot
description: >
  Drive a multi-slot subscribe capture over WiFi from a shell (no GUI). Mirrors the
  proven dashboard `_multislot_run` pattern: pre-clear stale FC subscription state,
  subscribe each slot sequentially with a 100 ms gap, decode 0x09 data frames, write
  one CSV per slot. Use when an agent needs to capture flight telemetry without
  launching the dashboard, when the dashboard is unavailable (CI, remote box, headless
  runs), or when the same capture needs to be reproduced deterministically across runs.
  Reads multi_slot_presets.yaml, writes CSVs that multislot-analyze consumes unchanged.
  See /multislot-analyze for the analysis half of the pipeline.
---

# capture-multislot — agent-facing multi-slot capture

The dashboard has a multi-slot recorder but it is GUI-only. This skill is the CLI
equivalent: same proven capture pipeline, same CSV output format, no Tk dependency.

## When to use

- "Capture a multi-slot flight log" without launching the dashboard.
- Reproducing a captured run on a different host (CI, remote bench box).
- Deterministic re-capture of a flight for diff/comparison.
- Building a CSV dataset for offline analysis (control system ID, MRAC weight
  evolution, EKF state trajectories).
- When the dashboard crashes mid-capture but you want to keep the experiment going.

## The canonical command

```bash
.venv\Scripts\python.exe -m ground_station.livewatch.capture_preset flight_comprehensive --secs 12
```

The script lives in `ground_station/livewatch/` (same package as `verify`,
`watch`, `stream_log`) so the standard `python -m` form works.

### Invocation options

| Command | Works? | Notes |
|---|---|---|
| `python -m ground_station.livewatch.capture_preset <args>` | yes | Canonical form. Same package as the other `livewatch` tools. |
| `python ground_station/livewatch/capture_preset.py <args>` | yes | Direct script run. cwd auto-added to sys.path. |
| `python -m capture_preset <args>` (after `cd ground_station/livewatch/`) | yes | Treats the script as a top-level module. |

Use the canonical `-m` form. It matches every other tool in the
ground-station suite.

Available presets live in `ground_station/livewatch/multi_slot_presets.yaml`:

| Preset | Slots | Manifests | Requested rates |
|---|---|---|---|
| `flight_comprehensive` | 4 | sync / inner_loops / mrac_weights / ekf_all | 10 / 80 / 50 / 50 Hz |
| `mrac_characterization` | 3 | sync / mrac_signals / mrac_weights | 10 / 50 / 50 Hz |
| `thrust_validation` | 2 | sync / thrust_estimators | 10 / 200 Hz |

Output lands in `ground_station/logs/multislot/slot{N}_<manifest>_<hz>hz_<timestamp>.csv`,
one file per slot. Then run `/multislot-analyze` on the timestamp to decode.

## What it does (six steps)

```
   ┌──────────────────────────────────────────────────────┐
   │ 1. Pre-clear FC subscription state                   │
   │    3 rounds of stop commands to all 4 slots + nudge │
   │    drains N stale packets (usually 0-300)           │
   └────────────────────┬─────────────────────────────────┘
                        ▼
   ┌──────────────────────────────────────────────────────┐
   │ 2. Open ephemeral UDP socket (no bind)               │
   │    SO_RCVBUF = 65535                                 │
   │    FC replies to source port automatically           │
   └────────────────────┬─────────────────────────────────┘
                        ▼
   ┌──────────────────────────────────────────────────────┐
   │ 3. Subscribe slots SEQUENTIALLY with 100 ms gap     │
   │    Wait for 0x08 schema for slot N before slot N+1  │
   │    Reject with clear error if 0x7F error frame seen │
   └────────────────────┬─────────────────────────────────┘
                        ▼
   ┌──────────────────────────────────────────────────────┐
   │ 4. Build MultiStreamDecoder from collected schemas  │
   └────────────────────┬─────────────────────────────────┘
                        ▼
   ┌──────────────────────────────────────────────────────┐
   │ 5. Open per-slot CSV writers (sample_idx,tick,hex)  │
   └────────────────────┬─────────────────────────────────┘
                        ▼
   ┌──────────────────────────────────────────────────────┐
   │ 6. Stream for --secs seconds, write 0x09..0x0C data │
   │    Print per-second status + final measured Hz/slot │
   └──────────────────────────────────────────────────────┘
```

## The pre-clear dance (critical)

**Why:** after a hung dashboard capture, a reflash, or any session that did not
clean up cleanly, the FC's `Subscribe_StreamTick` still holds the previous run's
slot state. A subsequent subscribe request for that slot is then silently
ignored — no schema reply, no error frame, no useful signal. The capture looks
like it started but no data flows.

**What we do:** three rounds of stop commands to all four slots, with a 20 ms
gap between commands and a 300 ms drain between rounds. Then we drain any
remaining bytes on the wire for 2 s. The first nudge (`b"\x00"`) flips the
MicoAir module's downlink routing to our source port — without it we receive
nothing at all.

**Proof it works:** the 2026-09-12 cold-boot capture drained **246 stale
packets** during pre-clear, then all four slots streamed cleanly afterwards.
Without pre-clear the same code returned empty CSVs.

**Code:** `_pre_clear_fc()` at the top of `ground_station/livewatch/capture_preset.py`.

## The wire-budget caveat (RESOLVED 2026-09-12)

Measured rates in 4-slot MIXED-mode configurations now land within ±2% of
**the requested Hz**. The fix was two-line:

1. **Firmware** (`API/subscribe.h:267`): `SUBSCRIBE_SEND_TASK_HZ = 200U → 80U`
   to match the measured MIXED-mode Send_Task cadence (the legacy
   Frame A/B/C out path steals ~3 ms from the 5 ms floor; the actual
   cycle is ~80 Hz).
2. **Host** (`ground_station/livewatch/capture_preset.py:189`):
   `divider = int(200 / hz) → int(80 / hz)` so the host-supplied
   divider matches the firmware constant.

Commits `4909e24` (firmware) + pending (host). Full validation in
`.agent_contracts/2026-09-12-wire-budget-firmware/journal.md`.

For the four-slot `flight_comprehensive` preset, captured 2026-09-12 20:53
(3 runs of 10 s each — see "30 s capture stall" note below):

| Slot | Manifest | Requested | Divider | Measured | Note |
|---|---|---|---|---|---|
| 0 | sync | 10 Hz | 8 | 10.1–10.2 Hz | exact |
| 1 | inner_loops | 80 Hz | 1 | 80.5–81.7 Hz | exact |
| 2 | mrac_weights | 50 Hz | 1 | 80.5–81.8 Hz | rounds up (`int(80/50)=1`) |
| 3 | ekf_all | 50 Hz | 1 | 80.5–81.7 Hz | rounds up (`int(80/50)=1`) |

All 12 measurements within ±2% of `80 / divider`. Slots 2 and 3 land at
80 Hz, not 50 Hz — the spec's `int(80/50)=1` rounding. Out of scope for
the fix; if "exactly 50 Hz" is required, drop those slots to "40 Hz
nominal" or split them across more slots.

**30 s capture stall (NEW 2026-09-12):** the post-fix aggregate rate
(320 frames/s on USART3, ~24 KB/s) saturates the host's
`SO_RCVBUF=65535` around the 5-second mark. The recv loop sees no more
packets. UA3TxDrops on firmware stays at 0 (peak ring 405 B / 4096 B);
the loss is purely host-side. Workaround: cap `--secs` at ≤10 s. Long
captures need a separate host-tooling fix (raise SO_RCVBUF or drain
faster) — tracked as a follow-on.

## Gotchas that have actually bitten

- **The MicoAir module must be the most-recent UDP source.** Without a
  recent uplink from our socket, downlink goes elsewhere and we receive
  nothing. The first action of `_pre_clear_fc()` is the nudge
  (`b"\x00"` uplink) — leave it in place.
- **`SO_RCVBUF=65535` is required on Windows.** Default Windows UDP
  receive buffer is 8 KB; WiFi subscribe frames can arrive back-to-back
  at 200+ Hz. Without the explicit setsockopt you drop frames silently.
- **The pre-existing slot 0 manifest MUST be `sync`** for the analyzer
  to decode (it carries the four contract vars: ARM/FM/voltage/tick).
  Other manifests can fail validation — `MultiSlotPresetManager.get()`
  raises ValueError if the contract is broken.
- **The captured CSV does not carry `tick` for raw 0x09..0x0C frames.**
  The script writes `tick=0` per row; the analyzer re-derives the tick
  from the slot payload. If you need per-sample firmware ticks, use
  `python -m ground_station.livewatch.stream_log --transport usart3`
  instead (single-slot, full timestamp).
- **The ELF must match the flashed image.** The script resolves manifest
  variable names against `OBJ/JX_FLY.axf` via DWARF. Stale ELF = silent
  garbage. Run `python -m ground_station.livewatch verify` first if you
  are not sure.

## After the capture: analyze

Hand the timestamp to `/multislot-analyze`:

```bash
.venv\Scripts\python.exe -m ground_station.scripts.multislot_analyze 20260912_152258
.venv\Scripts\python.exe -m ground_station.scripts.multislot_analyze --latest --plot
```

The decoder consumes the captured CSVs unchanged — same format the dashboard
writes, so any existing analysis scripts work as-is.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `TimeoutError: slot N schema not received` | Pre-clear missed something, or slot was already active | Re-run; if persistent, power-cycle the FC |
| `RuntimeError: refusing to start: preset X would put N% on the USART3 wire` | Pre-flight budget check rejected (frame_payload × rate > 80% wire) | Pick a smaller preset, lower per-slot rates, or split across two captures |
| `RuntimeError: firmware rejected slot N: <msg>` | FC sent a 0x7F error frame | Read the msg — usually divider out of range or manifest var unknown |
| `FileNotFoundError: OBJ/JX_FLY.axf` | Stale path or no fresh build | Rebuild firmware, then re-run |
| Empty CSVs (0 rows) but exit 0 | Pre-clear missed, FC slot stuck | Power-cycle FC, then re-run with longer `--secs` |
| Measured rate drops to ~half the requested rate after ~5 s of a long capture | Host `SO_RCVBUF=65535` saturated by post-fix 320 frames/s aggregate; recv loop stalls, samples lost. **Firmware drops stay at 0** — this is host-only | Cap `--secs ≤ 10` for now. Follow-on will raise SO_RCVBUF / drain faster. |

## Where things live

| What | Where |
|---|---|
| This skill's script | `ground_station/livewatch/capture_preset.py` |
| Skill doc | `.cursor/skills/capture-multislot/SKILL.md` |
| Purpose | `.cursor/skills/capture-multislot/PURPOSE.md` |
| Preset definitions | `ground_station/livewatch/multi_slot_presets.yaml` |
| Manifest definitions | `ground_station/livewatch/manifests.yaml` |
| Wire-format protocol | `docs/telemetry-protocol.md` |
| WiFi link setup | `/micoair-connect` skill |
| Analyzer (next step) | `/multislot-analyze` skill |
| Wire-budget investigation | `.agent_contracts/2026-09-12-wire-budget/` |
