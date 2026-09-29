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

## Open questions (in order)
- Q6 path envelope + fence shape (rectangular room-aligned fence vs bigger circle)
- then: motor cool-down between flights, test-plan library format, controller-agnostic interface to A,
  stop/abort criteria.
