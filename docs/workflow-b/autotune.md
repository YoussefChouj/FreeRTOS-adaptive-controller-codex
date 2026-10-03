# Workflow B: automatic PID tuning (WP-25 method, WP-26 code)

Model-based: excite one rate loop, estimate the plant FRF (IV, dither as instrument), fit integrator + lag + delay,
design gains for margin specs, verify in flight, revert if worse. Every number here is PROPOSED, none measured.

## Flights (3 packs minimum, about 85-95 s airborne each)
| # | Campaign | What flies | Then the agent runs |
|---|---|---|---|
| F1 | `autotune_rate_roll` | takeoff 0.5 m (MRAC injection 0), hold 15 s, roll multisine 0.5-15 Hz 60 deg/s 30 s, hold 5 s, land | `python -m ground_station.autotune.cli <F1 session> --axis roll` |
| F2 | `autotune_rate_pitch` | same on pitch | `... cli <F2 session> --axis pitch` |
| F3 | `autotune_verify` | write both proposals' knobs first; hold 15 s, roll then pitch multisine 40 deg/s 12 s, x and y 0.3 m doublets, land | `... cli <F3 session> --axis roll --verify <F1 json>` and the same for pitch |
| F4 (opt) | none | angle loop from the accepted rate gains | `... cli <F3 session> --axis roll --loop angle --rate-gains Kp,Ki,Kd --duration 12 --amp 40` |

Sessions are the recorder dirs in the campaign summary (`FlightRecord.recording`). The cli writes
`autotune_<axis>_<loop>.json` (and `..._verify.json`) into the session dir.

## What the cli prints and decides
- FRF: coherent bins (coherence >= 0.6), band coverage (share of 10 log sub-bands of 0.5-15 Hz with a coherent bin).
- Fit: k (deg/s^2 per mixer unit), tau, delay, relative residual.
- Margins (PM, GM, crossover, max|S|) of current, ideal and proposed gains, firmware and continuous units
  (Ki_fw = Ki * 0.005, Kd_fw = Kd / 0.005).
- Knobs: `gyroxPID.Kp = ... (cmd 0x01 idx 9)`. Only Kp and Kd change on the rate loop (both are pid.yaml knobs, so
  `campaign_api.apply_params` can write them); Ki stays. The angle loop changes `rollPID.Kp` / `pitchPID.Kp` only.
- Design rule: lowest 30 deg/s step ITAE with PM >= 50 deg (45 + 5 buffer), GM >= 6 dB, max|S| <= 2, no UMax
  saturation, and |C| at Nyquist <= 1.5 x current (D has no filter). Each gain moves at most +-30 % per flight;
  if the ideal is further, fly the step and repeat F1-F3.

Exit codes: 0 proposed / keep, 2 refused (reason printed, nothing to write), 3 verify failed (revert).

## Refusals (nothing is proposed)
- No multisine found in `gyro?PID.Des` (excite did not run, or wrong f0/f1/duration flags).
- Coherence in fewer than 5 of 10 sub-bands, or fewer than 4 coherent bins.
- Fit residual > 0.35, or a non-positive plant gain.
- No gains in the search box meet the spec and none improve PM over current.

## Pass / revert rule (F3, per axis)
Keep only if the re-measured loop (short multisine under the new gains) has PM >= 45 deg, GM >= 6 dB,
max|S| <= 2, and the pre-excite hover rate-error RMS is <= 1.1 x the F1/F2 baseline. Otherwise, or if the
plant cannot be re-measured, the cli prints the G_prev knobs: write them back before any other flight.
An F3 that aborts is also a revert.

## Abort conditions (any one lands the flight)
- Operator: RC stick (firmware SysID dead-man, then RC takeover), or `land` / `abort` in chat.
- GS monitor with the campaign's `abort: {tilt_deg: 15}` (now applied to the live monitor), position error,
  rate-error RMS, telemetry stale, firmware landing (fence, ceiling, heartbeat, low voltage, airborne cap).
- Firmware SysID: |roll| or |pitch| > 30 deg, altitude outside 0.30-1.50 m, > 50 cm from the start point,
  disarm, mode change -> RECOVERY (excitation off, return to the start point).
- Runner: the excite step starts only after a hold, at prim HOVER, within 0.15 m of the hover point (waits 5 s);
  otherwise it lands without exciting.

## Risks to watch
- Starting a run re-zeroes the optical-flow origin and the loc setpoints (send_data.c:1848-1865). The hover point
  and the fence move with the drone by its drift at that moment (bounded by the 0.15 m check). Watch the fence.
- MRAC injection stays off after the campaign (CMD 0x0F idx 10 = 0). Re-enable it, or reboot, after acceptance,
  and re-check MRAC on the new gains.
- The 0x03 ID frame is auto-enabled by SysID, but the WiFi bridge decodes 0x03 with an old layout
  (wifi_bridge.py:1918-1943), so the cli uses the core streams (`gyro?PID.Des/FB`, `xTickCount`) at 100 Hz and
  rebuilds the dither. A serial-bridge log with `id.*` keys is used directly when present.
