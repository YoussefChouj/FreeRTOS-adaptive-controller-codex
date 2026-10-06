import subprocess
import math
import threading
from dataclasses import dataclass, field
from typing import Callable, Any
from pathlib import Path

from ground_station.autotune import excitation as ex
from ground_station.service.campaign_schema import load_campaign
from ground_station.service.abort_monitor import AbortMonitor, AbortSample, AbortDecision
from ground_station.service.scenario_schema import TRAJ_KINDS, WFB_SETTLE_S, Scenario, Step
from ground_station.livetune.loop import OK_STATUSES as LIVETUNE_OK, LiveTuneSession, parse_step as parse_livetune
from ground_station.service.trajectory_pipeline import TrajLimits
from ground_station.platform.trajectory_upload import upload

# firmware enums (docs/workflow-b/interfaces.md): prim_state IDLE 0 / HOVER 2 / DESCEND 6, traj_state DONE 4
PRIM_IDLE = 0
PRIM_HOVER = 2
PRIM_DESCEND = 6
TRAJ_DONE = 4
# PROPOSED: time for status.motor_idle to read 1 after the IDLE command (stream at >= 10 Hz plus the ack)
IDLE_CONFIRM_S = 1.5
# cooldown passed to the pack gate for a pack that has not flown in this campaign: any min_rest_s passes
PACK_RESTED_S = 1e6

# excite: the drone must be this close to the hover point before the SysID start re-zeroes the origin, within
# EXCITE_ORIGIN_WAIT_S of the step start. PROPOSED, not measured.
EXCITE_ORIGIN_TOL_M = 0.15
EXCITE_ORIGIN_WAIT_S = 5.0
# Pad re-seat gate: the optical-flow position walks at up to this rate (worst body-y bias, 10-03 roam-and-return
# flights, docs/analysis/adaptive-arch-study-2026-10-05.md sec I). The drone stays RC-armed between flights, so the
# origin (and the fence around it) walks with the estimate until the operator re-seats and re-arms on the pad.
OF_DRIFT_CM_S = 1.35

class RunnerControl:
    def __init__(self):
        self._lock = threading.Lock()
        self._req = None

    def request(self, cmd: str):
        if cmd not in {"pause", "land", "abort"}:
            raise ValueError(f"invalid request {cmd!r}")
        with self._lock:
            if self._req == "abort":
                pass
            elif self._req == "land" and cmd == "abort":
                self._req = cmd
            elif self._req == "pause" and cmd in {"abort", "land"}:
                self._req = cmd
            elif self._req is None:
                self._req = cmd

    def get(self) -> str | None:
        with self._lock:
            return self._req

    def clear(self):
        with self._lock:
            self._req = None

@dataclass
class RunnerDeps:
    client: Any
    step: Callable[[float], None]
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    status: Callable[[], dict]
    sample: Callable[[float], 'AbortSample']
    packs: Any
    monitor: Any
    tuner: Any
    gate: Any
    flash: Callable[[], str]
    analyze: Callable[[str], float | None]
    wait_for_go: Callable[[str], bool]
    resting_v: Callable[[str], float]
    arm_allowed: Callable[[], bool] = lambda: False
    change_request: Callable[[], dict | None] = lambda: None
    diff_source: Callable[[], str] | None = None
    repo_root: str = "."
    lkg_commit: str = "HEAD"
    c_files: tuple[str, ...] = ()
    dt_s: float = 0.02
    flight_timeout_s: float = 120.0
    hover_s: float = 5.0
    cooldown_min_s: float = 0.0
    agent_arms: bool = True  # False live: the operator arms by RC, takeoff sends IDLE + TAKEOFF only
    control: RunnerControl | None = None
    apply_params: Callable[[dict], bool] = lambda params: True
    on_flight: Callable[['FlightRecord'], None] = lambda rec: None
    say: Callable[[str], None] = lambda text: None               # chat line to the operator (live: agent message)
    health: Callable[[], tuple[bool, str]] = lambda: (True, "")  # live: RC-armed + g_ekf_of_health
    ground_wait_s: float = 0.0  # fly mode: time on the ground before the auto-next check
    drift_budget_m: float = 0.0  # fly mode: pause for a pad re-seat once airborne s x OF_DRIFT_CM_S since go exceeds it
    # per-flight capture (decision 8): live applies the experiment's log_plan and starts a recording;
    # end_capture stops it and returns the session dir (FlightRecord.recording)
    begin_capture: Callable[[Any, str], Any] = lambda exp, flight_id: None
    end_capture: Callable[[Any], str] = lambda token: ""
    # what the runner is doing right now, one line (campaign_state "phase"): the agent reads a wait from one call
    on_phase: Callable[[str], None] = lambda text: None
    on_livetune: Callable[[dict], None] = lambda result: None  # livetune step result (best gains, J history, CMA state)


def _gate_problem(status: dict) -> str:
    """Why the firmware TAKEOFF gate (API/wfb_glue.c:109) would refuse, from the streamed inputs; "" if none seen."""
    if "motor_idle" in status and status["motor_idle"] != 1:
        return "motors left idle before TAKEOFF (status.motor_idle 0: stick takeover, disarm, or the FSM changed state)"
    if status.get("sbus_lost") == 1:
        return "RC link lost (status.sbus_lost 1)"
    return ""


def _takeover_problem(auth_at_idle, status: dict) -> str:
    """The flymode 1 IDLE takes the stick authority (firmware 0e5beae); losing it before TAKEOFF is a pilot takeover.
    The wfb TAKEOFF gate does not see it (the protected wfb_apply flies the setpoints anyway), so refuse here."""
    if auth_at_idle == 1 and status.get("rc_authority") == 0:
        return "pilot took the sticks during idle (status.rc_authority 1 -> 0: a fast stick move)"
    return ""

def default_diff_source(repo_root: str, lkg_commit: str, c_files: tuple[str, ...]) -> str:
    if not c_files:
        return ""
    cmd = ["git", "diff", lkg_commit, "--"] + list(c_files)
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=False)
    return res.stdout

def files_after_from_diff(diff_text: str, repo_root: str) -> dict[str, str]:
    files = {}
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path == "/dev/null":
                continue
            full_path = Path(repo_root) / path
            try:
                files[path] = full_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                files[path] = ""
    return files

@dataclass
class FlightRecord:
    flight_id: str
    pack_id: str
    experiment: str
    j: float | None
    abort_level: int
    abort_reason: str
    decision: str
    hover_only: bool
    duration_s: float
    reflash_hash: str = ""
    scenario: str = ""
    steps: list[dict] = field(default_factory=list)
    recording: str = ""

@dataclass
class CampaignReport:
    campaign: Any
    flights: list[FlightRecord]
    status: str
    reason: str

@dataclass
class FlightOutcome:
    aborted: bool
    decision: AbortDecision | None
    landed: bool
    steps: list[dict]


def fly_scenario(scenario: Scenario, deps: RunnerDeps, hover_only: bool = False) -> FlightOutcome:
    """Fly one scenario: takeoff, each step in order, land. Any abort -> traj_stop + land.

    takeoff: set_hover_z + arm/idle/takeoff, wait prim HOVER. hold: heartbeat for s.
    goto/path: settle WFB_SETTLE_S in HOVER, upload step.points, traj_start, wait traj DONE and prim back in HOVER.
    livetune: hold HOVER while a livetune.loop session tunes gains (ticked every cfg.dt_s); it always ends on the
    baseline gains, and an end other than budget / max_evals / walk_budget lands the drone.
    hover_only (first flight after a reflash) keeps takeoff and land and swaps the middle for hold deps.hover_s.
    The whole flight shares one deadline, deps.flight_timeout_s; a firmware landing (prim DESCEND/IDLE we did not
    command) aborts the step. Each step gets a record {step, kind, t0_s, t1_s, ok} relative to the takeoff command.
    """
    steps = list(scenario.steps)
    if hover_only:
        steps = [steps[0], Step("hold", {"s": deps.hover_s}, duration_s=deps.hover_s), steps[-1]]
    max_ticks = math.ceil(deps.flight_timeout_s / deps.dt_s)
    t_start = deps.clock()
    records: list[dict] = []
    ticks = 0
    airborne = excite_sent = False
    decision: AbortDecision | None = None

    def prim() -> int:
        return int(deps.status()["prim_state"])

    def wait(cond: Callable[[], bool], phase: str, dt_s: float | None = None) -> bool:
        nonlocal ticks, decision
        dt = dt_s or deps.dt_s
        while not cond():
            req = deps.control.get() if deps.control else None
            if req in ("land", "abort"):
                decision = AbortDecision(level=1, reason=f"operator {req}")
                return False
            if ticks >= max_ticks:
                decision = AbortDecision(level=1, reason=f"timeout: {phase}")
                return False
            if airborne and prim() in (PRIM_DESCEND, PRIM_IDLE):
                trip = int(deps.status().get("safety_trip", 0))
                decision = AbortDecision(level=1, reason=f"firmware landing during {phase} (safety_trip {trip})")
                return False
            deps.client.heartbeat()
            deps.step(dt)
            dec = deps.monitor.step(deps.sample(deps.clock()))
            if dec.level >= 1:
                decision = dec
                return False
            ticks += dt / deps.dt_s
        return True

    for i, st in enumerate(steps[:-1]):
        t0 = deps.clock() - t_start
        extra: dict = {}
        if st.kind == "takeoff":
            cmds = []
            if "mrac_injection" in st.args:
                inj = float(st.args["mrac_injection"])
                cmds.append(("mrac_injection",
                             lambda: deps.client._send_cmd(ex.CMD_MRAC_FLAGS, ex.MRAC_IDX_INJECTION, inj)))
            cmds.append(("set_hover_z", lambda: deps.client.set_hover_z(scenario.hover_z_m)))
            if deps.agent_arms:
                cmds.append(("arm", deps.client.arm))
            cmds.append(("idle", deps.client.idle))
            refused = next((name for name, send in cmds if not send()), None)
            # IDLE is acked even when the firmware ignores it (send_data.c CMD_ARM idx 1 needs ARMED, on the ground,
            # throttle stick below RC_IDLE_THR_THRESHOLD; since 0e5beae flymode 1 needs no stick); TAKEOFF then fails the wfb
            # gate as STATE. Fail now instead.
            gate_why = ""
            t_idle = deps.clock()
            if not refused and "motor_idle" in deps.status():
                while deps.status().get("motor_idle") != 1 and deps.clock() - t_idle < IDLE_CONFIRM_S:
                    deps.sleep(deps.dt_s)
                if deps.status().get("motor_idle") != 1:
                    refused, gate_why = "idle", "motors did not go to idle (status.motor_idle 0): flymode 1 (SDK) and firmware 0e5beae or later"
            auth_idle = deps.status().get("rc_authority")
            # idle_s: props spin at idle on the ground before TAKEOFF (operator 10-06: spool-up too short)
            idle_s = float(st.args.get("idle_s", 0.0))
            t_idle = deps.clock()
            if not refused and idle_s > 0 and not wait(lambda: deps.clock() - t_idle >= idle_s, "idle"):
                ok = False
            elif (refused or (gate_why := _gate_problem(deps.status()) or _takeover_problem(auth_idle, deps.status()))
                  or not deps.client.takeoff()):
                refused = refused or "takeoff"
                why = gate_why or getattr(deps.client, "last_error", "") or "not applied"
                hint = " (operator: flymode 1, arm by RC, hands off the sticks)" if refused == "idle" and not deps.agent_arms else ""
                decision = AbortDecision(level=1, reason=f"takeoff refused at {refused}: {why}{hint}")
                ok = False
            else:
                extra = {"idle_s": idle_s} if idle_s > 0 else {}
                ok = airborne = wait(lambda: prim() == PRIM_HOVER, "takeoff")
        elif st.kind == "hold":
            t_hold = deps.clock()
            ok = wait(lambda: deps.clock() - t_hold >= st.duration_s, "hold")
        elif st.kind in TRAJ_KINDS:
            t_settle = deps.clock()
            ok = wait(lambda: deps.clock() - t_settle >= WFB_SETTLE_S and prim() == PRIM_HOVER, f"{st.kind} settle")
            if ok:
                res = upload(st.points, deps.client)
                if not res.ok:
                    decision = AbortDecision(level=1, reason=f"{st.kind} upload failed: {res.error}")
                    ok = False
                else:
                    deps.client.traj_start()
                    ok = wait(
                        lambda: int(deps.status()["traj_state"]) == TRAJ_DONE and prim() == PRIM_HOVER, st.kind
                    )
        elif st.kind == "excite":
            # The start re-zeroes the OF origin: send it only while holding at the hover point (schema: after a hold).
            t_ex = deps.clock()
            ok = wait(lambda: deps.clock() - t_ex >= EXCITE_ORIGIN_WAIT_S or (
                prim() == PRIM_HOVER and math.hypot(*deps.sample(deps.clock()).pos_m[:2]) <= EXCITE_ORIGIN_TOL_M),
                "excite origin")
            xy = deps.sample(deps.clock()).pos_m[:2]
            if ok and (prim() != PRIM_HOVER or math.hypot(*xy) > EXCITE_ORIGIN_TOL_M):
                decision = AbortDecision(level=1, reason=f"excite: not holding at the hover point (x {xy[0]:.2f}, "
                                                         f"y {xy[1]:.2f} m, tol {EXCITE_ORIGIN_TOL_M} m)")
                ok = False
            if ok:
                a = st.args
                excite_sent = True
                cmds = ex.start_commands(a["axis"], a["signal"], a["f0"], a["f1"], a["amp"], a["duration_s"])
                if not all(deps.client._send_cmd(ex.CMD_SYSID, idx, val) for idx, val in cmds):
                    why = getattr(deps.client, "last_error", "") or "not applied"
                    decision = AbortDecision(level=1, reason=f"excite: sysid command refused: {why}")
                    ok = False
                else:
                    # Abort after the window is a no-op if SysID already finished (sysid.c:226); then wait RECOVERY.
                    t_on = deps.clock()
                    ok = wait(lambda: deps.clock() - t_on >= ex.active_s(a["duration_s"]), "excite")
                    if ok:
                        deps.client._send_cmd(ex.CMD_SYSID, ex.IDX_START, 0.0)
                        ok = wait(lambda: deps.clock() - t_on >= ex.step_s(a["duration_s"]), "excite recovery")
        elif st.kind == "livetune":
            lim = TrajLimits()
            cfg, errs = parse_livetune(st.args, (0.0, 0.0, scenario.hover_z_m), (lim.x_abs_m, lim.y_abs_m))
            if errs:
                raise ValueError(f"step {i}: livetune: {'; '.join(errs)}")
            # CMD 0x01 / 0x0F / 0x14 go through the client's transaction path (WfbClient and LiveWfbClient)
            session = LiveTuneSession(cfg, deps.client._send_cmd, deps.clock, deps.say)
            try:
                ok = wait(lambda: session.tick(deps.sample(deps.clock()), prim()), "livetune", cfg.dt_s)
            finally:
                session.close(decision.reason if decision else "")
            ok = ok and session.result.status in LIVETUNE_OK
            extra = {"livetune": session.result.summary()}
            deps.on_livetune(extra["livetune"])
        else:
            raise ValueError(f"step {i}: unexpected kind {st.kind!r} before land")
        records.append({"step": i, "kind": st.kind, "t0_s": round(t0, 3),
                        "t1_s": round(deps.clock() - t_start, 3), "ok": ok, **extra})
        if not ok:
            break

    aborted = decision is not None
    if aborted:
        if excite_sent:
            deps.client._send_cmd(ex.CMD_SYSID, ex.IDX_START, 0.0)  # SysID abort -> RECOVERY
        deps.client.traj_stop()
    t0 = deps.clock() - t_start
    land_sent = deps.client.land()
    landed = False
    for _ in range(max_ticks):
        deps.client.heartbeat()
        deps.step(deps.dt_s)
        if prim() == PRIM_IDLE:
            landed = True
            break
        if not land_sent and prim() == PRIM_HOVER:  # LAND lost or refused while still hovering: resend
            land_sent = deps.client.land()
    records.append({"step": len(steps) - 1, "kind": "land", "t0_s": round(t0, 3),
                    "t1_s": round(deps.clock() - t_start, 3), "ok": landed})
    return FlightOutcome(aborted, decision, landed, records)


def _control_report(deps: RunnerDeps, campaign: Any, flights: list[FlightRecord]) -> CampaignReport | None:
    req = deps.control.get() if deps.control else None
    if req == "abort":
        return CampaignReport(campaign, flights, "operator_needed", "operator abort")
    elif req == "land":
        return CampaignReport(campaign, flights, "operator_stop", "operator land")
    elif req == "pause":
        return CampaignReport(campaign, flights, "operator_stop", "operator pause")
    return None

def run_campaign(yaml_path: str, deps: RunnerDeps) -> CampaignReport:
    campaign = load_campaign(yaml_path)
    if campaign.abort:  # the campaign's abort overrides (e.g. autotune tilt_deg 15) reach the live monitor
        deps.monitor = AbortMonitor(limits=campaign.abort_limits)
    queue = []
    for exp in campaign.experiments:
        for _ in range(exp.repeats):
            queue.append(exp)
            
    flights = []
    last_landing_t = deps.clock()
    # pack gate rest is per pack and only after that pack flew: a fresh pack is rested (before WP-23 the first
    # flight waited min_rest_s after Go with the drone RC-armed on the pad)
    pack_landed_t: dict[str, float] = {}
    last_duration = 0.0
    fw_hash = ""
    has_flashed = False
    history = []
    runner_consecutive_aborts = 0
    judge = None
    
    max_ticks = math.ceil(deps.flight_timeout_s / deps.dt_s)
    
    fly = campaign.mode == "fly"
    n_flights = min(campaign.max_flights, len(queue)) if fly else campaign.max_flights
    need_go = True
    prev_pack = None
    drift_m = 0.0  # worst-case origin walk since the last go (the operator re-seats before a go)

    for i in range(n_flights):
        exp = queue[i % len(queue)] if queue else None
        if not exp:
            break
        pack_id = campaign.packs[i % len(campaign.packs)]
        flight_id = f"{campaign.campaign}-{i+1:03d}"
        
        report = _control_report(deps, campaign, flights)
        if report:
            return report

        # 1. wait_for_go (fly mode: once per pack, again only after a failed auto-next check)
        need_go = need_go or not fly or pack_id != prev_pack
        prev_pack = pack_id
        if need_go:
            deps.on_phase(f"waiting for go: pack {pack_id}, flight {i + 1}/{n_flights}")
        if need_go and not deps.wait_for_go(pack_id):
            report = _control_report(deps, campaign, flights)
            if report:
                return report
            return CampaignReport(campaign, flights, "operator_stop", "go wait ended without a go")
        if need_go:
            drift_m = 0.0

        # 2. Cooldown
        target_elapsed = deps.cooldown_min_s if fly else max(last_duration, deps.cooldown_min_s)
        cooldown_max_ticks = math.ceil(target_elapsed / deps.dt_s) + max_ticks

        ticks = 0
        if deps.clock() - last_landing_t < target_elapsed:
            deps.on_phase(f"motor cooldown {target_elapsed:.0f} s before flight {i + 1}/{n_flights}")
        while deps.clock() - last_landing_t < target_elapsed and ticks < cooldown_max_ticks:
            deps.sleep(deps.dt_s)
            ticks += 1

        if deps.clock() - last_landing_t < target_elapsed:
            return CampaignReport(campaign, flights, "operator_needed",
                                  f"cooldown: {deps.clock() - last_landing_t:.1f} s since landing < {target_elapsed:.1f} s")

        ticks = 0
        allowed = False
        reason = ""
        while ticks < max_ticks:
            elapsed = deps.clock() - pack_landed_t[pack_id] if pack_id in pack_landed_t else PACK_RESTED_S
            try:
                v = deps.resting_v(pack_id)
            except Exception as exc:  # live: battery voltage not streaming
                return CampaignReport(campaign, flights, "operator_needed", f"battery: {exc}")
            allowed, reason = deps.packs.next_flight_allowed(pack_id, v, elapsed)
            if allowed:
                break
            shown_v = round(v, 2) if isinstance(v, (int, float)) else v
            deps.on_phase(f"pack gate {pack_id} (resting {shown_v} V): {reason}; waiting")
            deps.sleep(deps.dt_s)
            ticks += 1

        if not allowed:
            return CampaignReport(campaign, flights, "operator_needed", f"pack {pack_id}: {reason}")
            
        # 3. Code change (tune mode only)
        just = deps.change_request() if not fly else None
        if just is not None:
            diff_text = deps.diff_source() if deps.diff_source else default_diff_source(deps.repo_root, deps.lkg_commit, deps.c_files)
            files = files_after_from_diff(diff_text, deps.repo_root)
            res = deps.gate.check_change(diff_text, just, files)
            if not res.ok:
                return CampaignReport(campaign, flights, "gate_refused", "; ".join(res.reasons))
            fw_hash = deps.flash()
            has_flashed = True
            judge = "hover"
        else:
            if not has_flashed:
                fw_hash = ""
                
        # 4. hover_only
        hover_only = deps.gate.next_flight_must_hover() if not fly else False
        
        # 5. arm_allowed
        deps.on_phase(f"stream check before flight {i + 1}/{n_flights}")
        if not deps.arm_allowed():
            return CampaignReport(campaign, flights, "arm_refused", "stream check failed before takeoff")
            
        # 6. Flight
        params = deps.tuner.propose(history) if not fly else {}
        if params and not deps.apply_params(params):
            return CampaignReport(campaign, flights, "operator_needed", "param write refused")

        try:
            capture = deps.begin_capture(exp, flight_id)
        except Exception as exc:  # no log plan on the stream: do not fly unlogged
            return CampaignReport(campaign, flights, "operator_needed", f"capture: {exc}")
        deps.monitor.begin_flight()
        deps.on_phase(f"flying flight {i + 1}/{n_flights}: {exp.name}")
        flight_start_time = deps.clock()
        try:
            outcome = fly_scenario(exp.scenario, deps, hover_only)
        finally:
            recording = deps.end_capture(capture) or ""
        aborted, decision, landed = outcome.aborted, outcome.decision, outcome.landed

        deps.monitor.end_flight()
        last_landing_t = pack_landed_t[pack_id] = deps.clock()
        last_duration = last_landing_t - flight_start_time
        drift_m += last_duration * OF_DRIFT_CM_S / 100.0
        
        # 8. Analyze
        j = deps.analyze(flight_id) if not aborted else None
        if not fly:
            deps.tuner.record(params, j, valid=j is not None)
        history.append({"params": params, "J": j, "valid": j is not None})
        
        if aborted:
            runner_consecutive_aborts += 1
        else:
            runner_consecutive_aborts = 0
            
        # 9. Gate
        gate_decision = ""
        if judge == "hover":
            gate_decision = deps.gate.on_flight_result(aborted=aborted, j=j, hover=True)
            if gate_decision == "revert":
                judge = None
            else:
                judge = "traj"
        elif judge == "traj":
            gate_decision = deps.gate.on_flight_result(aborted=aborted, j=j)
            judge = None

        if not fly:
            deps.gate.record_flight(flight_id, fw_hash)
        
        decision_was_revert = (gate_decision == "revert")

        rec = FlightRecord(
            flight_id=flight_id,
            pack_id=pack_id,
            experiment=exp.name,
            j=j,
            abort_level=decision.level if aborted and decision else 0,
            abort_reason=decision.reason if aborted and decision else "",
            decision=gate_decision,
            hover_only=hover_only,
            duration_s=last_duration,
            reflash_hash="",
            scenario=exp.scenario.name,
            steps=outcome.steps,
            recording=str(recording),
        )

        if not landed:
            flights.append(rec)
            deps.on_flight(rec)
            if decision_was_revert:
                return CampaignReport(campaign, flights, "operator_needed", "landing timeout; revert flash pending")
            return CampaignReport(campaign, flights, "operator_needed", "landing timeout")

        if decision_was_revert:
            rec.reflash_hash = fw_hash = deps.flash()
            has_flashed = True

        flights.append(rec)
        deps.on_flight(rec)
            
        report = _control_report(deps, campaign, flights)
        if report:
            return report
        
        # 10. Abort limit
        if (aborted and decision and decision.level >= 3) or max(runner_consecutive_aborts, deps.monitor.consecutive_aborts) >= 2:
            return CampaignReport(campaign, flights, "operator_needed", "abort level 3 or consecutive")

        # 11. Auto-next (fly mode): wait on the ground, post a 3-line result, fly on only if every check passes
        if fly:
            nxt = queue[i + 1] if i + 1 < n_flights else None
            deps.on_phase(f"on the ground after flight {i + 1}/{n_flights}: auto-next check")
            ok, why = _auto_next(deps, outcome)
            if ok and nxt is not None and 0.0 < deps.drift_budget_m < drift_m:
                ok, why = False, (f"re-seat: origin may have walked {drift_m:.2f} m (budget {deps.drift_budget_m:.2f} m, "
                                  f"{OF_DRIFT_CM_S} cm/s worst flow bias). Disarm, put the drone on the pad marker, "
                                  f"re-arm by RC (arming re-zeroes the origin)")
            if nxt is not None and nxt is not exp and nxt.go_before:
                ok, why = False, (nxt.go_before if ok else f"{why}; also: {nxt.go_before}")
            report = _control_report(deps, campaign, flights)
            if report:
                return report
            deps.say(_result_lines(rec, i + 1, n_flights, nxt, ok, why))
            need_go = not ok

    return CampaignReport(campaign, flights, "complete", "")


def _auto_next(deps: RunnerDeps, outcome: FlightOutcome) -> tuple[bool, str]:
    """Decision 12: landed, idle, no trip/abort, deps.health() (RC-armed, KF healthy). A recovered fence
    push-back is not an abort; a firmware landing is (fly_scenario aborts on prim DESCEND/IDLE it did not command)."""
    ticks = math.ceil(deps.ground_wait_s / deps.dt_s) if deps.ground_wait_s > 0 else 0
    for _ in range(ticks):
        req = deps.control.get() if deps.control else None
        if req is not None:
            return False, f"operator {req}"
        deps.client.heartbeat()
        deps.sleep(deps.dt_s)
    if outcome.aborted:
        return False, f"flight aborted: {outcome.decision.reason if outcome.decision else 'unknown'}"
    st = deps.status()
    prim = int(st.get("prim_state", -1))
    if prim != PRIM_IDLE:
        return False, f"not idle on the ground (prim_state {prim})"
    if int(st.get("safety_trip", 0)):
        return False, f"safety_trip {int(st['safety_trip'])}"
    return deps.health()


def _result_lines(rec: FlightRecord, n: int, total: int, nxt: Any, ok: bool, why: str) -> str:
    """The 3-line chat result after each fly-mode flight: what flew, how each step went, what happens next."""
    steps = ", ".join(f"{s.get('kind', '?')} {'ok' if s.get('ok') else 'FAIL'}" for s in rec.steps) or "none"
    abort = f"abort L{rec.abort_level}: {rec.abort_reason}" if rec.abort_level else "landed, no abort"
    if nxt is None:
        tail = "campaign done" if ok else f"campaign done; post-flight check failed: {why}"
    elif ok:
        tail = f"auto-next OK -> flight {n + 1}/{total} ({nxt.name})"
    else:
        tail = f"PAUSED before flight {n + 1}/{total}: {why}. Say go to continue, or land/abort"
    return (f"flight {n}/{total} {rec.experiment} ({rec.scenario}): {rec.duration_s:.1f} s, {abort}\n"
            f"steps: {steps}\n"
            f"{tail}")
