# Grill log: autonomous fly -> analyse -> repeat loop (workflow B)

Session 2026-09-29 (/grilling). Nothing built yet; do not act until user confirms shared understanding.
Other sessions own: (A) deterministic post-flight analysis pipeline, (C) flight16 analysis/tuning.
This session owns ONLY (B): agent subscribes+streams, arms, idles motors, hover / preset paths,
lands at takeoff spot, runs A's analysis, recommends tuning, checks battery >30%, repeats;
battery hits 30% in air -> land at origin and stop for battery swap. Must be modular for future
controllers (currently PID + adaptive layer).

## Facts found (verified in code this session)
- GS arm + virtual sticks: TASK/send_data.c ~1930-1990 (ARM REQ idx0; idx1 motor idle; ARM REQ OFF
  only relinquishes authority, drone stays armed, physical RC resumes).
- Presets: TASK/AutoflyTask.c PathArbitrate (sinusoid/circle/figure8/TWC); gated on GS authority.
- Landing: only RC ch5 edge sets FLIGHT_PHASE_LANDING (TASK/RemoterTask.c:180). Descent auto
  0.30 m/s, touchdown detect, auto disarm (TASK/StabilizerTask.c:694-735). No GS land command found.
- Battery: 4S, real_voltage updated 1 Hz, beep only <15.0 V (StabilizerTask.c:1554). No auto-land.
- RC takeover: physical stick rate >0.05/tick grabs authority back; ch10 mode switch = hard kill
  regardless of authority (API/rc_input.c:20-100). Heartbeat lost flag at rc_input.c:250 (effect TBD).
- Dashboard agent has operator-only `allow_agent_arm` toggle (service/agent.py:619,715).
- Memory rule conflicts: "never arm/spin motors" (supervisor-may-post-8081.md) -> must be resolved.
- Analysis engine ground_station/analysis/flight_report.py reads dashboard telemetry.csv+manifest,
  NOT VOFA slot CSVs (logs/vofa/flightN.slotK.csv + meta.json).

- GS link loss: RC_HEARTBEAT_TIMEOUT_MS=500 (API/rc_input.h:40) -> authority revoked, virtual sticks
  zeroed, s_heartbeat_lost=1. RCInput_IsHeartbeatLost() has NO callers -> nothing lands; drone just
  follows the physical sticks.
- RC (SBUS) link loss or ch9<=500 for >10 ticks -> FLIGHT_EVENT_DANGEROUS_STOP (TASK/RemoterTask.c:156-167),
  i.e. motors cut even mid-air. The RC transmitter MUST stay on for the whole session.
- ch7 rising edge = existing "arm + fly up to Z=0.5 m" trigger (RemoterTask.c:188) -> takeoff primitive exists.

## Decisions
- Q1 sequencing: user chose to focus this session on B only (A, C run in parallel sessions).
- Q2 supervision (2026-09-29): OPERATOR-GATED BATTERY SESSIONS. Operator swaps battery, puts drone on pad,
  gives one "go" (operator-only allow_agent_arm + per-battery go). Agent runs the whole test queue on
  that battery unattended. RC transmitter powered and within operator reach (ch10 kill); operator in the
  same room but may work on other things. Memory rule amended (supervisor-may-post-8081.md).

- Q3 link loss (2026-09-29): FIRMWARE AUTO-LAND IN PLACE. Heartbeat lost while GS held authority and
  phase==FLYING -> FLIGHT_PHASE_LANDING (existing descent+touchdown+disarm). Stick takeover and ch10
  still override. Tier-0 edit (permission at build time). Agent logs flight as aborted(link_loss).
  User: RF drop unlikely (3 m, closed room). Kept anyway because it also covers the PC or GS process
  dying (laptop AC is fragile).
- Derived (no objection expected): the agent's normal "land" uses a NEW GS land command that enters the
  same FLIGHT_PHASE_LANDING path (today only RC ch5 can start a landing).
- Fact: OF origin (0,0) is rebased on every ARM rising edge (StabilizerTask.c:871-915) and by
  CMD 0x10 Reset_World_Origin (StabilizerTask.c:180-206). The origin is per flight, not per battery.

- Q4 origin + geofence (2026-09-29): return to ESTIMATED origin (0,0) at hover height, settle, then
  GS land. Operator re-centres the drone on the pad at each battery swap. Accepted: landing spot may
  walk a little between flights within one battery.
  Room (user-given): 3 m wide x 4 m long x 2 m high, pad at floor centre -> 1.5 m to the side walls,
  2.0 m to the end walls. OF frame vs room axes unknown, so use a CIRCULAR fence on the 1.5 m
  half-width. Proposed (not measured, tune later): fence R=1.0 m (0.5 m margin), path envelope
  <=0.7 m, altitude ceiling 1.5 m. Breach -> abort -> auto-land in place.
- Per-experiment limit (user, 2026-09-29): max 120 s airborne per experiment (motor heating).
  A ground cool-down between flights is still open.

- Q5 battery (user, 2026-09-29): option 1 + firmware backstop.
  (a) PRE-FLIGHT GATE on resting voltage (disarmed, settled) -> SoC via 4S LiPo curve; fly only if
      predicted post-flight SoC >= 30%. Per-flight drop is LEARNED per pack from measured resting V
      before/after each 120 s flight. Otherwise end the battery session and ask for a swap.
  (b) IN-AIR agent trigger on filtered LOADED voltage -> stop test, return to origin, GS land.
      Starts conservative, recalibrated from measured sag on these packs.
  (c) Existing firmware beep <15.0 V kept.
  (d) FIRMWARE LOW-VOLTAGE AUTO-LAND backstop (tier-0), threshold BELOW the agent's so the agent
      normally fires first; firmware only catches agent failure. Same FLIGHT_PHASE_LANDING path.
  No current sensor found; voltage only. All thresholds unmeasured -> calibrate on first flights.
- Packs (user-given, ACG, all 4S1P, 16.8 V full): 1x 5300 mAh 45C 463 g; 2x 4000 mAh 30C 363 g.
  Label "14.8 V" is the NOMINAL voltage (3.7 V/cell), not the discharge floor.
  Consequences: ~100 g mass difference changes hover throttle and the adaptive layer's job, so every
  battery session must record PACK ID (operator names it at "go"; label the two small packs A/B so
  aging is tracked). Learned per-flight drop and sag are stored PER PACK.
- User feedback on Q4: path envelope <=0.7 m is TOO LIMITING -> reopened as Q6.
- Fact: OF earth_x/y are rotated by imu_data.yaw (StabilizerTask.c:426-451); yaw comes from the AHRS
  quaternion (API/imu_update.c:198), no magnetometer found -> world x axis = nose direction at
  POWER-ON, slow gyro drift after. Inferred from code; verify on bench (imu_data.yaw after boot).
  => If the drone is placed nose-to-a-marked-wall at each battery swap (= power-on), the OF frame is
  aligned with the room, so a RECTANGULAR fence becomes possible.

- Q6 fence (user, 2026-09-29): RECTANGULAR ROOM-ALIGNED FENCE (replaces Q4's circle).
  Operator places drone at pad centre, nose toward a MARKED end wall (4 m axis) at every battery swap
  (= power-on) -> OF x axis = room length axis. Proposed, unmeasured: fence +-1.1 m (width) x +-1.6 m
  (length) = 0.4 m wall margin; path envelope +-0.8 x +-1.3 m (0.3 m overshoot/drift margin); ceiling
  1.5 m (room 2 m). Agent statically checks each path's full extent vs envelope before flying it
  (reject if outside). At takeoff agent checks imu_data.yaw drift; beyond a threshold (TBD) that
  flight falls back to a circular fence. Bench check first: imu_data.yaw ~0 right after boot.
- Fact (for Q7): docs/research-platform/SPEC.md:25 already lists "no motion capture; cross-track RMSE
  judged against the drone's own estimate" as an open thesis risk. Code has a legacy T265 position
  feed (Global_file/global_declare.h:75 t265posx/y via linux_data; StabilizerTask.c:506-513, commented
  out) and API/mrac.h:22 PAYLOAD_HEAVY comment "Jetson Orin, Realsense D435i, T265".

## Open questions (in order)
- Q7 independent position truth: user asks if their camera "with separate localisation" is good for
  motion capture. Need: which camera, fixed in room or on the drone. Leaning: external fixed camera
  (+ tag on drone), offline, feeds A only, never the safety loop.
  User has (2026-09-29): (a) bare camera module reached via a USB-to-TTL adapter, model unknown, wiring
  unknown -> do NOT guess pins; serial link is too slow for video (115200 baud = ~11.5 kB/s) unless
  it is an ESP32-CAM-style board (TTL only for flashing, video over WiFi = shares air with drone link);
  (b) own phone, already USB-connected to PC. Proposed: phone, fixed high in a room corner (geometry:
  path envelope spans ~61 deg horizontally from a corner, fence ~75 deg, computed not measured).
- Q7 partial (user, 2026-09-29): PHONE (Xiaomi Redmi Note 15 Pro+, Android) is the truth camera, ~2 m
  high on carbon boxes, pointing slightly down, held by a DJI OM 5 gimbal; phone + OM 5 on PC USB.
  Checkerboards fixed on two walls. Proposed: replace gimbal by a rigid clamp (a gimbal moves the
  camera = moving reference; ActiveTrack must never run); keep wall boards in view as per-frame
  camera-moved check. Recording path (scrcpy camera source = PC clock + agent start/stop, vs phone
  camera app with manual focus/shutter + adb pull) -> pick by bench test.
  Geometry (computed from room + proposed envelope, not measured): line of sight to the drone is only
  ~7-31 deg below horizontal over most of the envelope -> a FLAT TOP TAG is seen nearly edge-on;
  height-plane intersection amplifies height error 1/tan(angle) = ~1.7-5x. Single camera is weak
  along its view axis -> second camera at the adjacent corner (laptop webcam or chip camera) only if
  a ground test at known floor points shows the along-view error is too big.
- Q7b OPEN: marker. Proposed: bright matte sphere (ping-pong-ball class) on a short light standoff at
  frame centre, above the prop disks, same look from every side; optional 2nd colour ball on the nose
  for yaw later. Markerless (drone features / video segmentation) = fallback only. Asked for photos.
- Q7b user reply (2026-09-29): no standoff possible; only option may be colouring prop tips. Props:
  black with silver/black tips, white with black/white tips, black and white "orthogonal" (layout to
  confirm by photo/clip). User-given: z, roll, pitch estimates are the most accurate; yaw and x/y
  drift most. => Camera only has to measure x, y (+ yaw); z from ToF and roll/pitch from AHRS used as
  inputs (they are gravity/floor-referenced, so they do not drift). Revised: ToF-height ray-plane
  intersection is acceptable because a ToF error gives a bounded, non-growing camera xy error while
  OF drift grows with time; drift growth is what A judges.
  Fact: props already carry 2 reflective marks per prop (blade undersides) for RPM sensing
  (BSP/rpm.h:30-31, RPM_PULSES_PER_REV=2) -> hover RPM is loggable, so ring formation vs shutter
  can be computed from data (2-blade prop needs >= half a rev per exposure: >= 3000 rpm at 1/100 s).
  Idea: a spinning prop smears into a disc/ring = ellipse in image; ellipse centre = motor axis; ellipse
  major axis = prop diameter unfaded by view angle -> range. Front/back colour difference -> yaw.
  Paint caution: tip paint = imbalance at the worst radius -> vibration -> IMU noise that pollutes the
  controller analysis; tape can fly off. Both blades equally, thin, rebalance.
  Proposed Q7b: record a ~30 s hover clip with the CURRENT props first (next operator flight, phone
  locked, 1/100 s), agent checks disc/body detectability offline; paint only if discs are too faint.
- Q7b DECIDED (user, 2026-09-29): option 1 (hover clip with current props, check rotor discs offline),
  FALLBACK option 3 (body-outline tracking via background subtraction, accept few-cm bias). No paint.
  Phone role (proposed, user asked "free processor?"): phone = agent-controlled RECORDER over ADB
  (start/stop, adb pull, clock offset), NOT a processor; all vision offline on PC. On-phone processing
  only if camera position ever enters the flight loop (excluded: camera feeds A only).
  Fact: adb and scrcpy are NOT on PATH on this PC (checked 2026-09-29) -> platform-tools needed later.
- Q8 DECIDED (user, 2026-09-29): motors-off ground cool-down >= duration of the flight just flown
  (120 s flight -> >= 120 s; proposed, not measured). No motor/ESC temperature sensor known (unverified).
  User pushback: A's analysis can take > 120 s. So the next-flight gate is the LAST of:
  cool-down done, resting voltage settled (Q5 gate), analysis the next flight depends on done.
  Drone stays disarmed on the ground while waiting; idle electronics drain is caught by the Q5 gate.
- Q8b DECIDED (user, 2026-09-29): accepted as proposed below. Wait cap value set after timing A on real logs.
  Proposal was: split A's output into a blocking
  "decision" part (telemetry metrics -> next tuning step) and a non-blocking part (camera/video truth,
  long reports) that runs in the background. Analysis wait cap (value TBD) -> pause session + notify.
- Q9 OPEN: test-plan library format. Facts (read 2026-09-29):
  firmware presets in TASK/send_data.c, SDK mode + GS authority only (AutoflyTask_PathArbitrate):
  0x0A TWC point-to-point (target x/y/z, yaw, execute), 0x0B sinusoid (center, amplitude, frequency,
  duration, axis), 0x0C circle (center, radius, angular_speed, duration), 0x11 figure-8 (center,
  amplitude, angular_speed, duration). Dashboard path library ground_station/service/path_library.py:
  one JSON per path in logs/paths/ (points x/y/z, spacing); 0 paths saved on disk now.
  Proposed: one YAML plan per campaign; experiment = maneuver (firmware preset + params, or a named
  library path) + controller settings (name -> value) + capture preset + duration <= 120 s + repeats;
  validator computes the full trajectory before arming and rejects anything outside envelope/ceiling.
- Q9 DECIDED (user, 2026-09-30): YAML plan format accepted as proposed. User ADDITIONS:
  (a) inclination-angle parameters on the presets, (b) a richer preset set, (c) a custom preset:
  user draws a trajectory by hand, uploaded to firmware (WiFi uplink or compiled in, whichever works best).
  Facts (read 2026-09-30): MCU STM32F407ZG; main SRAM 128 KB, build uses RW+ZI 125,776 B -> ~5.3 KB
  free (computed from OBJ/JX_FLY.map). CCM 64 KB at 0x10000000 declared as IRAM2 in the uvprojx; no
  firmware data placed there found (grep). path-panel.js already refuses drawn/custom kinds with
  "needs waypoint-upload firmware (flyable now: hover, line, circle, figure-8)".
- Q9b OPEN: how to deliver (a)(b)(c). Proposed: ONE generic uploaded-trajectory executor in firmware
  (time-sampled x/y/z/yaw buffer in CCM, uploaded over WiFi in chunks, checksum + bounds check in
  firmware, executes only when complete, interpolates between samples); ALL new presets incl. tilted
  ones generated on the GS in Python and validated there; existing 4 firmware presets kept unchanged.
  Buffer example 120 s x 10 Hz x 4 floats = 19,200 B (computed; rate proposed) -> CCM, not main SRAM.
  Rejected: streaming setpoints live (WiFi jitter pollutes tuning metrics); flash per path (slow,
  blocked when armed). Firmware change is flight-path code -> tier-0 permission at build time.
- Q9b DECIDED (user, 2026-09-30): accepted as proposed, with a user ADDITION: points must be at a FIXED
  DISTANCE apart, and the user or the agent sets exactly how the path is timed (velocity per point,
  acceleration, jerk limits, named profiles) so trajectories are smooth. Recorded design consequence:
  GS pipeline = shape (preset generator or hand drawing) -> tilt rotation -> resample at fixed arc-length
  spacing (parameter) -> timing profile (constant speed / trapezoid accel-limited / S-curve jerk-limited /
  per-point velocity override) -> validator (envelope, ceiling, <= 120 s, speed/accel/jerk limits) -> upload.
  Firmware buffer = fixed-spacing points, each x/y/z/yaw + timestamp (5 floats = 20 B); firmware stays
  simple and interpolates by time between neighbours. Point count follows path length / spacing, not
  duration: e.g. 60 m path (120 s at 0.5 m/s) at 5 cm spacing = 1,200 points x 20 B = 24,000 B
  (computed; speed and spacing proposed) -> CCM. Exceptions: hold/dwell = zero-distance segment with a
  time gap; step/doublet presets use profile "none" (they are meant to be sharp). Fact (read 2026-09-30):
  firmware already has CMD 0x14 SysID excitation (chirp/multisine on pitch/roll/yaw rate or Z, f0/f1,
  amplitude, duration, geofence flag; TASK/send_data.c:1792) -> frequency sweeps use 0x14 as a
  maneuver kind in the plan, not a position chirp.
- Q10 OPEN: agent autonomy over gains. Fact (read 2026-09-30): ground_station/service/agent.py:240-253
  classes every PID/MRAC/limits/filter param-write command (0x01..0x1E list) as tier 0, and a tier-0
  write "needs operator approval in EVERY mode, autonomous included". Proposed: operator approves a
  per-campaign gain envelope once (per parameter: min, max, max change per flight); agent writes gains
  only while landed + disarmed, only inside the envelope, reads them back, logs the exact set per flight;
  anything outside the envelope -> asks. Requires a narrow exception to the agent.py rule.
- Q10 DECIDED (user, 2026-09-30): once the operator starts a campaign the agent has FULL FREEDOM over
  gains (no approved range needed) AND may change flight-critical firmware code when a control-theory
  justification supports it; some parameters stay off limits. This is the explicit tier-0 grant for
  workflow B campaigns (AGENTS.md: tier 0 needs explicit permission). Read-back of every write and
  per-flight logging of the exact gain set / firmware hash still apply (from the proposal).
  Facts (read 2026-09-30): tier-0 files per docs/agent-map/modules.yaml: StabilizerTask.c, mrac.c,
  mrac_math.c, pid.c, flight_fsm.c, rc_input.c, RemoterTask.c, pwm.c, imu_update.c, gyro_filter.c,
  bmi088_driver.c, main.c, send_data.c. Sim bench exists: sim/bench (plant.py, calib_replay against
  logs, controller ports ctrl_*.py, c_ref/ C-reference with test_equiv.py) -> a pre-flight sim gate is
  feasible.
- Q10b OPEN: what is off limits. Proposed PROTECTED SET = the safety net, never changed by the agent:
  RC input + ch10 kill + pilot takeover (rc_input.c, RemoterTask.c, AutoflyTask_PathArbitrate), arm/disarm
  and landing transitions (flight_fsm.c), heartbeat-loss auto-land, low-V backstop, geofence/ceiling,
  trajectory-upload bounds check, flash-when-armed block, motor output driver + mixer saturation
  (pwm.c), IMU driver (bmi088_driver.c), init (main.c); params: safety limits (tilt/rate limits, fence,
  ceiling, low-V thresholds, heartbeat timeout, mixer saturation). Everything that shapes how well it
  flies is open: PID/MRAC code and gains, filters, EKF modes, trajectory tracking. Mixed files
  (StabilizerTask.c, send_data.c) get PROTECTED BEGIN/END markers. Enforced mechanically: a protected
  list (paths, marked regions, param IDs) checked against every diff and every param write; a hit ->
  refuse + ask operator.
- then: gate a code change must pass before it flies (build, sim bench, rollback image), controller-
  agnostic interface to A, stop/abort criteria.
