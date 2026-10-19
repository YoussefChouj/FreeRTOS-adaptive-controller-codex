# Flight scorecard: how to read it

`ground_station/analysis/scorecard.py` turns one labday flight folder into one card of numbers, rolls the day's cards
into a day report, and groups flights by controller configuration for the thesis comparison (baseline vs adaptive).
Every number is computed from the flight's logs. A value that cannot be computed shows as `-`, never a guess.
Thresholds, the pass mapping, SoC and Wh are PROPOSED and say so in every output.

**Real-log check still open.** The module is tested only on synthetic fixtures
(`ground_station/analysis/tests/test_scorecard.py`), because `logs/` is git-ignored and the cloud has no flight data.
Before trusting a table, run it locally on the real 10-18 and 10-19 days and compare a few cells by hand with
`debrief.txt` and the campaign summary:

    python -m ground_station.analysis.scorecard 2026-10-18
    python -m ground_station.analysis.scorecard 2026-10-19 --compare

## Where it runs

- **labday, after every flight.** `Day.score` in `ground_station/lab/labday.py` runs right after the
  `--- <id> done` line. It prints one line, then the report path:

      score mem1: pass pass (PROPOSED); hold cam rms 4.1 cm; hold EKF rms 2.3 cm; land offset 6 cm; aborts 0; ingest lag max 0.4 s; sag 0.62 V; 1.9 Wh (PROPOSED)

  (Illustrative numbers.) Each part appears only when its value exists; `aborts` is always printed. The day report
  is rebuilt from every flown flight that has a log folder, so a resumed day still lists every flight. If a flight
  has no folder the line says `no log folder, nothing to score`. If the scorer throws, the line says
  `scorecard failed (...)` and the day goes on.
- **By hand, for a whole day.** `python -m ground_station.analysis.scorecard <date>` scores every folder under
  `logs/flights/<date>/`. It matches each folder to the day file's entries by `.fly` name, in flight order (a
  re-fly shifts the later ids). Add `--compare` to also write `docs/lab/scorecard-compare.md` and `.csv` from
  every day that has a report.

## Outputs

| file | what |
|---|---|
| `docs/lab/days/<date>-report.md` | Flights table (one row per flight); pass checks clause by clause; sources (folder, session, camera source, pack, abort reasons); trend table |
| `docs/lab/days/<date>-report.json` | The day's rows. The trend and `--compare` read these, so a day counts once it has been scored. |
| `docs/lab/scorecard-compare.md` / `.csv` | One column per controller configuration: mean (sample SD, n >= 2) of every numeric metric, plus pass / fail / manual counts |

## Inputs

All of these come from the flight folder `logs/flights/<date>/<NN>_<campaign>/`:

- `debrief.txt` (written by `campaign_fly`): status, airborne s, aborts, landing offset, camera landing.
- `outputs.txt`, or the `outputs` line of the debrief: the campaign outputs dir, which holds `metrics.json` and
  `campaign.yaml`. `metrics.json` `flights[].recording` names the recording session, which holds `telemetry.csv`
  and `events.jsonl`.
- The pipeline log `<date>.jsonl` in the labday events dir. Its `go` row for this folder gives the pack and pid;
  its `link_lost` rows are matched to that pid.
- The 8081 service logs under `logs/service/`, for the `ingest behind` lines.
- The camera `fixes.csv` under `logs/video_truth/<id>-<fly>/`, when the camera ran.

Phases come from the telemetry `prim_state`: HOVER is the hold and TRAJ is the trajectory.

## Metrics

| column | how it is computed | source |
|---|---|---|
| status | First word of the debrief (`complete`, `ABORTED`, ...) | debrief |
| air s | Airborne seconds printed by the debrief | debrief |
| hold s | Length of the prim_state HOVER window (`campaign_outputs.flight_metrics`) | telemetry |
| hold rms cm (EKF) | RMS of the x/y distance from EKF position (`locx/locyPID.FB`) to target (`.Des`) over the HOVER rows. It measures how well the controller tracks its own estimate, not the true position. | telemetry |
| hold rms cm (cam) | The same RMS, but with camera truth instead of the EKF position | telemetry or fixes.csv |
| hold drift cm (cam) | Largest x/y distance of the camera position from its median during HOVER, i.e. how far the drone wandered, whatever the target | as above |
| traj rms cm (EKF / cam) | The two RMS columns above over the TRAJ rows (circle, figure-8, goto) | as above |
| cam-EKF rms cm | RMS of the fusion innovation (camera minus EKF) while airborne: how far the estimate sits from the truth | telemetry |
| takeoff osc cm | √(x² + y²) of the x and y oscillation RMS (error minus its 2 s running mean, `lab_flights.osc`) from 1 to 4 s after liftoff: the wobble after takeoff, not a steady offset | telemetry |
| touch err cm | x/y error at touchdown. Camera when available, else EKF; the Sources section says which (`lab_flights.land_metrics`). | telemetry, fixes.csv |
| touch vz cm/s | Fastest descent rate in the 0.5 s before touchdown (negative = down) | telemetry |
| land offset cm | Landing point vs takeoff point, as the debrief printed it | debrief |
| cam land cm | Landing point vs the pad, measured by the camera, as the debrief printed it | debrief |
| aborts | Number of `ABORTED` debrief lines; if there are none, the session's `abort` events. Reasons are in Sources. | debrief, events.jsonl |
| stalls | Number of `stream_stall` events in the session | events.jsonl |
| link lost | Pipeline `link_lost` rows of this fly run's pid | pipeline log |
| ingest lag max s | Worst `ingest_lag_s` of the 1 Hz `link` snapshots | events.jsonl |
| ingest dropped | Last minus first `ingest_dropped` of those snapshots. The bridge counter runs since 8081 started, so only the difference means anything. | events.jsonl |
| ingest behind | Number of 8081 `ingest behind` lines inside the session's wall-clock span. The worst frame age is kept in the card as `ingest_behind_max_ms`. | service log |
| V rest | Median voltage before liftoff (`real_voltage`, else `status.vbat`) | telemetry |
| sag V | V rest minus the lowest airborne voltage | telemetry |
| Wh (PROPOSED) | (SoC at rest − SoC after landing) × pack capacity × mean airborne V. SoC comes from the pack's curve in `ground_station/analysis/packs.yaml` (via `battery_model.PackRegistry`), and the pack comes from the pipeline `go` row. There is no current sensor, so this is an estimate. If no pack resolves, there is no Wh. | telemetry, packs.yaml |
| pass (PROPOSED) | See below | day yaml + card |

**Camera truth.** The preferred source is the telemetry fusion innovation `g_ext_pos.e_x/e_y` (cm, camera minus EKF).
It gives camera = EKF + innovation on the same clock, with no sync step; rows flagged stale are dropped. If the
flight has no innovation, `fixes.csv` is aligned to the EKF x track by cross-correlation
(`video_truth.sync_xcorr`). It is used only when the correlation reaches `CAM_SYNC_R_MIN` = 0.8 (PROPOSED). Otherwise
every cam column is `-` and Sources gives the r it reached. This sync path has been tested on synthetic data only.

## Pass verdict (PROPOSED)

The day file's `pass` text is split on `;`. Each clause is mapped by keyword:

| clause mentions | check |
|---|---|
| `figure-8` or `complete` | status `complete`, no aborts, and some TRAJ time |
| `pad` and `land` | land offset at most `PASS_LAND_CM` = 15 cm (PROPOSED). With no offset in the debrief: manual. |
| `hold` or `hover` | some HOVER time and no aborts |
| anything else | manual (not checked from the logs) |

The verdict is `fail` if any clause fails; `pass` if all clauses pass; `pass+manual` if some pass and the rest are
manual; `manual` if nothing could be checked. The day file's pass marks are themselves PROPOSED, so the verdict
means "matches the proposed criteria", not "met a measured spec".

## Controller configuration (thesis comparison)

Each flight is keyed by `controller / gains <preset> / mrac_injection <v>`:

- `controller` comes from `campaign.yaml`.
- `gains` is the `.fly` gains preset (`campaign_fly.extract_fly_directives`).
- `mrac_injection` is the last `command` event 15/10 written during the session. If there is none, the
  `campaign.yaml` takeoff step; if there is none, the `.fly` text.

`mrac_injection` 0 is the **baseline** arm and > 0 is the **adaptive** arm. If it is unknown, the arm is `?`.
Baseline columns come first. For the "controller-agnostic" claim, compare arms within the same controller and
gains preset, and compare the adaptive gain across controllers.

## Why not the flightlab battery plugin

`ground_station/analysis/flightlab/plugins/battery.py` needs the flightlab `Log`, its segments and its config. A labday
flight folder has none of these. The scorecard therefore reads the voltage series directly and reuses only
`battery_model.PackRegistry` for SoC. Tracking, touchdown and the airborne window reuse `lab_flights`; hold time
reuses `campaign_outputs`.
