# Lab commands: the ground truth

Every command used at the lab, in the order a lab day uses them. Each one works when typed by hand, and an agent
looking for a lab command reads this file first instead of searching the code.

**Keep it true.** A commit that adds or changes a lab command, flag, key or file also updates this file (rule in
`AGENTS.md`, Conventions). `ground_station/lab/tests/test_commands_doc.py` checks every command here against the
code: each `python -m` module and its `--flags`, each `.\X` launcher and each `cam`/`camera_truth.sh` subcommand.

**Which shell.** Use PowerShell or cmd in the repo root for `.\labday`, `.\fly`, `.\cam` and `.\lab`. Git Bash (also
the Bash tool in Claude Code) runs `bash ground_station/lab/camera_truth.sh ...`. In PowerShell, `bash` starts WSL,
which has no adb, scrcpy or Windows python. Never use it for the camera. `.\cam` runs Git Bash for you.

## Quick map

| I want to | Command (PowerShell, repo root) |
|---|---|
| Fly the whole day | `.\labday 2026-10-19` |
| See what is flown / the plan | `.\labday 2026-10-19 --list` |
| Gates off (battery + firmware trips + RC only) | `.\labday 2026-10-19 --no-gates` or `.\fly <card> --once --no-gates` (restart 8081 after the 10-24 commit) |
| Scan the floor (new phone placement, drone out of view) | `.\cam floor floor-1019` |
| Start the camera fusion for one flight | `.\cam fusion f1` (mode 3, `--yaw-deg 94`) |
| Same with another mode | `.\cam fusion f1 2 --yaw-deg 94` |
| Fly one flight | `.\fly lab_m_memory_u100 --once` |
| Stop the camera after landing | `.\cam stop` (newest running session) or `.\cam stop f1` |
| Check rows, video, fixes | `.\cam check f1` |
| End the live VOFA+/PlotJuggler stream | `.\cam end` |
| Start the ground service by hand | `python -m ground_station.service --port 8081` |
| Flash the firmware | `python -m ground_station.flashtool.rebuild_and_flash --yes` |
| Every flight ever flown, one table per day | `python -m ground_station.lab.flight_index` -> `logs/flights/INDEX.md` + `index.csv` (labday refreshes it after each scored flight; `--grep lqr` prints matching rows) |
| Drift NN live for one flight | `.fly` line `set g_drift_nn_live=1 g_drift_nn_clamp_mps=0.05` (section 4.1) |

## 1. A lab day: `labday`

**2026-10-28 deck (current):** flash first (`python -m ground_station.flashtool.rebuild_and_flash --yes`, COM6, then
`python -m ground_station.livewatch verify`), restart 8081 `--lab`, then `.\labday 2026-10-28`. Order: rate step
`rate32d169y104` (terminal y/n), smoke, MRAC shadow/live pairs (angle, vel, pos), PID and LQR baselines, sysid refly.
`.\labday 2026-10-28 --list` shows it; why in `docs/lab/2026-10-28-verdicts.md`.

For the 2026-10-27 deck, restart the labday process and resume with
`.\labday 2026-10-27 --from rate-sysid-roll-wide`. This repeats roll, pitch, yaw,
then the rate tuning gate and verification hover, even if those cards were already flown.
Each SysID card specifies
`camera_mode: 0`: the camera/tracker still records, and sends mode 0 (corrections off).
Every other card inherits the day's mode 3, including verification hovers. A card may
override `camera_mode` with integer 0, 1 (shadow), 2 (offset corrections), or 3 (in-EKF).
Mode 2 still corrects position; use mode 0 to isolate identification from camera feedback.
`--no-camera` skips the tracker; it is not a substitute for explicitly sending mode 0.
This change needs a new labday process, with no firmware flash or service restart.

Standalone altitude diagnosis (PROPOSED 0.8 m / 30 s hover, nominal PID, no gain sweep):

```powershell
.\fly lab_z_cf_hover --once --gains logs/lab/2026-10-27/2026-10-27-gains.json
```

This requests PROPOSED 200 Hz for CF height/velocity, acceleration, bias, residual and correction count;
range arrivals/rejections; both Z loops' P/I/D/output terms; collective and the core motor,
attitude, reference/feedback and battery signals. Actual gains/limits, body acceleration
and effective estimator flags share the direct slot. The new-image card omits camera_fusion
telemetry to fit the budget; the old-image compatibility card retains it. A lower-rate
CF replay plan is refused. The primary card requires the reviewed root-cause image;
before deployment, substitute lab_z_cf_hover_legacy in all old-image commands. Results go to
`logs/flights/<date>/<NN>_z-cf-hover/` and the referenced
`logs/sessions/` recording. This standalone command does not launch camera fusion; keep the
existing calibrated camera session running if using it. RC arm and normal abort gates apply.
Keep the same gains for this diagnostic; a hover identifies symptoms, not full stability margins.

The CF diagnostic uses **one direct subscription, slot 0, divider 1** including `dt_c`,
`y_corr`, latched `mode`, range inputs and `xTickCount`. Slots 1–3 are stopped first;
there is no parallel dashboard stream. Core/arm/status fields are in the same CF frame.
Formerly slow gains/takeoff diagnostics are promoted to the same rate. Old-image DWARF
sizing resolves 102 scalars, 48 ranges and 325 payload bytes: (325 + 12) * 200 =
67,400 B/s. The extended image with camera would need 70,600 B/s and is rejected.
Omitting camera gives 100 scalars and 317 payload bytes, calculated 65,800 B/s
against the 70,041.6 B/s planning cap. The host calculator accepts a PROPOSED legal
placement for new fields (47 ranges, 386 request bytes); actual new-AXF placement,
range fit and transport cadence remain unverified. This is calculated sizing, not
measured throughput. Slots 1-3 add no load because they are stopped.
The live typed preview must resolve every field at the requested clock and fit the budget.
CF diagnostics bypass the ordinary lab 25 Hz cap. Before recording a flight, slot 0
must deliver PROPOSED >=190 Hz with zero sequence drops over a PROPOSED 2 s window;
otherwise takeoff is refused and the normal arm/status frame is restored. This rate gate does not
replace checking every saved firmware tick for replay.

First verify the same slot layout on the ground, with the drone disarmed. Wait PROPOSED
10 seconds, then press Enter to stop. This command checks for consecutive 5 ms snapshots
and prints `CF capture cadence PASS` only when no prediction snapshots are missing:

```powershell
.\fly lab_z_cf_hover --record-only cf-ground-rate --rate 200
```

This requires confirmed disarmed telemetry, an idle runner and no existing recording.
It uses the same single-slot frame as the flight, writes no PID gains and sends no arm/takeoff
commands. It restores the normal arm/status frame on exit, including failure. A pass requires
PROPOSED >=8 seconds of saved, consecutive 5 ms snapshots in slot 0. A cadence failure
requires fixing capture before another flight; do not compensate by changing the filter or gains.
The current resumption checklist is `docs/lab/cf-resume-2026-10-27.md`.

After recording, choose the hover interval using firmware seconds after the first complete
fast-slot frame (the bounds below are PROPOSED examples; select the actual plateau):

```powershell
python -m ground_station.lab.cf_vz_check logs/sessions/SESSION_ID 7.5 36.5
```

This writes `cf_vz_check.json` and `cf_vz_check.png` into that session. It reports H1/H2/geometric
gain, phase and coherence for velocity/range derivative, acceleration/range second derivative,
and height/range; broadband and individual frequency bands, hover range standard deviation,
correction/arrival rates, and both-mode offline replay correlation. The range reference shares
the sensor with the filter; it is not independent truth. Replay requires every prediction tick,
an initialized armed segment, and all new fast fields. Missing ticks, hidden corrections,
resets or acceleration clipping prevent a faithful-replay claim. The historical slow capture
cannot supply those missing inputs. `estimator is still not understood` means the replay gate
has not passed; it does not claim a successful estimator model.

Offline four-recording Z root-cause analysis (reads the source logs without modifying them):

```powershell
python tools/analyze_z_cf_b1.py --logs-root D:/Projects/UAV_lab/FreeRTOS-adaptive-controller-codex/logs --output-dir docs/lab/assets/z-cf-rootcause
```

Produces measured spectra, relative lags, cadence, bias/throttle correlation and a figure.
The short 25 Hz records cannot identify true sensor latency or hidden correction steps;
search-boundary spectral peaks are unresolved slow variation, not precise periods.

The analysis JSON also reports ToF-to-height and d(ToF)/dt-to-velocity lag
(positive means CF follows ToF; PROPOSED 0.1-2 Hz zero-phase band, +/-0.5 s
search), dominant period, optional coherent bias/throttle correlation, and observed
correction residuals. Raw of_alt_cm/100 is preferred, with of2_h fallback. At
25 Hz, inter-frame differences cannot isolate 5 ms corrections; contiguous 200 Hz
snapshots are required. Missing replay diagnostics yield unavailable metrics;
large gaps are rejected. Effective alt_cf.delay_ticks or alt_cf.spread_mode nonzero
disables baseline replay certification. Pending requests are only a fallback;
unlogged effective modes prevent certification even when the baseline traces match. All-tick residuals
include smoothing between arrivals; arrival-tick residuals are reported separately.

Offline vertical closed-loop simulation (all physical parameters are PROPOSED):

```powershell
python -m ground_station.lab.z_cf_sim --output docs/lab/data/z-cf-rootcause-A5
```

Writes estimator/delay/motor/PID sweeps, cadence sensitivity, assumptions and test evidence.
The model uses the actual 100 Hz altitude / 200 Hz climb PID schedule. Its averaged
sample/hold margins and assumed thrust slope are simulation evidence only; hover
throttle alone cannot identify mass or thrust response.

Motors-off estimator diagnostic: g_alt_cf_disarmed_test is a runtime boolean,
default 0. While disarmed, 1 runs CF predict/correct for the hand test and keeps
its outputs isolated from Z controller feedback. It clears on arm; diagnostic
toggles and the arm transition reset estimator history/bias. The disarmed diagnostic
uses the selected CF rate mode. Firmware lacking this flag cannot perform this test.

Root-cause runtime variable IDs are permanent and all reject either armed-state source:

| ID | Variable | Range/default | Use |
|---|---|---|---|
| 60 | g_alt_tof_sample_mode | 0..1 / 1 | 0 old tick median; 1 newest accepted frame |
| 61 | g_alt_tof_delay_ticks | 0..31 / 0 | 5 ms ticks; identify delay before setting |
| 62 | g_alt_cf_spread_mode | 0..1 / 0 | experimental correction spreading |
| 63 | g_alt_cf_disarmed_test | 0..1 / 0 | motors-off diagnostic; clears on arm |

Configure with the diagnostic off, then enable the hand test while disarmed.
Record effective alt_cf.sample_mode, alt_cf.delay_ticks and alt_cf.spread_mode;
pending globals are not evidence of active settings. The old-image compatibility
card has no new flags, body-acceleration fields or disarmed hand-test capability:

```powershell
.\fly lab_z_cf_hover_legacy --record-only cf-ground-rate-old --rate 200
.\fly lab_z_cf_hover_legacy --once --gains logs/lab/2026-10-27/2026-10-27-cf-legacy.json
```

Height-filter ladder, three flights (2026-10-27, firmware d051deca). Same 0.4-1.2 m ladder each time; only
VAR 58 changes: 0 = ToF difference (FD), 1 = tuned CF (wn 10), 3 = KF. 2 is not used (rejected). Each gains
file is written disarmed after the terminal y/n. Then resume the day deck:

```powershell
.\fly lab_z_filter_ladder --once --gains logs/lab/2026-10-27/2026-10-27-zladder-fd.json
.\fly lab_z_filter_ladder --once --gains logs/lab/2026-10-27/2026-10-27-zladder-cf.json
.\fly lab_z_filter_ladder --once --gains logs/lab/2026-10-27/2026-10-27-zladder-kf.json
.\labday 2026-10-27 --from smoke-hover
```

Result (flown 2026-10-27, flights 09-11): KF won (hold rms 4.2 cm vs FD 5.2 / CF 5.3, drop recovery 0.78 s vs CF
1.77 s), plot `logs/flights/2026-10-27/11_z-filter-ladder/z_filter_flights.png`. **KF is the firmware default
since firmware 59667f4b** (`ALT_VZ_MODE_DEFAULT`, `TASK/StabilizerTask.h`). To fly another mode, write VAR 58 = 0 or 1
disarmed (the zladder fd/cf gains files above); a reflash or reboot brings back KF.

CF correction-rate ABBA experiment (PROPOSED): after the reviewed firmware is flashed and
verified, copy the two preset sources next to the saved day gains. The copies preserve the
saved attitude gains and explicitly set the Z position PID to 0.7/0.005/0.1 (Ki back to the
original 0.005 on 2026-10-27, operator) and velocity PID to 400/0.435/0. The variants differ only in `g_alt_cf_rate_mode` (legacy versus
rate-scaled); both select CF velocity feedback. Source PID defaults are unchanged.

```powershell
New-Item -ItemType Directory -Force logs/lab/2026-10-27 | Out-Null
Copy-Item docs/lab/gains/2026-10-27-cf-legacy.json logs/lab/2026-10-27/
Copy-Item docs/lab/gains/2026-10-27-cf-rate-scaled.json logs/lab/2026-10-27/
# cf-legacy-hover (A)
.\fly lab_z_cf_hover --once --gains logs/lab/2026-10-27/2026-10-27-cf-legacy.json
# cf-rate-scaled-hover (B)
.\fly lab_z_cf_hover --once --gains logs/lab/2026-10-27/2026-10-27-cf-rate-scaled.json
# cf-rate-scaled-hover-repeat (B)
.\fly lab_z_cf_hover --once --gains logs/lab/2026-10-27/2026-10-27-cf-rate-scaled.json
# cf-legacy-hover-repeat (A)
.\fly lab_z_cf_hover --once --gains logs/lab/2026-10-27/2026-10-27-cf-legacy.json
```

**ToF sample-mode A/B (needs main 19dde9dd or later flashed), one command, no manual writes:**

```powershell
.\cfab --dry-run   # prints the 4 plans; nothing flies
.\cfab             # flights 1-4: sample mode 0, 1, 1, 0; RC arm starts each flight
```

`cfab.cmd` runs `.\fly lab_z_cf_hover --once` four times. Each flight loads
`logs/lab/2026-10-27/2026-10-27-cf-ki005-mode0.json` or `...-mode1.json`. The two files are
identical (Ki 0.005, CF velocity, rate mode 0, delay 0, spread 0, hand test 0) except row 60
`g_alt_tof_sample_mode` (0 = old tick median, 1 = fixed per-sample median). A failed flight stops
the chain.

These descriptive names label the ABBA sequence; standalone recordings retain campaign
name `z-cf-hover` with unique session timestamps. Record which session belongs to each label.
Disarm between flights: mode writes latch on the next arm edge. Keep the same camera setup
and use the same hover-window selection. Run `cf_vz_check` on the first fast-slot capture
before continuing: PROPOSED minimum matching-mode replay correlation is 0.95, with no
hidden-input issues. Without this the report must say the estimator is still not understood.
A first operator capture is needed to validate replay; it cannot be validated on a capture
that does not yet exist. Existing RC authority and flight gates remain active.

Decision rule (all thresholds PROPOSED): each of both rate-scaled flights must beat each
of both legacy flights: velocity/range-derivative phase lag at 0.5–0.9 Hz shrinks by at least
15 degrees, geometric gain is at least 0.75, and hover `of2_h` standard deviation is no worse.
Otherwise the rate-scaling hypothesis is falsified; investigate range latency using the
fast replay rather than tuning PID gains. The experiment is operator-run, not added to the
automatic day deck; the same standalone commands are recorded in the day YAML comments.

One-change follow-up after the diagnostic with measured Z position gains 0.7/0.001/0.1:

```powershell
.\fly lab_z_cf_hover --once --gains docs/lab/gains/z_cf_soft_p.json
.\fly --gains docs/lab/gains/z_cf_soft_p_rollback.json
```

The trial changes only position Kp to 0.45 (PROPOSED); Ki, Kd, velocity and attitude gains
retain their current RAM values. Use in the same powered session as the baseline hover;
after a power cycle, first restore the saved day gains. Expected tradeoff: slower height
recovery. No CF coefficients change, and this does not resolve possible velocity-estimator
bias. Compare hover RMS and peak error; land if worse. The second command restores the
measured Kp=0.7 while disarmed, without flying. These presets do not update the day file.

```powershell
.\labday 2026-10-19                 # dry run each flight once, then fly them in order (8081 + camera started for you)
.\labday 2026-10-19 --list          # plan + what is flown, then exit
.\labday 2026-10-19 --no-gates      # safety monitor gates off; low battery, firmware trips, RC kill/takeover stay
.\labday 2026-10-19 --sheet         # the run sheet as markdown, then exit
.\labday 2026-10-19 --from mem2     # start at this flight id
.\labday 2026-10-19 --pack 2        # the pack type in the drone now (1 or 2), once after a swap to the other type
.\labday 2026-10-19 --dry-run       # only the ground dry runs of the flights left
.\labday 2026-10-19 --no-camera     # fly without the camera fusion
.\labday 2026-10-19 --no-plots      # do not generate background plots after each flight
.\labday 2026-10-19 --floor floor-1019   # floor scan folder under logs/video_truth (default: the day file's)
.\labday 2026-10-19 --cooldown 120  # rest between a landing and the next flight, s
```

- Day file: `docs/lab/days/<date>.yaml`. Its `camera:` block sets the floor scan, the fusion mode and the tracker
  args for every flight (section 3).
- Prompts: before a flight, Enter flies it (then arm by RC), `s` skips it, `f` rescans the floor, `q` quits. After a
  bad end, `r` flies it again, Enter goes on, `q` quits; after an AUTO STOP, `f` rescans the floor and flies it again.
  During the cooldown, Enter skips the rest.
- The dry run is cached per flight (key = HEAD + `git diff HEAD -- ground_station`). It runs again only when the
  code changes.
- With the camera, every launch first scans the floor (drone out of view, Enter) or keeps the current scan (`k`).
- Before GO, `fly` rechecks fresh height telemetry after the RC-arm countdown. Missing/stale height or pad height
  outside +/-0.2 m (PROPOSED) refuses GO; disarm by RC and fix the range/firmware before retrying. `--no-gates`
  does not bypass this check. Camera fixes are withheld while startup scenery is learned or height is outside the
  calibrated floor-to-camera interval; `HEIGHT INVALID` identifies the bad input rather than drawing false drift.
  A scan counts only when its folder holds the scenery mask and the landing pad is seen in at least half the frames.
  New scans are `floor-<date>[-N]`.
- Battery: before each flight, the resting volts and the predicted pack % after the flight against the 30% gate;
  after each flight, the landing volts and the sag; the cooldown line shows live volts.
- A failed gate says SWAP THE PACK: power off, swap, power on. labday waits for the link to drop and come back,
  asks the pack type and goes on in the same run (8081 keeps running, the dry-run cache stays valid).

### 1.1 Terminal tuning gates

A flight in `docs/lab/days/2026-10-27.yaml` with a `tune:` block opens an interactive **terminal tuning gate** in `.\labday` after landing (drone disarmed):

- `tune: {kind: pid, loop: rate|angle|vel|pos, axes: [...], from: [...]}` — runs `python -m ground_station.autotune.cli` on the `from` flight logs and shows old -> new `Kp`/`Ki`/`Kd`, crossover `wc`, `PM`, `GM`, `max|S|`, fit residual, coherence coverage, and outer/inner bandwidth separation (`>= 3.0x`).
- `tune: {kind: xy_model, loop: pos, axes: [x, y], from: [...]}` — synthesizes model-based LQR/MPC/ADRC gains (`VAR 75..83`) and reference models (`logs/ref_fit/<kind>.json`) from the identified cascade.
- `tune: {kind: adapt, layer: <layer>, mode: shadow|live, baseline: <id>}` — runs `ground_station/analysis/adapt_gate.py` and shows the verdict (`OK`, `TOO SLOW` -> `gamma x2`, `TOO FAST` -> `gamma x0.5`, or `DISABLE` when live tracking error exceeds the shadow baseline) plus the single proposed knob change.

| Key | Where | Does |
|---|---|---|
| `y` | terminal tuning gate | write proposed gains now while disarmed (saves pre-gate snapshot, updates day gains file, writes via 8081) |
| `n` | terminal tuning gate | keep old gains and record the declined decision in history |
| `r` | terminal tuning gate | re-fly the `from` flight(s) before deciding |
| `q` | terminal tuning gate | stop the day (resumable with the same `.\labday` command) |
| `v` | post-flight prompt | revert gains (`xy`, `pid`, `all`, or `<gate>`) from `snapshots` while disarmed |
| `u` | failed verify hover | undo the preceding gate's gain write immediately and restore the pre-gate snapshot |

### 1.2 Day gains file

When a day file sets `gains: <preset>` (e.g. `gains: rate35y104` in `docs/lab/days/2026-10-27.yaml`), `.\labday` maintains the persistent day gains file (`logs/lab/<date>/gains.yaml` and `logs/lab/<date>/<date>-gains.json`) and passes it via `--gains` to every flight:

- Keys in the day gains file: `base` (initial preset name), `values` (active PID / `VAR` / MRAC map written before each flight), `staged` (candidate changes awaiting gate approval), `snapshots` (pre-gate value snapshots keyed by gate id and `xy`/`pid`/`all` scopes), and `history` (audit log of `t`, `gate`, `var`, `old`, `new`, `decision`, `why`).
- Every flight also writes a per-flight `<flight folder>/gains.json` snapshot recording the exact active gains, source file/preset, and gate history at takeoff.
- Rollback: press `v` at a post-flight prompt, press `u` after a failed verify hover, or invoke `.\labday <date>` with `--revert xy|pid|all|<gate>` to restore a saved snapshot from `snapshots` while disarmed.

Temporary CF/Z smoke-hover candidate: `docs/lab/gains/z_cf_soft_i.json` changes only
`Z_posPID.Ki` from measured 0.005 to 0.001 (PROPOSED), reducing height-integral carry.
The climb-rate gains and CF coefficients stay unchanged; offset recovery may be slower.
The candidate is RAM only and needs a repeated smoke-hover before continuing the deck.
When applied to the day gains, save the prior value under gate `z-cf-soft-i` so every
subsequent flight records the candidate and the existing revert command can restore it:

```powershell
.\labday 2026-10-27 --from smoke-hover
.\labday 2026-10-27 --revert z-cf-soft-i
```

The standalone rollback mapping is `docs/lab/gains/z_cf_soft_i_rollback.json`.
The height-only preset does not contain the day's attitude gains; keep the merged day
gains file for the campaign. A power cycle alone does not remove the day-file override.

### 1.3 Plots after each flight

After each flight lands, `.\labday` launches a background plot worker into `<flight folder>/plots/` (`PLOT_TIMEOUT_S = 120` s PROPOSED) without delaying the next flight prompt:

- Prints `plots: <absolute path to <flight folder>/plots>` right after the post-flight battery line.
- Generates `tracking.png`, `analysis.png`, `autotune_<loop>_<axis>.png` (SysID flights), `adapt_gate.png` (adaptation flights), and `<flight folder>/plots/index.md` (one line per PNG with a one-line caption).
- Prints `plots ready: <n> files` when finished, or `plots failed: <reason>` if the worker exits non-zero or times out (never stops the day).
- Turn plotting off with `.\labday 2026-10-19 --no-plots` or by setting `plots: false` in the day file header (`docs/lab/days/<date>.yaml`).

## 2. Video tracking pipeline (camera fusion)

The chain: phone camera, then scrcpy (window `r5cam`, recorded to `cam.mkv`, moved off screen), then
`live_tracker` (finds the drone, makes fixes and shows the tracker window), then UDP 47800, then 8081 relays the
fixes to the drone (CMD 0x2C), then the drone's EKF. The phone is camera id 4, zoom 0.6, 1080p30.

### 2.1 Floor scan: after the phone moves, and once per lab day

Take the drone off the pad and out of view, with the pad marker (ArUco id0) fully visible.

```powershell
.\cam floor floor-1019                                   # 10 s video + ArUco scan into logs/video_truth/floor-1019
```

- It passes when it prints `floor OK: pad marker id0 in N of M frames` (it needs at least half of the frames), then
  the cached coral scenery mask, `coral scenery (pad clear) cached: N px`.
- `floor NOT OK`: clear the view of the pad marker and run it again. An older `cam.mkv` of the same name is kept as
  `cam.<HHMMSS>.mkv.bak`.
- A longer scan: `$env:FLOOR_S=20` before the command, or `FLOOR_S=20 bash ground_station/lab/camera_truth.sh
  floor floor-1019`.
- Re-check a scan by hand: `python -m ground_station.analysis.video_truth aruco logs/video_truth/floor-1019/cam.mkv --out logs/video_truth/floor-1019/aruco.npz --step 5`

### 2.2 One flight by hand: camera, fly, stop

```powershell
.\cam fusion f1                       # default: mode 3 (in-EKF) --yaw-deg 94, floor = newest logs/video_truth/floor-*
.\fly lab_m_memory_u100 --once        # arm by RC when it asks
.\cam stop                            # after landing + disarm: ends the tracker, the recording and the video
                                      # of the newest running session (`.\cam stop f1` names one)
```

- `.\cam fusion <name> <mode> [tracker args]` sets the mode and the args yourself. Any extra arg goes to
  `live_tracker`.
- Git Bash form: `CT_FLOOR=logs/video_truth/floor-1019 bash ground_station/lab/camera_truth.sh fusion f1 3 --yaw-deg 94`
- With 8081 up, 8081 owns the link, relays the fixes and logs, and the tracker reads the height from it
  (`--tel-url`). With 8081 down, `stream_log` owns the link (`--relay-port 47800`) and the tracker tails
  `logs/<name>.slot1.csv`.
- `fusion` first closes any leftover tracker, camera loop or live stream, and keeps a re-flown name's last run in
  `logs/video_truth/<name>/run-HHMMSS/`.
- Healthy start: `.\cam check f1` shows `window 'r5cam' ready 1920x1080` and a `fixes N` count that keeps growing.

### 2.3 Tracker window keys

| Key | Does |
|---|---|
| `r` | recording of the tracker window on / paused (`tracker.mp4`; it already records from the start) |
| `a` | set the anchor at the drone's current spot (drift pass circle, `--pass-cm`, default 20) |
| `c` | clear the trails |
| `v` | next 3D view |
| `z` | zoom picture-in-picture on / off |
| `f` | full screen on / off |

Before every flight, the dot should sit on the parked drone body. If it does not, land, don't fly, and rescan the
floor (2.1).

### 2.4 Other camera subcommands

```powershell
.\cam check f1        # rows per slot, video size, scrcpy restarts, tracker ready line, fix count
.\cam end             # end the live VOFA+/PlotJuggler stream (before starting 8081 by hand)
.\cam video f1        # live window + recording only (workflows A/B/C keep their own log)
.\cam start f1        # camera-truth recording session: 8081 + preset slots + stream log + video (drone DISARMED,
                      # preset and name confirmed by the operator first); 3rd arg = preset, default drift_rc
```

### 2.5 Environment knobs (set before `.\cam` / `camera_truth.sh`)

| Variable | Default | Does |
|---|---|---|
| `CT_FLOOR` | `.\cam`: newest `logs/video_truth/floor-*`; script: `logs/video_truth/r5_marks_1` | floor scan the tracker uses |
| `DELAY_MS` | 200 | camera delay sent to the firmware for back-dating (measured lag 0.21 s, 10-13) |
| `CT_WIN` / `CT_SIZE` | off screen, borderless / 1920x1080 | scrcpy window; `CT_WIN=` `CT_SIZE=1194x672` shows it on screen |
| `VOFA` | `127.0.0.1:1347` | flight view to VOFA+ (only when 8081 is down); `VOFA=` turns it off |
| `PLOT` | off | PlotJuggler stream, e.g. `127.0.0.1:9870` (only when 8081 is down) |
| `FLOOR_S` | 10 | floor scan video length, s |

### 2.6 Files of one flight: `logs/video_truth/<name>/`

`cam*.mkv` (camera video), `fixes.csv` (every fix), `tracker.mp4` (tracker window video), `tracker.out` and
`stream_log.out` (logs), `scrcpy.log`, `fusion` (holds the mode), `stop` (stop file), `run-HHMMSS/` (earlier runs).
Archived videos are on Drive: see `docs/agent/video-archive.md` before you call a missing `cam.mkv` lost.

### 2.7 The tracker on its own (dry run, replay, fit)

```powershell
python -m ground_station.analysis.live_tracker --source logs/video_truth/f1/cam.mkv --cam docs/video-truth/cam_r4_lenswave_2026-10-11.json --floor logs/video_truth/floor-1019 --detector coral --gate --height-m 0.5 --out logs/f1_dry_fixes.csv --show
python -m ground_station.analysis.live_tracker replay r5_marks_1 --mode 3
python -m ground_station.analysis.live_tracker fit logs/r5_shadow_3 --auto
python -m ground_station.analysis.camera_delay logs/campaigns/<lab-x-delay session> --flight xy-camera-delay-mode2
```

- `camera_delay` (after the xy delay flight, `lab_x_delay`): lines up the camera's xy velocity against the drone's own
  and prints the camera lag next to the `--delay-ms` setting. The lag leaves out the telemetry link's one-way latency.
  It says re-set when the gap passes 20 ms (PROPOSED, `--tol-ms`).

- Without `--send`, the tracker sends nothing (a dry run). `--source` takes a video file, a capture index or
  `window:<title>`.
- `replay` re-runs the online tracker and the fusion on recorded videos.
- `fit` fits yaw, scale and offset of the camera path against the EKF path.

### 2.8 Camera truth of a campaign flight (truth4, nn4)

A campaign (`.\fly`) logs to `logs/campaigns/<campaign>_<stamp>/telemetry.csv`, not to stream_log slot CSVs.
`--stem` takes that dir (or the csv) directly; there is no conversion step.

```powershell
python -m ground_station.analysis.video_truth truth4 <name> --stem logs/campaigns/<campaign>_<stamp> --floor logs/video_truth/floor-1019 --fixes --no-reflect
python -m ground_station.analysis.video_truth nn4
python -m ground_station.analysis.video_truth report4 -
```

- `<name>` is the video dir under `logs/video_truth/`; `--fixes` syncs on its `fixes.csv`.
- In `truth4.json`, check `lock_shift`, `n_recv` and `lost_end` under `fixes`. The counter sync takes the first
  logged fix count as zero, i.e. it assumes the tracker sent nothing before the campaign recording began. Fixes
  sent earlier (`.\cam fusion` running before `.\fly`) shift the clock by whole fix periods; compare `offset_s`
  (fixes) with `offset_takeoff_s` (lift-off) in `truth4.json` before using the flight: a gap of seconds means one
  of the two anchors slipped.
- The session must carry the `nn4` capture group for `nn4`; the drift NN doc is
  `docs/lab/drift-nn-leak-fix-2026-10-19.md`.

## 3. Fusion modes

The tracker puts the mode into its frames (`--mode`), and the drone applies it.

| Mode | Name | What the drone does with the camera fixes |
|---|---|---|
| 0 | off | ignores them |
| 1 | shadow | logs them only, so camera and EKF can be compared without touching the control |
| 2 | offset | corrects the EKF position with a slowly learned offset, outside the EKF |
| 3 | in-EKF | fuses each fix into the EKF as a position measurement (the 10-19 day mode) |

How to set it:

| Path | Where the mode goes |
|---|---|
| labday | day file, `camera: {mode: 3, args: [--yaw-deg, "94"], floor: floor-1019}`; `--no-camera` = no fusion |
| by hand | `.\cam fusion <name> <mode> [args]` (no mode = 3 `--yaw-deg 94`) |
| tracker alone | `python -m ground_station.analysis.live_tracker ... --send udp:47800 --mode 2 --delay-ms 200` |

Alignment (camera to earth yaw):
- `--yaw-deg 94` fixes it (the 10-15 to 10-19 placement).
- With no `--yaw-deg` and no `--align`, modes 2 and 3 get `--align auto`, which locks the yaw after an 80 cm camera
  path (`--align-span-cm`).

Frame grab (2026-10-22): a live source (window, webcam) is grabbed on its own thread, so the grab overlaps detection
and the preview; each fix keeps the clock stamp of its own grab. `--serial-grab` puts the grab back in the loop (A/B
only). The tracker.out result dict shows `frames`, `grabs`, `read_ms` (threaded: the wait), `detect_ms`, `show_ms`.
Camera delay (`--delay-ms`, the screen-flash test). The value is sent to the firmware (0xFE frame) to back-date each
fix; the tracker itself does not use it. Lay the laptop under the phone camera, screen up, start scrcpy (`r5cam`, the
fusion settings), then run (the screen flashes black/white fullscreen for ~70 s; Esc stops):

```powershell
python -X utf8 -m ground_station.lab.camlat 60 r5cam logs/camlat/<date>
```

It prints `LATENCY photon->frame read: ... median N ms` and saves `camlat.png` (drawn vs seen, histogram, ROI).
`--delay-ms` = that median + the tracker's read->send time (median of `t_send - t_cap` in a recent `fixes.csv`).
The link from the laptop to the firmware is not in either number. Measured 2026-10-26 after the 10-22 speed-up:
266 ms photon->read (n 60/60, std 24; was 328 on 10-21) + 31 ms read->send = **297 ms** (10-27 deck).
Re-measure when the phone, zoom, scrcpy size or tracker read path changes.

## 4. One flight: `fly`

```powershell
.\fly lab_m_memory_u100 --once             # one flight of a campaign, then exit (starts 8081 if it is down)
.\fly lab_m_memory_u100 --once --pack 2    # say the pack type after a swap to the other type
.\fly lab_m_memory_u100 --dry-run          # compile and print the plan, no 8081 contact
.\fly --list-exp                           # every experiment row
.\fly --list-gains                         # every gain preset
.\fly lab_m_memory_u100 --once --gains c3  # write a gain preset to RAM (stop-and-wait) before the flight
```

- Keys in flight: `s` ends the step and holds the hover point (`n` next step, `l` land); `n` next step; `l` land;
  `p` PlotJuggler. **Ctrl+C lands.** Presses less than 2 s apart are one press, so mashing it lands once; a press
  after 2 s quiet aborts (it lands the same way, the report says `operator abort`). Under `labday` the deck
  survives the burst and goes on to the next card (2026-10-28).
- Pack swap mid-day: power off, swap, power on. `fly` sees the link go quiet for 3 s, waits for it to come back,
  then asks for the pack type.
- The pack gate predicts the pack % at the end of the flight and refuses below 30%. Only the operator may change
  that limit.

### 4.1 Drift NN: shadow or live (needs the 10-19 build flashed)

The onboard drift net (`NN/drift_nn_task.c`, PROPOSED weights) always runs and logs. Two VAR_WRITE rows decide
whether the flight loop uses its output:

| id | Variable | Range | Default | Meaning |
|---|---|---|---|---|
| 56 | `g_drift_nn_live` | 0..2 | 0 | 0 shadow (logged only), 1 residual added to the mode-2 position track, 2 also to the EKF velocity feedback |
| 57 | `g_drift_nn_clamp_mps` | 0..0.30 | 0.10 | limit on each applied residual axis, m/s |

In a `.fly` file, before `takeoff`:

```text
slow 50 drift_nn nn4 xy_ctrl ekf_states estimator_truth
set g_drift_nn_live=1 g_drift_nn_clamp_mps=0.05
```

- Log `drift_nn` (and `nn4`, the net's inputs) on every flight, shadow or live, so every flight is NN data.
- Live 2 acts only while the EKF velocity feedback is on (`g_ekf_of_vel_fb`, default 1, no VAR_WRITE row).
- Run the camera at fusion mode 0 or 1 (`.\cam fusion <name> 1 --yaw-deg 94`, section 3). In mode 2 or 3
  (`g_ext_pos_on` >= 2) the net is not applied, so the camera stays an independent truth.
- The net restarts on every take-off and is applied only after 125 steps (6.25 s of flight).
- `g_drift_nn.live_on` is 1 on the ticks where the residual was applied. `g_drift_nn.live_why` says why not:

| live_why | Meaning |
|---|---|
| 0 | applied |
| 1 | `g_drift_nn_live` = 0 (shadow) |
| 2 | task not running (`g_drift_nn.status`: 2 low heap, 3 not created, 4 bad weight checksum) |
| 3 | no new step for 200 ms |
| 4 | warm-up: fewer than 125 steps since take-off |
| 5 | not EKF bias mode 2, or the OF EKF is unhealthy |
| 6 | not flying |
| 7 | camera fusion in the loop (`g_ext_pos_on` >= 2) |

- Score a flight with camera truth: `video_truth truth4` then `nn4` (section 2.8). The design notes are in
  `docs/lab/drift-nn-leak-fix-2026-10-19.md`.

### 4.2 Excitation pattern step (needs the excite build flashed, CMD 0x2E)

A campaign scenario step that plays one of the 24 firmware multisine rows (`API/excite.c`, host mirror
`ground_station/analysis/excite_patterns.py`) on one or more axes at once:

```yaml
steps:
  - takeoff: {z: 0.8}
  - hold: {s: 5}                       # required: the start latches the xy box centre where the drone holds
  - pattern: {layer: inner, speed: slow, axes: [pitch, roll], periods: 2}
  - land
```

| Key | Values | Meaning |
|---|---|---|
| `layer` | `outer`, `inner` | outer = setpoint (x/y cm, z m, yaw/pitch/roll deg), inner = rate (deg/s, z m/s) |
| `speed` | `slow`, `fast` | disjoint harmonic sets of the same axis |
| `axes` | any of `x y z yaw pitch roll`, no repeats | axes never share a harmonic, so several can run together |
| `periods` | 1..10, default 1 | outer T = 40 s, inner T = 10 s per period (PROPOSED) |

- Step time = periods x T + 2 s settle. Takeoff z must be at least 0.40 m; an outer z pattern must stay inside 0.40..1.40 m.
- The firmware refuses (and latches `excite_reason`) when disarmed, on the ground, outside 0.30..1.50 m, beyond the
  50 cm xy box, over the tilt limit, or during SysID. A land or abort sends the excite abort first.
- Rehearse on the fake drone: `pytest -q ground_station/service/tests/test_pattern_step.py`.

### 4.3 Experiment telemetry block (needs the item-6 build flashed, `mrac_xt`)

One contiguous range of 81 int16 words (MULTI build): pattern word + phase + period, then per MRAC axis
(pitch, roll, yaw, z, x, y) e and u_ad (x1000), L2 trap bits (3 words), and 8 group contributions
(EMA of theta*phi by group, x1000). Log it in its own slot, then decode:

```bash
python -m ground_station.livewatch.stream_log --group "100:mrac_xt.pat:81" --seconds 60 --out logs/xt.csv
```

```bash
python -m ground_station.analysis.mrac_xt logs/xt.slot0.csv
```

Which axes the pattern excited and which feature groups were active (spec item 7, thresholds PROPOSED):

```bash
python -m ground_station.analysis.mrac_active logs/xt.slot0.csv
```

- Cost is (7 + 162) x 100 = 16.9 KB/s at 100 Hz on the WiFi link; drop-free at that rate is UNVERIFIED.
- Run `python -m ground_station.livewatch verify` first: the ELF must match the flashed image.
- Layout and pattern-word bits: `ground_station/analysis/mrac_xt.py` docstring; spec item 6 in
  `docs/design/2026-10-22-mrac-experiment-stack.md`.

### 4.4 MRAC L3 prior (needs the item-3 build flashed, CMD 0x2F..0x34)

Seed the next flight's MRAC weights from a previous flight's final Theta. Disarmed only (the firmware refuses it armed,
interlock 6). Python, with the service on 8081 (`HttpWfbClient`) and a previous flight's log as a DataFrame `df`:

```python
from ground_station.platform import mrac_prior
from ground_station.service.campaign_fly import HttpWfbClient
prior = mrac_prior.prior_from_columns(df, last_n=500)      # median of the last 500 Theta samples per feature
steps = mrac_prior.plan(prior, sigma=0.5, pull=("pitch", "roll"), warm=("pitch", "roll"))
failed = mrac_prior.send(HttpWfbClient("http://127.0.0.1:8081"), steps)   # [] = every write APPLIED
```

- Warm bits go last: every accepted write re-seeds the warm axes at the next disarmed tick.
- A failed (refused or unknown) write is safe to resend: the writes are idempotent.
- Readback: the seeded Theta shows in the next flight's `mrac_state.<axis>.Theta[i]`, not in the reply.
- `sigma=0.5` above is an example, not a tuned value (PROPOSED).

### 4.5 MRAC x/y layers, D4 (needs the item-5 build flashed, CMD 0x1D field 31)

Pick which x/y MRAC layers fly: 1 inner only (boot), 2 outer only, 3 both, 0 neither. Send it on the ground; it is
a target, and the ground latch makes it live from the next takeoff (a reboot goes back to 1). With the service on 8081:

```python
from ground_station.service.campaign_fly import HttpWfbClient
c = HttpWfbClient("http://127.0.0.1:8081")
c.mrac_xy_layers("outer")                                  # "none" / "inner" / "outer" / "both"; True = APPLIED
c.mrac_variant("roll", "basis", 3)                         # any CMD 0x1D knob: axis name or 0..5, field name or number
```

- A value the firmware would refuse raises `ValueError` before anything is sent; the firmware's own answer
  (refused, or no reply) comes back as `False`.
- Readback: `mrac_xy_layers` (target) and `mrac_xy_layers_live` (flying); the outer state is `mrac_pos[0..1]`.
- Outer row values bw / zeta are PROPOSED (spec item 5): rerun the research sim before an outer flight.
- The x/y layer target path is UNVERIFIED on the drone (host tests only).

### 4.6 MRAC `.fly` ground verbs, reference fit and layer A/B (2026-10-24)

In a `.fly` script (`ground_station/service/fly_file.py`), ground verbs go before `takeoff`:

```text
layers xy both                              # groups: xy, att, yaw, z; targets: inner, outer, both, off
xy_layers inner                             # alias of `layers xy inner`
mrac_ref from=lqr                           # loads logs/ref_fit/lqr.json (ref_type, ref_model_bw, ref_delay_s[, ref_model_zeta])
mrac_prior from=latest:lab-p-pattern-learn  # seeds Theta from the newest session of that campaign (or an explicit session id)
takeoff 0.5
hold 3
pattern layer=outer speed=slow axes=x,y periods=1
step axis=z delta=0.15 hold=3               # CMD 0x1C onboard program step (axes: z in m, yaw in deg)
land
```

Fit the x/y reference model from an alone-retest shadow session (`logs/ref_fit/<kind>.json`):

```powershell
python -m ground_station.analysis.mrac_ref_fit <session> --kind lqr
```

Compare MRAC layer arms against a reference arm on tracking, step response, control effort, multisine margins and hold oscillation:

```powershell
python -m ground_station.analysis.layer_ab 2026-10-24 --arms xy-layer-inner,xy-layer-outer,xy-layer-both --ref xy-layer-inner
```

### 4.7 Inner-to-outer SysID chain and model-based gain design (2026-10-27)

Identify each cascaded loop from inside out (`rate` -> `angle` -> `vel` -> `pos`), save `model_<loop>_<axis>.json` and diagnostic FRF/PSD/margin plots, and print the PROPOSED gain-write command (never sent automatically):

```powershell
python -m ground_station.autotune.cli <session> --loop rate --axis roll --correct-zoh --plot-dir logs/autotune --stem rate_roll --print-gain-write
python -m ground_station.autotune.cli <session> --loop rate --axis roll --flight-file <saved-flight.fly> --rate-gains 3.5,0.01,13 --correct-zoh --out logs/autotune
python -m ground_station.autotune.cli <session> --loop angle --axis roll --inner-model logs/autotune/model_rate_roll.json --out logs/autotune
python -m ground_station.autotune.cli <session> --loop vel --axis x --inner-model logs/autotune/model_angle_pitch.json --out logs/autotune
python -m ground_station.autotune.cli <session> --loop pos --axis x --inner-model logs/autotune/model_vel_x.json --out logs/autotune
```

`--flight-file` reads the matching loop/axis excitation settings from the saved flight;
split low/high bands are aligned separately and combined through the existing cross-spectrum estimator.
`--rate-gains` above is an example: use that recording's flown gain snapshot.
The deck gate resolves the session from the flight metadata, supplies these snapshots automatically,
and recomputes cached fits. It reads nested proposed margins, fit-bin coherence, fit residual and bandwidth
separation. The existing coverage/residual/margin refusals remain active; a software repair does not
make a poor measured fit acceptable. Missing snapshots refuse instead of guessing flown parameters.

Replay the 10-24 rate logs, compute excitation/subscribe link math, write `API/xy_ctrl_gains_candidate.h`, and run the PID/LQR/MPC/ADRC chain simulations (`docs/lab/days/2026-10-27.yaml`):

```powershell
python -m ground_station.analysis.sysid_chain_sim --sim-only
```

## 5. Ground service 8081

```powershell
python -m ground_station.service --port 8081                    # real drone over WiFi
```

- `labday`, `fly` and `cam start` start it when it is down.
- Logs go to `logs/service/8081-<YYYYMMDD-HHMMSS>.log`.
- Restart it only while the drone is disarmed and no flight runs. The service needs about 40 s to bind after
  start-up.
- Health check: `curl.exe -s --noproxy "*" http://127.0.0.1:8081/health`

## 6. Flash the firmware

```powershell
python -m ground_station.flashtool.rebuild_and_flash --yes
```

- Stop 8081 first (it locks the .axf), and only flash with the drone disarmed.
- Lab mode suppresses legacy UART5 frames. If UART5 is silent after stopping 8081, the flash tool verifies the
  previous ELF and requires clean, unanimous disarmed SWD votes before requesting MIXED telemetry over the lab
  WiFi link. The WiFi port must be free; UART5 power and telemetry arm checks must then pass normally. No
  telemetry still means refusal; it does not by itself prove the drone is unpowered. Service startup restores
  SUBSCRIBE_ONLY for the flight streams.
- Never use `--force`. Stop on a non-zero exit.

## 7. Night rehearsal on the fake drone

```powershell
python -m ground_station.lab.fake_drone --vbat 16.4                       # fake FC on 127.0.0.2:14550, control 14551
python -m ground_station.service --port 8081 --wifi-host 127.0.0.2        # service talks to the fake drone
python -m ground_station.lab.fake_drone ctl arm                           # also: disarm, reboot, vbat 15.2, sbus off, ch6 on, quit
```

`ctl reboot` goes silent for 3 s (`--silent-s`), like a pack swap.

## 8. When something breaks

| Symptom | Look / do |
|---|---|
| stale_telemetry abort | `Select-String "ingest behind" (Get-ChildItem logs\service\8081-*.log \| Sort-Object LastWriteTime)[-1]`; while it lags: `py-spy dump --pid <8081 pid>` |
| Dot off the drone body | land, rescan the floor (2.1), check `--yaw-deg` |
| No video / camera loop fights | `.\cam check <name>` (restarts count); `.\cam stop <name>` closes every scrcpy and camera loop |
| Live stream holds UDP 14550/47800 | `.\cam end` |
| Tracker window left open | the next `.\cam fusion` closes it (stop file, then a force kill) |

## Safety (always)

- Never arm or spin motors from a script. The operator arms by RC.
- Never use the SWD probe during a capture. WiFi only.
- Start a camera-truth recording only after the operator confirms the preset, the variable list and the session
  name.
- Never run `lab end` on test or rehearsal state, because it rewrites `docs/lab/catalog.yaml`.
