# Run sheet R5: live camera fusion bring-up (EXT_POS mode 2)

Written 2026-10-12 (overnight R4 run). Evidence: `docs/flights/2026-10-12-r4-overnight-report.md`.
Every threshold below is PROPOSED until a step measures it. Stop at the first failed gate.

```
flash mode-2 ─> bench (link, 3 cm kick) ─> latency event ─> track check ─> shadow out-and-back ─> closed-loop hover gate
                                                                                                     │ pass
                                                                                  dense trajectory presets ─> load scenarios
```

## 0. Build and preset (drone on the bench, disarmed, props off)

1. Flash the mode-2 build (e179e95f or later; Keil 0 errors, host test `API/tests/test_ext_pos.c` passes):
   `python -m ground_station.flashtool.rebuild_and_flash --yes` (never `--force`; stop on a non-zero exit).
2. Re-price the VOFA preset `drift_rc_extpos` against the flashed ELF (overnight: 86300 B/s computed for the
   mode-2 layout vs the 87552 budget, slot2 60/62 ranges; the old ELF lacked `c_x/c_y/n_anchor`). Apply it only if
   it fits.

## 1. Bench: link and the 3 cm slew kick

1. Logger with the relay port, then the tracker on the scrcpy window:
   ```bash
   python -m ground_station.livewatch.stream_log --frames <drift_rc_extpos frames> --seconds 3600 --out logs/r5_bench.csv --relay-port <PORT>
   python -m ground_station.analysis.live_tracker --source "window:<scrcpy title>" --cam docs/video-truth/cam_r4_lenswave_2026-10-11.json --height-m <h> --detector coral --gate --send udp:<PORT> --mode 1 --delay-ms 0 --out logs/r5_bench_fixes.csv
   ```
2. Check that the mode and delay readbacks match the hello frames and that fixes arrive at about 10 Hz.
3. Kick: switch to `--mode 2`, move the drone by hand about 20 cm and hold it there. `c_x/c_y` must ramp at most
   3 cm per step (SLEW) and settle. A step bigger than 3 cm is a firmware bug: stop.

## 2. Latency event (measures `--delay-ms`)

A sharp tap on the frame, seen in both the video and `Acc_*_Real`. delay = frame time minus IMU time plus the
link. Overnight, error grows with delay (b17 1 s-mean d60 p90: 9.2 / 10.3 / 11.5 cm at 0.15 / 0.3 / 0.5 s,
replay). Hover gate math needs about 0.15 s or less.

## 3. Coral track quality (go/no-go for every flight)

On-pad id0 found, `h_ring` 0.06 to 0.09 m (b5, b6, b9 and b12 read 0.15 to 0.17), live_tracker `gate_rej` low
(b17 9 of 3557 fixes, b5 29 of 2004). A b10-style track (700 gate rejects, 4 reseeds) defeats fusion: in replay
the firmware gated 608 fixes, re-anchored 557 times and ended at 90 cm rms. If the track jumps, fix the
lighting or background first, then repeat.

## 4. Shadow flight (mode 1): out-and-back

Hover, then about 1 m out and back on one axis, then land on the pad. Fixes are logged, not applied. Offline:
EarthAlign rotation fit (overnight rot -77 to -101 deg on 11 of 13 flights) and fuse4 replay on the new log. The
replayed fused 1 s-mean d60 p90 must be 12 cm or less to continue.

## 5. Closed-loop hover gate (mode 2)

- No sysid start and no origin reset while in mode 2 (`send_data.c` case 6 zeroes earth; Reset_World_Origin
  zeroes c).
- 90 s hover. Gate: 1 s-mean 60 s drift p90 of 10 cm or less on truth4 scoring. The camera is also the truth, so
  also measure the landing point vs the take-off mark with a tape: independent, 10 cm or less.
- Pass: go to step 6. Fail: land, mode 0, no dense or load flights today.

## 6. Only after a pass

Dense trajectory following presets, then the load scenarios (Workflow A logging, operator flies).
