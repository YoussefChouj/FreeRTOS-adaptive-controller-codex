# Drift NN target leak, 2026-10-19

The camera-off drift model (`python -m ground_station.analysis.video_truth nn4`, functions `nn4_data` / `nn4` in `ground_station/analysis/video_truth.py`) learns a velocity correction from truth4 flights: flights with camera ground truth. On a flight with camera fusion on, the target, part of the input, and the scored track all carried the camera. This note covers how the camera got in, what changed, how to evaluate the fix on the 2026-10-19 flights, and how the model would be enabled on the vehicle.

## Summary

- **The leak.** With `g_ext_pos_on` 2 or 3, the firmware adds the camera correction into the logged earth track (`ano_of.earth_x/y`). Mode 3 also feeds it into the EKF state (`s_ekf_of.x[...]`, `innov_x/y`). The old pipeline:
  - used truth − earth as its target;
  - took four EKF channels as inputs;
  - integrated its prediction onto the earth track.
  On a fused flight the "camera-off MLP" was therefore scored on a camera-aided track.
- **Who it hit.** Only fused flights (r6/r7, run sheet `docs/lab/run-sheet-2026-10-13-drift-free.md`). The R4 flights in `docs/flights/2026-10-12-r4-overnight-report.md` (§3, "Design (`video_truth nn4`)") were flown without fusion, so their table is not affected.
- **The fix (this commit).** The target is now camera truth − raw optical flow, and the inputs no longer include anything the fusion writes. Raw OF (`ano_of.of2_dx/dy_fix`) enters the firmware before the EKF and is never written by the fusion (`TASK/StabilizerTask.c:846`). The track is raw OF plus the prediction. `ext_pos_on` is reported per flight. A new `rawof` row is the baseline a model has to beat.
- **10-19 evaluation: not run.**
  - The cloud copy of the repo has no truth4 flights and no 10-19 telemetry.
  - The 10-19 flights were captured as campaign session `telemetry.csv`. truth4 and `nn4_data` read stream_log slot CSVs.
  - The steps and the format gap are under "Evaluating on 10-19".
- **Shadow and active enable: PROPOSED.** The firmware runs no flow model today. The plan and its cost are below. Nothing was flashed or flown.

## How the camera got into the old target

**Firmware.** `Ext_Pos_Tick` (`TASK/StabilizerTask.c:961`) runs every tick. Mode 1 (`g_ext_pos_on` = 1) only scores the fixes. The other two modes apply them:
- **Mode 2.** `ext_pos_step` moves the target on each fix (line 983) and `ext_pos_spread` ramps `ano_of.earth_x/y` toward it on every tick (lines 990-994).
  - The EKF is not touched.
  - `earth` becomes the camera-aided track.
- **Mode 3.** `Ext_Pos_Ekf` (`TASK/StabilizerTask.c:933`) calls `EkfOf_UpdatePos` (`API/ekf_of.c:281`). That update moves the position states `x[0]`/`x[3]`, and through the covariance the velocities `x[1]`/`x[4]` and later innovations.
  - The same step is also added to `earth_x/y` (line 951).

**Old pipeline (target "earth").**
1. **Target:** the body-frame derivative of truth − earth. With fusion on, earth already follows the camera. The target was therefore the drift the camera left behind, not the drift a camera-off estimator has.
2. **Features:** `NN4_CH`, 17 channels. Four of them are EKF outputs: `s_ekf_of.x[1]`, `x[4]`, `innov_x` and `innov_y`. In mode 3 they carry the camera updates.
3. **Score:** the prediction was integrated onto `earth`. So even a zero prediction scored the camera-fused track.

The yaw sense was also picked from the EKF velocity, which mode 3 also bends.

## The fix

`nn4_data(name, target="rawof", features=NN4_RAW_CH)`, now the default:

| | Before (target "earth") | Now (target "rawof") |
|---|---|---|
| Target `Y` | d(truth − earth), body frame, at the window end | mean over the step of d(truth) in the body frame, minus the scaled raw OF over the same step |
| Raw OF scale | — | `NN4_OF_SCALE` = 0.01 × 1.094 m/s per count (`M_PER_CM` × `g_of_scale`). The bias trim `s_of_bias_x/y` is left to the model's intercept. |
| Features | `NN4_CH`: 17 channels, 170 features | `NN4_RAW_CH`: the 12 slot-1 channels (IMU, attitude, both flow sensors) + `ano_of.of_alt_cm`. 13 channels, 10 block means over 1 s = 130 features. |
| Yaw sense | earth velocity vs EKF velocity | body truth velocity vs raw OF (complex correlation, `corr` per flight) |
| Track | `earth` + ∫ prediction | `nn4_track`: raw OF + prediction, rotated to the truth4 frame and integrated. Before the first full window it is the raw-OF track (`head`). |

**Frames.**
- `rot_deg` comes from the truth4 earth→floor fit. It is one rotation that aligns the axes, not a position correction.
- Body = truth4 × `exp(-j·rot)` × `exp(j·sign·yaw)`.
- `back` is the inverse at each step time. It turns a body vector into the truth4 frame.

**`nn4` (LOFO) output.** The scored rows are:
- `oracle`: the true residual (pipeline check, should land near truth);
- `rawof`: zero prediction, the baseline;
- `ridge`;
- `mlp`;
- `earth`: the logged track. On a fused flight it is a camera reference, not a camera-off baseline.

`nn4_lofo.json` records `ext_pos_on` per flight (the highest `g_ext_pos_on` logged). The CLI prints it as `ext N`. `report4` has a "raw OF alone" bar.

**Kept for compatibility.**
- `NN4_CH` is unchanged. `ground_station/livewatch/campaign_capture.py` (the `nn4` log group) and `ground_station/analysis/nn_io.py` use it.
- `nn_io.seq` calls `nn4_data(..., target="earth")` with the EKF channels, so it still has the leak on fused flights (follow-up 1).

**Tests.** `ground_station/analysis/tests/test_nn4_data.py` has four tests on synthetic flights:
1. The rawof target equals the body residual the raw OF misses (within 3 mm/s). `back` undoes rot + yaw.
2. Two fused flights with the same raw OF but different camera-aided earth/EKF channels give identical `F`, `Y`, `of`, `back`, `head`. That is the leak check.
3. `head` is the raw-OF track. The oracle prediction rebuilds truth, and a zero prediction drifts.
4. The legacy target "earth" still gives the old residual.

## Evaluating on 10-19

Not run: this needs files that only exist on the lab PC.

**What 10-19 has.**
- The fixes were sent on every flight. `logs/video_truth/{mem1..mem6,z1,z2}-lab-*/fixes.csv` hold 458-1097 sent fixes per flight, at about 6.5 Hz.
- Whether the firmware applied them (`g_ext_pos_on` ≥ 2) is in the session telemetry only.
- The floor scan for the 10-19 phone pose is `logs/video_truth/floor-1019/aruco.npz`.

**Format gap.** The 10-19 flights were captured by the campaign runner, not stream_log. Each flight is a session `telemetry.csv` under `logs/campaigns/<campaign>_<stamp>/` on the lab PC (flight 01: `lab-m-memory-u100_20261019-151725`):
- long rows `received_ns,key,value`;
- the `nn4` group at 50 Hz, `camera_fusion` at 100 Hz.

truth4 (`r4_truth`), `fixes_clock` and `nn4_data` all go through `read_slots`. That reads `<stem>.slot<k>.csv` on the drone clock (`t_src_ms`). A session `telemetry.csv` differs in three ways:
- it has only the host clock;
- it has no `g_ext_pos.n_old` (`fixes_clock` counts `n_ok + n_rej + n_old`);
- it has no slot split.

So truth4 cannot be built from it as is. Two ways to close the gap (follow-up 2):
- a converter that writes slot CSVs from a session `telemetry.csv`;
- a `read_slots` branch built on `log_corpus._load_session` + `hold`, as `read_livewatch` already does for the run/overlay path, with `fixes_clock` matched on the host clock.

**Local steps once the gap is closed.** Run from the repo root on the lab PC:

```
python -m ground_station.analysis.video_truth truth4 <name> --stem <slot-csv stem> --floor logs/video_truth/floor-1019 --fixes --no-reflect
python -m ground_station.analysis.video_truth nn4
python -m ground_station.analysis.video_truth report4 -
```

Notes on these steps:
- `--no-reflect` is required: `nn4_data` rejects a reflected earth fit.
- Delete the old `logs/video_truth/nn4_lofo.json` and every `truth4_score_nn4.json` first. They came from the leaky pipeline.
- `nn4` with no names scores every truth4 flight, so the r4/r6/r7 flights and the 10-19 flights pool together.
- Read the `ext` column before reading any number.

**Gate before any enable.** On the 1 s-mean 60 s drift p90 (`drift60s_p90_cm`), leave-one-flight-out:
1. `mlp` (or `ridge`) beats `rawof` on most held-out flights and on the median over all flights.
2. On unfused flights (`ext` < 2), it also beats `earth`, the camera-off EKF it would replace or feed.
3. `oracle` stays within a few cm of truth on every flight. A flight where it does not has a sync or frame problem; drop it from the pool.

The 10-12 report's honest number was 47 cm held out against a 10 cm budget. A model that passes 1 and 2 but not the budget goes to shadow only.

## Enabling on the vehicle (PROPOSED)

The firmware runs no flow model today. The only learned weights on board are the MRAC RBF (`API/mrac_variant.h:28`).

| Model | Params | MAC / step | At 10 Hz |
|---|---|---|---|
| Ridge | 262 | 260 | 2.6 kMAC/s |
| MLP 130-32-32-2 tanh | 5314 | 5248 | 52 kMAC/s, 21 KB float32 |

**Shadow (first).**
1. Keep 10 block means over 1 s of the 13 `NN4_RAW_CH` channels, in the same order and units as the logs.
2. Run the model at 10 Hz.
3. Integrate raw OF + prediction into a separate track (`nn_x/nn_y`), and log it with the prediction in a new capture group.
4. Do not feed it into the EKF or any loop.

What shadow tests: the on-board features match the logged ones (float order, scaling, and the window edges at `ts`). A shadow flight with the camera on also gives truth for free (truth4) and adds to the pool.

**Active (only after the gate and a flown shadow A/B).**
1. Add the predicted body residual to `ofx/ofy` before `EkfOf_Update` (`TASK/StabilizerTask.c:846-874`), behind a runtime flag that defaults to off.
2. Clamp the correction (start at 5 cm/s) and slew-limit it, so a bad window cannot steer the vehicle.
3. Freeze it on the ground and below the landing hold band (`LAND_XY_HOLD_ALT`, 0.12 m in `API/land_profile.h`).
4. Fly a camera-off A/B (flag on/off, same preset). Score with truth4.

**Fused flights in the pool.** A flight with `g_ext_pos_on` ≥ 2 trains fine under the new target, because raw OF and truth are both camera-free on the vehicle side. But its `earth` row is not a camera-off baseline.

## Follow-ups

1. **`nn_io.seq` is still leaky** (`ground_station/analysis/nn_io.py`). It uses target "earth" with the EKF channels. Move it to the rawof target and `NN4_RAW_CH` before training any sequence model on fused flights.
2. **Session `telemetry.csv` → truth4 / nn4** (see "Format gap"). Needed before the 10-19 flights can be scored.
3. **Regenerate the stored scores.** `nn4_lofo.json` and the `truth4_score_nn4.json` files on the lab PC predate this fix.
