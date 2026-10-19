<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, including all existing tests,
   conftest.py, model.py, registry.py, pipeline.py, config/*.yaml and schema/*.json.
2. Make every assertion pass by fixing implementation code. Keep existing tests and assertions exactly
   as they are. [ImpossibleBench, arXiv 2510.20270]
3. Write complete implementations. Every function body does real work; no "...", TODO, `pass` stubs or
   "rest of code" comments.
4. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
5. Run every command in the foreground and paste its verbatim output and exit code into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
6. Catch only specific exceptions (ValueError, KeyError, ...) and re-raise or record them in "warnings".
   Bare `except:` and `except Exception: pass` are forbidden. [general, arXiv 2603.28592]
7. Compare floats with `np.isclose`/`pytest.approx`; test NaN with `np.isnan`/`math.isnan`.
8. State array axes explicitly (`axis=0`); write interval ends as half-open [t0, t1) unless told otherwise;
   check lengths before slicing. [general, arXiv 2511.18782]
9. Keep every function signature, name and return shape exactly as the brief specifies.
10. Import only: python stdlib, numpy, scipy, pandas (loaders only), matplotlib, jsonschema, yaml.
    Use only functions you have seen documented; when unsure, check with `python -c "help(...)"`.
11. If a command or edit fails twice the same way, change approach; never repeat an identical call.
12. Write the digest `.agent-ops/out/<id>.md` BEFORE printing DONE; the supervisor rejects a run whose
    digest file is missing, regardless of exit code.
13. Research tasks: cite canonical URLs only (no vertexaisearch / google.com/url redirects); quote <= 25 words.
14. Output: no preamble, no summary prose. Code in files; status in the digest.
</guardrails>

<context>
Repository: STM32F407 flight-controller firmware plus a Python ground station (package `ground_station`).
This is "task G3" of workflow B (autonomous tuning flights): the typed command client for the new firmware
commands, and a software drone that answers those commands exactly like the firmware. Every later
ground-station task is tested against this software drone, so its fidelity to the firmware matters more
than anything else in this task.
Read first, fully:
- docs/workflow-b/interfaces.md sections 0, 1, 2 and the two section-4 rows `platform/wfb_commands.py`
  and `service/fake_drone.py`.
- ground_station/platform/transactions.py (Command, Outcome, build_command, parse_command: reuse them,
  do not re-implement the frame packing).
- API/wfb_types.h, API/wfb_traj.h, API/wfb_traj.c, API/wfb_prim.h, API/wfb_prim.c, API/wfb_safety.h,
  API/wfb_safety.c (the firmware state machines the software drone must reproduce: states, transitions,
  error codes, timers, constants).
- ground_station/service/trajectory_pipeline.py (TrajPoint, TrajLimits, validate, crc32, generate).
Frame: x forward, y left, z up, metres; yaw in degrees; t in seconds. The hover point is (0, 0, hover_z).
Python 3.10+, standard library only. Type hints on every public function. No I/O, no threads, no sleeping,
no wall clock: time advances only through `FakeDrone.step(dt)`. No global mutable state.
</context>

<task>
Implement the command client, the software drone, and their tests.

ALLOW-LIST (create only these four files, nothing else anywhere):
- ground_station/platform/wfb_commands.py
- ground_station/platform/tests/test_wfb_commands.py
- ground_station/service/fake_drone.py
- ground_station/service/tests/test_fake_drone.py

PART 1: ground_station/platform/wfb_commands.py
- Constants: CMD_KILL = 0x0D, CMD_ARM = 0x0E, CMD_PRIM = 0x1A, CMD_TRAJ = 0x1B.
- class PrimIdx(IntEnum): TAKEOFF 0, LAND 1, HEARTBEAT 2, SET_HOVER_Z 3.
- class TrajIdx(IntEnum): BEGIN 0, APPEND 1, CRC_HI 2, COMMIT 3, START 4, STOP 5, CLEAR 6.
- class ArmIdx(IntEnum): ARM 0, IDLE 1.
- encode(cmd: int, idx: int, value: float, txid: int) -> bytes
  Returns transactions.build_command(Command(transaction_id=txid, command_id=cmd, index=idx, value=value)).
- class WfbClient:
  - __init__(self, send: Callable[[bytes], int], first_txid: int = 1)
    `send` takes one frame and returns the result code (transactions.Outcome value).
  - Every method below builds one frame with the next transaction id, calls `send` once, and returns
    True only when the result equals Outcome.APPLIED (ACK and REJECTED give False). Transaction ids
    increase by 1 per frame and wrap from 65535 to 1 (0 is never used). An exception raised by `send`
    propagates unchanged.
  - arm() -> CMD_ARM, ArmIdx.ARM, 1.0        idle() -> CMD_ARM, ArmIdx.IDLE, 1.0
  - kill() -> CMD_KILL, 0, 0.0               (operator abort: motors off)
  - takeoff(), land(), heartbeat() -> CMD_PRIM with value 0.0
  - set_hover_z(z: float) -> CMD_PRIM, SET_HOVER_Z, z
  - traj_begin(n: int) -> CMD_TRAJ, BEGIN, float(n); n that is not an int (bool counts as not an int)
    raises ValueError before anything is sent
  - traj_append(v: float) -> CMD_TRAJ, APPEND, v
  - traj_commit(crc: int) -> sends CRC_HI with float(crc >> 16); when that is not APPLIED returns False
    without sending COMMIT; otherwise sends COMMIT with float(crc & 0xFFFF) and returns its result.
    crc outside 0..0xFFFFFFFF or not an int raises ValueError before anything is sent.
  - traj_start(), traj_stop(), traj_clear() -> CMD_TRAJ with value 0.0
  - property last_txid -> int (id of the last frame sent; 0 before the first frame)

PART 2: ground_station/service/fake_drone.py
- class PrimState(IntEnum), TrajState(IntEnum), WfbErr(IntEnum), Trip(IntEnum): same names (without the
  WFB_ prefix) and the same numeric values as the C enums in API/wfb_prim.h, API/wfb_traj.h,
  API/wfb_types.h, API/wfb_safety.h.
- @dataclass(frozen=True) FakeParams: every timing and rate constant the C modules use (return XY rate,
  settle radius and time, return timeout, climb and descent rates, heartbeat timeout, airborne cap,
  low-voltage threshold and hold, tilt threshold and hold, fence, ceiling, hover_z range). Take each default
  from the C default-limit tables; where the C modules leave a rate to the integration (climb rate,
  descent rate, position tracking speed) choose a value, and list every such choice in the digest.
- class FakeDrone:
  - __init__(self, limits: TrajLimits = TrajLimits(), params: FakeParams = FakeParams(), hover_z: float = 0.5)
  - Plain attributes a test may set at any time: sbus_live: bool = True, vbat_v: float = 16.0,
    roll_deg: float = 0.0, pitch_deg: float = 0.0.
  - Read-only properties: armed: bool, motors_idle: bool, position: tuple[float, float, float],
    yaw_deg: float.
  - send(self, frame: bytes) -> int
    Parses the frame with transactions.parse_command. A frame that does not parse, or an unknown command
    id or index, returns Outcome.REJECTED and changes nothing. Otherwise applies the rules below and
    returns Outcome.APPLIED or Outcome.REJECTED. Never raises.
    - CMD_ARM idx ARM: value >= 0.5 arms (accepted only on the ground with sbus_live); value < 0.5 disarms
      (accepted only on the ground). idx IDLE: value >= 0.5 sets motors to idle (needs armed, on the
      ground); value < 0.5 clears it (on the ground).
    - CMD_KILL: always APPLIED. Motors off, disarmed, primitive state IDLE, trajectory execution stopped;
      when airborne the vehicle drops to z = 0 at once.
    - CMD_PRIM and CMD_TRAJ: exactly the "Accepted when" and "Effect" columns of interfaces.md section 1,
      with the error code the C modules return for each rejection stored in `last_err` (0 after an applied
      CMD_PRIM or CMD_TRAJ command). Values are handled as float32 (they arrive that way).
    - COMMIT: reproduce wfb_traj_commit's order. State, CRC_HI present, float count, crc_lo range, then
      compare the CRC (trajectory_pipeline.crc32 over the received points), then the remaining checks by
      calling trajectory_pipeline.validate(points, limits, hover_z) and mapping the tag of its FIRST
      message to the error code (TIME, BOUNDS, ENDPOINT, SPEED; RANGE and COUNT map to their codes too).
      Do not re-implement those checks. Pass -> READY; fail while LOADING -> EMPTY.
  - step(self, dt: float) -> None
    Advances time by dt (dt <= 0 or non-finite raises ValueError). Order inside one step: timers
    (heartbeat age, airborne time, trajectory time), safety net, primitive state machine, then motion.
    - Primitive state machine and return/settle/descend timing: a faithful port of wfb_prim_step.
    - Safety net: a faithful port of wfb_safety_step using the drone's own position, vbat_v, roll_deg,
      pitch_deg, heartbeat age and gs_flight_active. Actions: LAND_VIA_HOVER = the LAND path,
      LAND_IN_PLACE = descend in place, KILL = the CMD_KILL effect. The trip stays latched until disarm.
    - Motion (point mass, no dynamics): the position moves straight toward the current setpoint, limited
      to the tracking speed from FakeParams; yaw takes the setpoint yaw. While executing, the setpoint
      is the trajectory sampled at traj_t with the interpolation of wfb_traj_sample (linear in time, yaw
      on the shortest arc). At the last point the trajectory state becomes DONE and the vehicle holds the
      hover point.
    - Touchdown (z reaches 0 in DESCEND): disarmed, motors off, primitive state IDLE,
      gs_flight_active 0, safety state reset as wfb_safety_init does.
  - status(self) -> dict[str, float]
    Exactly the 13 keys of interfaces.md section 2, every value a Python float.

Required tests, PART 1 (ground_station/platform/tests/test_wfb_commands.py; use a recording `send` and
transactions.parse_command to inspect frames):
1. encode(0x1A, 2, 0.0, 1) == bytes.fromhex("ccdf010001001a020400000000001c") and has length 15
   (value measured from build_command on this commit).
2. One parametrised test: every WfbClient method sends the (command id, index, value) listed above.
3. A method returns True for APPLIED and False for ACK and for REJECTED.
4. Transaction ids: 1, 2, 3 for three frames; first_txid=65535 gives 65535 then 1; last_txid follows.
5. traj_commit(0x12345678) sends CRC_HI 0x1234 then COMMIT 0x5678; a rejected CRC_HI sends one frame
   only and returns False; crc = -1, 2**32 and 1.5 raise ValueError with nothing sent.
6. traj_begin(2.0) and traj_begin(True) raise ValueError with nothing sent.
7. An exception from `send` propagates.

Required tests, PART 2 (ground_station/service/tests/test_fake_drone.py; drive the drone only through
WfbClient(drone.send), drone.step and the settable attributes; build trajectories with
trajectory_pipeline.generate or hand-built TrajPoint lists; a helper `upload(client, points)` and a helper
`run(drone, client, seconds, heartbeat=True)` that steps at dt = 0.02 and sends a heartbeat every 0.2 s):
1. TAKEOFF is rejected when not armed, when armed without idle, and with sbus_live False (last_err is
   not 0 each time); after arm + idle it is applied, gs_flight_active becomes 1, and the drone reaches
   primitive state HOVER at (0, 0, hover_z) within 0.15 m.
2. SET_HOVER_Z: 0.7 applied on the ground and used by the next takeoff; 0.2, 1.3 and NaN rejected;
   rejected while airborne.
3. Upload of generate("circle", {"radius_m": 0.3}, Profile(0.3, 0.5, 0.02, 0.5)): state READY,
   traj_n == N, traj_rx == 5N, traj_crc_hi and traj_crc_lo equal the halves of trajectory_pipeline.crc32.
4. One test per upload rejection, each asserting the returned False, the resulting trajectory state and
   last_err: BEGIN with 1, 601 and 2.5; APPEND outside LOADING; a (5N+1)-th APPEND; APPEND NaN;
   CRC_HI 65536 (send the raw frame with encode); COMMIT before CRC_HI; COMMIT with too few floats;
   COMMIT with a wrong CRC; and four hand-built lists with a correct CRC failing TIME, BOUNDS, ENDPOINT
   and SPEED.
5. START is rejected on the ground and while climbing; applied in HOVER. During execution the position
   stays within 0.05 m of the interpolated trajectory; it ends with trajectory state DONE, primitive
   state HOVER, position within 0.15 m of the hover point. BEGIN and CLEAR are rejected while EXECUTING.
6. STOP in mid-trajectory: state READY, and the drone returns to the hover point and reaches HOVER.
   STOP when not executing is rejected.
7. LAND from HOVER: descends, touches down, ends disarmed with primitive state IDLE and
   gs_flight_active 0. LAND in mid-trajectory away from the hover point: the horizontal distance from
   the origin is below the settle radius at the first step in which z decreases. LAND on the ground is
   rejected.
8. Heartbeat: with heartbeats every 0.2 s, 5 s of hover trips nothing; without heartbeats safety_trip
   becomes HEARTBEAT after the timeout and the drone lands by itself.
9. Airborne cap: hovering with heartbeats past the cap gives safety_trip AIRBORNE_CAP and a landing.
10. Low voltage: vbat_v just under the threshold for the hold time gives LOW_V and a landing; a dip
    shorter than the hold time trips nothing.
11. Tilt: roll_deg over the threshold for the hold time gives TILT and motors off at once.
12. kill() while airborne: applied, disarmed, primitive state IDLE, z == 0.
13. A frame with a wrong checksum, a truncated frame, and an unknown command id each return REJECTED
    and leave status() unchanged.
14. Determinism: two drones given the same command and step sequence return equal status() and position.
15. status() has exactly the 13 keys of interfaces.md section 2 and every value is a float.

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/platform/tests/test_wfb_commands.py ground_station/service/tests/test_fake_drone.py -q

Digest: write .agent-ops/out/wfb-g3.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved (one line each: what the C modules or interfaces.md left open and
what you chose), every FakeParams value that does not come from a C table, a line "SUBSTITUTIONS: none"
(or what you substituted), and "NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
