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
| Gates off (battery + firmware trips + RC only) | `.\labday 2026-10-19 --no-gates` or `.ly <card> --once --no-gates` (restart 8081 after the 10-24 commit) |
| Scan the floor (new phone placement, drone out of view) | `.\cam floor floor-1019` |
| Start the camera fusion for one flight | `.\cam fusion f1` (mode 3, `--yaw-deg 94`) |
| Same with another mode | `.\cam fusion f1 2 --yaw-deg 94` |
| Fly one flight | `.\fly lab_m_memory_u100 --once` |
| Stop the camera after landing | `.\cam stop` (newest running session) or `.\cam stop f1` |
| Check rows, video, fixes | `.\cam check f1` |
| End the live VOFA+/PlotJuggler stream | `.\cam end` |
| Start the ground service by hand | `python -m ground_station.service --port 8081` |
| Flash the firmware | `python -m ground_station.flashtool.rebuild_and_flash --yes` |
| Drift NN live for one flight | `.fly` line `set g_drift_nn_live=1 g_drift_nn_clamp_mps=0.05` (section 4.1) |

## 1. A lab day: `labday`

```powershell
.\labday 2026-10-19                 # dry run each flight once, then fly them in order (8081 + camera started for you)
.\labday 2026-10-19 --list          # plan + what is flown, then exit
.\labday 2026-10-19 --no-gates      # safety monitor gates off; low battery, firmware trips, RC kill/takeover stay
.\labday 2026-10-19 --sheet         # the run sheet as markdown, then exit
.\labday 2026-10-19 --from mem2     # start at this flight id
.\labday 2026-10-19 --pack 2        # the pack type in the drone now (1 or 2), once after a swap to the other type
.\labday 2026-10-19 --dry-run       # only the ground dry runs of the flights left
.\labday 2026-10-19 --no-camera     # fly without the camera fusion
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
  A scan counts only when its folder holds the scenery mask and the landing pad is seen in at least half the frames.
  New scans are `floor-<date>[-N]`.
- Battery: before each flight, the resting volts and the predicted pack % after the flight against the 30% gate;
  after each flight, the landing volts and the sag; the cooldown line shows live volts.
- A failed gate says SWAP THE PACK: power off, swap, power on. labday waits for the link to drop and come back,
  asks the pack type and goes on in the same run (8081 keeps running, the dry-run cache stays valid).

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
The faster read changes the true camera latency, so re-measure `--delay-ms` before trusting the old 330 ms.

## 4. One flight: `fly`

```powershell
.\fly lab_m_memory_u100 --once             # one flight of a campaign, then exit (starts 8081 if it is down)
.\fly lab_m_memory_u100 --once --pack 2    # say the pack type after a swap to the other type
.\fly lab_m_memory_u100 --dry-run          # compile and print the plan, no 8081 contact
.\fly --list-exp                           # every experiment row
.\fly --list-gains                         # every gain preset
.\fly lab_m_memory_u100 --once --gains c3  # write a gain preset to RAM (stop-and-wait) before the flight
```

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
