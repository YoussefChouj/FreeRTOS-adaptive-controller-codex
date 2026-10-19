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
This is "task G10" of workflow B (autonomous tuning flights): the ground-station abort monitor. While the
drone flies a test trajectory, the campaign runner feeds every telemetry sample to this monitor. The monitor
answers with a decision level:
  0 = nothing, keep flying
  1 = stop the trajectory and land (the runner sends the LAND command; never the motor kill)
  3 = campaign stop (land if airborne, then no more flights until the operator acts)
Level 2 (flight failure -> revert parameters) is decided after the flight by another module; this module
never returns 2.
The firmware has its own, faster safety net. This monitor is the slower, earlier layer: its limits sit
inside the firmware limits, so it lands the drone before the firmware has to.
The module is pure logic: no I/O, no threads, no clock reads, no logging needed. Time comes only from the
sample. Python 3.10+, standard library only (dataclasses, collections, math, typing). Type hints on every
public function. No global mutable state.
Read first: docs/workflow-b/interfaces.md section 4 (the `service/abort_monitor.py` row and the paragraph
under the table about abort level 1).
</context>

<task>
Implement the abort monitor and its tests.

ALLOW-LIST (create only these two files, nothing else anywhere):
- ground_station/service/abort_monitor.py
- ground_station/service/tests/test_abort_monitor.py

Interface (names, field order and defaults exact):

@dataclass(frozen=True)
class AbortLimits:                      # every default below carries the comment  # PROPOSED
    pos_err_m: float = 0.25             # 3-D distance between position and reference
    pos_err_hold_s: float = 0.3
    tilt_deg: float = 35.0              # |roll| or |pitch|
    tilt_hold_s: float = 0.2
    osc_rms_dps: float = 60.0           # rate-error RMS, worst axis
    osc_window_s: float = 1.0
    sat_frac: float = 0.5               # mean motor-saturation fraction
    sat_window_s: float = 1.0
    stale_s: float = 0.5                # telemetry age
    soc_min_pct: float = 30.0           # comment this one  # spec Q5, not PROPOSED
    max_consecutive_aborts: int = 2     # comment this one  # spec Q12, not PROPOSED

@dataclass(frozen=True)
class AbortSample:
    t_s: float                                   # host monotonic time of this step, s
    age_s: float                                 # time since the last telemetry frame arrived, s
    airborne: bool
    pos_m: tuple[float, float, float]
    ref_m: tuple[float, float, float] | None     # None when there is no position reference
    roll_deg: float
    pitch_deg: float
    rate_err_dps: tuple[float, float, float]     # roll, pitch, yaw rate error
    sat_frac: float                              # fraction of the four motors at a limit, 0..1
    safety_trip: int = 0                         # firmware trip code, 0 = none
    soc_pct: float | None = None                 # None when unknown

@dataclass(frozen=True)
class AbortDecision:
    level: int
    reason: str

NO_ABORT = AbortDecision(0, "")
TRIP_NAMES: dict[int, str] = {1: "HEARTBEAT", 2: "LOW_V", 3: "AIRBORNE_CAP", 4: "FENCE", 5: "CEILING", 6: "TILT"}

class AbortMonitor:
    __init__(self, limits: AbortLimits = AbortLimits())
    begin_flight(self) -> None      # clears the latched decision, the hold timers and the windows
    step(self, sample: AbortSample) -> AbortDecision
    end_flight(self) -> None        # aborted flight (latched level >= 1): consecutive count + 1; else count = 0
    reset_campaign(self) -> None    # consecutive count = 0, then the same clearing as begin_flight
    decision (read-only property) -> AbortDecision      # the latched decision
    consecutive_aborts (read-only property) -> int

Behaviour of step(), in this order:
1. Latch. The decision only moves up: once level 1 is latched, later steps return it unchanged unless a
   level-3 condition appears (then level 3 is latched). Level 3 stays until begin_flight or reset_campaign.
   The reason of the first trip at a level is kept.
2. Time. A sample whose t_s is NaN or infinite trips level 1, reason "nonfinite:t_s". A sample whose t_s is
   not greater than the t_s of the previous accepted sample is ignored: return the latched decision and
   change nothing.
3. Level-3 checks (evaluated on every accepted sample, airborne or not, in this order):
   a. safety_trip != 0 -> level 3, reason "firmware:" + TRIP_NAMES.get(code, f"TRIP_{code}").
   b. soc_pct is not None and not (soc_pct >= soc_min_pct) -> level 3, reason "battery". A NaN soc trips.
4. Not airborne: clear the hold timers and the windows, return the latched decision. No level-1 check runs.
5. Level-1 checks (airborne only), first hit wins, in this order:
   a. Non-finite input: any NaN or infinite value in age_s, pos_m, ref_m (when not None), roll_deg,
      pitch_deg, rate_err_dps, sat_frac -> reason "nonfinite:<field>" with the field name as written here
      (for example "nonfinite:pos_m").
   b. Stale: age_s > stale_s -> reason "stale_telemetry". When stale, checks c to f do not run on this
      sample and the sample is not added to the windows.
   c. Tilt: |roll_deg| > tilt_deg or |pitch_deg| > tilt_deg continuously for tilt_hold_s -> reason "tilt".
   d. Position error: ref_m is not None and distance(pos_m, ref_m) > pos_err_m continuously for
      pos_err_hold_s -> reason "position_error". With ref_m None the hold timer clears.
   e. Oscillation: over the samples with t_s in (t_now - osc_window_s, t_now], RMS of rate_err_dps per
      axis; the largest of the three > osc_rms_dps -> reason "oscillation".
   f. Saturation: mean of sat_frac over the samples with t_s in (t_now - sat_window_s, t_now] > sat_frac
      limit -> reason "motor_saturation".
6. Two consecutive aborts: when a level-1 check trips and consecutive_aborts + 1 >= max_consecutive_aborts,
   latch level 3 instead, reason "consecutive_aborts:" + the level-1 reason.

Definitions:
- "Continuously for H": the condition was true on every accepted airborne sample from one with time t0 up
  to now, and t_now - t0 >= H. One sample where the condition is false clears the timer. With H = 0 the
  first true sample trips.
- A window check (e, f) runs only when the oldest sample kept since the windows were last cleared is at
  least the window length old (t_now - t_first >= window). Before that it cannot trip.
- Drop samples older than the window from the deque on every step (bounded memory).
- Comparisons are strict ">" as written; a value equal to the limit does not trip.

Required tests (plain pytest functions; build samples with a small helper `mk(t, **overrides)` that returns
a healthy airborne sample: age 0.0, pos == ref == (0, 0, 0.5), roll/pitch 0, rate error zeros, sat 0.0):
1. Healthy flight: 5 s at 50 Hz, every step returns NO_ABORT; decision is NO_ABORT.
2. Tilt: above the limit for less than tilt_hold_s then back under -> no abort; above for the hold time ->
   level 1 "tilt"; exactly at the limit (equal) never trips; negative roll and pitch both covered.
3. Position error: 0.30 m off for the hold time -> level 1 "position_error"; with ref_m None the same
   positions never trip; an error pulse shorter than the hold does not trip.
4. Oscillation: a +-100 dps square wave on one axis trips "oscillation" only once the window is full (not
   on the early samples); a +-10 dps wave never trips; test each of the three axes.
5. Saturation: sat_frac 1.0 on every sample trips "motor_saturation" once the window is full; 0.4 never
   trips.
6. Stale: age_s 0.6 -> level 1 "stale_telemetry" at once; age_s 0.5 (equal) does not trip.
7. Non-finite: NaN in each of age_s, pos_m, ref_m, roll_deg, pitch_deg, rate_err_dps, sat_frac gives
   "nonfinite:<field>" (parametrize); NaN t_s gives "nonfinite:t_s"; inf covered at least once.
8. Firmware trip: safety_trip 4 -> level 3 "firmware:FENCE" (also when airborne is False);
   safety_trip 9 -> "firmware:TRIP_9".
9. Battery: soc_pct 29.9 -> level 3 "battery"; 30.0 -> no abort; None -> no abort; NaN -> level 3.
10. Latch: after a level-1 trip, healthy samples still return the same decision; a later safety_trip raises
    it to level 3; after level 3 nothing changes it; the first level-1 reason is kept when a second level-1
    condition appears.
11. Not airborne: a tilt of 80 deg with airborne False never trips; timers clear, so airborne tilt must run
    the full hold again afterwards.
12. Time order: a sample with t_s equal to or below the previous one changes nothing (send a tilted
    out-of-order sample during a hold and check the hold still completes at the original time).
13. Consecutive aborts: flight 1 aborts level 1, end_flight -> consecutive_aborts == 1; begin_flight; flight
    2 tilt trip returns level 3 "consecutive_aborts:tilt". Separate case: abort, end_flight, begin_flight,
    clean flight, end_flight -> count 0, and the next abort is level 1 again. reset_campaign sets count 0.
14. begin_flight clears a latched level 3 and the windows (a full saturated window before begin_flight does
    not make the first sample after it trip).
15. Bounded memory: after 100 s at 50 Hz the internal deques hold no more than the window length of samples
    (assert through len on the private deques; name them `_osc` and `_sat`).
16. AbortLimits(), AbortSample and AbortDecision are frozen (assigning a field raises
    dataclasses.FrozenInstanceError).

Acceptance command (run from the repo root until it passes):
python -m pytest ground_station/service/tests/test_abort_monitor.py -q

Digest: write .agent-ops/out/wfb-g10.md with: the files created, the acceptance command and its output
verbatim, every ambiguity you resolved, a line "SUBSTITUTIONS: none" (or what you substituted), and
"NOT RUN:" lines for anything you could not run.
Foreground commands only. Do not commit. Do not leave caches or any other file in the repository.
</task>
