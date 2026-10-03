# Live rate-loop tuning (`livetune` step, WP-28)

One hover flight tries many rate-loop gain sets. The ground station switches gains every 6.5 s, excites the
loop, scores the response from telemetry, and CMA-ES proposes the next set. Every number here is PROPOSED;
none has been flown yet. Code: `ground_station/livetune/`. Campaign: `ground_station/service/campaigns/livetune_rate_rp.yaml`.

## Operator flow
1. Load `livetune_rate_rp` (fly mode). Optional: paste WP-26 FRF gains into `baseline:`; the default is
   `API/pid.c` gyroxPID/gyroyPID (Kp 5, Ki 0.01, Kd 10). Roll and pitch share one set.
2. Arm by RC, then Go. The agent takes off to 0.8 m, holds 3 s and starts the step.
3. The step turns MRAC output injection off (CMD 0x0F idx 10 = 0, left off) and loads the excitation
   (CMD 0x14: chirp 1 to 8 Hz, 30 deg/s peak, 1 s run between 1.5 s ramps).
4. Windows run until `budget_s` (70 s, ~10 windows) or `max_evals` ends it, then hold 3 s and land.
5. After landing, read the result. If it recommends gains, fly a **verify flight** with them (below).

## One window (6.5 s)
| phase  | time  | what happens |
|--------|-------|--------------|
| write  | ~0.3 s | CMD 0x01 Kp Ki Kd for gyroxPID and gyroyPID (6 commands) |
| settle | 2.5 s | hold; also covers the firmware SysID recovery (2.0 s) after the last window |
| excite | 4.0 s | CMD 0x14 start on this generation's axis (roll, then pitch, ...) |
| score  | 0     | J from that window's 50 Hz samples |

Each axis gets a baseline window first. CMA-ES (lambda 7 for 3 gains) ranks candidates on one axis per
generation; candidates are x = log(gain/baseline) inside +-30 % (`trust`), sigma0 0.1.

## Cost
`J = RMS(e)/A + 2.0 * mean(sat_frac) + 1.0 * a_peak(f >= 10 Hz)/A`, where e = rate FB - Des of the excited axis,
A = 30 deg/s, and a_peak is the largest spectral line (Hann rFFT) at or above f_c = 10 Hz. That band ends at 25 Hz
(the 50 Hz log rate), so the known 25 Hz limit cycle sits right at its edge. J_rel = J / baseline J of the same
axis; "best" is the lowest J_rel.

## What you will see (chat)
- `livetune gen N (roll): best J_rel 0.91 at Kp .. Ki .. Kd ..; sigma 0.087, 14 candidates` after each generation.
- `livetune: candidate Kp .. tripped (rate error 130 deg/s > 100); baseline restored` on a trip.
- At the end: `livetune budget: ... baseline restored. recommended for a verify flight: Kp .. Ki .. Kd ..`,
  or `no candidate beat the baseline by 5%: keep the baseline`. The step record also holds every window's
  J, the CMA-ES mean and its state (`es_state`).

## Abort rules
| event | action |
|-------|--------|
| \|roll\| or \|pitch\| > 15 deg, \|rate error\| > 100 deg/s, sat >= 0.5 for 0.3 s, > 0.5 m from the hover point | **trip**: abort the excitation, baseline gains at once, candidate infeasible, sigma x 0.7 |
| 3 trips in a row | stop tuning, baseline, land |
| baseline window trips, or still tripped 2.5 s after a revert | stop, write firmware defaults, land |
| telemetry older than 0.5 s, no samples, or a write not applied | stop, try the baseline, land (the WFB heartbeat timeout lands anyway) |
| prim_state leaves HOVER (RC takeover, firmware landing), safety_trip, battery < 40 % | stop, baseline; **RC takeover always wins**, the tuner never fights it |
| time budget or max_evals | stop, baseline, continue the scenario (hold, land) |

Any end writes the baseline back. The best gains are **never applied** in flight.

## Origin walk (why the drone must hold still before each start)
CMD 0x14 start resets the optical-flow origin, which moves the WFB hover point and fence with the drone
(`TASK/send_data.c:1850-1864`). Each start therefore waits until the drone is within 8 cm of the hover point;
that offset is added to a walk sum. The supervisor checks walk + position against the fence box (TrajLimits
1.3/1.7 m), and the run ends (`walk_budget`) before the walk passes 0.4 m. Proposed fix: a CMD 0x14 start
that keeps the origin (firmware + `firmware_contract.py`), not built in WP-28.

## Link loss and the gain lease
If the link drops, the revert command cannot reach the drone, so the candidate gains stay while the WFB heartbeat
timeout lands it. `API/pid.c` `GAIN_LEASE_ROW(enable, lease_ms)` (default **0 = off**, 2000 ms) restores the gains
it snapshotted at the first in-flight CMD 0x01 write when 0x01 writes stop for `lease_ms`, or when the drone
lands. The livetune loop re-sends the active Kp every 1 s as the keep-alive. Enabling the lease needs a
firmware build and a bench check first.

## Verify flight
Copy `recommended` into the descriptor knobs (`gyroxPID.*`, `gyroyPID.*`) and fly a hover + doublet campaign
with those params written on the ground. Accept it only if that flight's J is better than the baseline's.
