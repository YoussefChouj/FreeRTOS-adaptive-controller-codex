import subprocess
import math
import threading
from dataclasses import dataclass
from typing import Callable, Any
from pathlib import Path

from ground_station.service.campaign_schema import load_campaign
from ground_station.service.abort_monitor import AbortSample, AbortDecision
from ground_station.service.trajectory_pipeline import generate
from ground_station.platform.trajectory_upload import upload

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
    control: RunnerControl | None = None
    apply_params: Callable[[dict], bool] = lambda params: True
    on_flight: Callable[['FlightRecord'], None] = lambda rec: None

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

@dataclass
class CampaignReport:
    campaign: Any
    flights: list[FlightRecord]
    status: str
    reason: str

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
    queue = []
    for exp in campaign.experiments:
        for _ in range(exp.repeats):
            queue.append(exp)
            
    flights = []
    last_landing_t = deps.clock()
    last_duration = 0.0
    fw_hash = ""
    has_flashed = False
    history = []
    runner_consecutive_aborts = 0
    judge = None
    
    max_ticks = math.ceil(deps.flight_timeout_s / deps.dt_s)
    
    for i in range(campaign.max_flights):
        exp = queue[i % len(queue)] if queue else None
        if not exp:
            break
        pack_id = campaign.packs[i % len(campaign.packs)]
        flight_id = f"{campaign.campaign}-{i+1:03d}"
        
        report = _control_report(deps, campaign, flights)
        if report:
            return report

        # 1. wait_for_go
        if not deps.wait_for_go(pack_id):
            report = _control_report(deps, campaign, flights)
            if report:
                return report
            return CampaignReport(campaign, flights, "operator_stop", "")
            
        # 2. Cooldown
        target_elapsed = max(last_duration, deps.cooldown_min_s)
        cooldown_max_ticks = math.ceil(target_elapsed / deps.dt_s) + max_ticks
        
        ticks = 0
        while deps.clock() - last_landing_t < target_elapsed and ticks < cooldown_max_ticks:
            deps.sleep(deps.dt_s)
            ticks += 1
            
        if deps.clock() - last_landing_t < target_elapsed:
            return CampaignReport(campaign, flights, "operator_needed", "cooldown not reached")
            
        ticks = 0
        allowed = False
        reason = ""
        while ticks < max_ticks:
            elapsed = deps.clock() - last_landing_t
            allowed, reason = deps.packs.next_flight_allowed(pack_id, deps.resting_v(pack_id), elapsed)
            if allowed:
                break
            deps.sleep(deps.dt_s)
            ticks += 1
            
        if not allowed:
            return CampaignReport(campaign, flights, "operator_needed", reason)
            
        # 3. Code change
        just = deps.change_request()
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
        hover_only = deps.gate.next_flight_must_hover()
        
        # 5. arm_allowed
        if not deps.arm_allowed():
            return CampaignReport(campaign, flights, "arm_refused", "")
            
        # 6. Flight
        params = deps.tuner.propose(history)
        if not deps.apply_params(params):
            return CampaignReport(campaign, flights, "operator_needed", "param write refused")

        deps.monitor.begin_flight()
        flight_start_time = deps.clock()
        deps.client.set_hover_z(exp.profile.hover_z_m)
        deps.client.arm()
        deps.client.idle()
        deps.client.takeoff()
        
        aborted = False
        decision = None
        
        def run_loop_until(cond, phase):
            nonlocal aborted, decision
            ticks = 0
            while not cond():
                if deps.control:
                    req = deps.control.get()
                    if req in ("land", "abort"):
                        aborted = True
                        decision = AbortDecision(level=1, reason=f"operator {req}")
                        return False
                if ticks >= max_ticks:
                    aborted = True
                    decision = AbortDecision(level=1, reason=f"timeout: {phase}")
                    return False
                deps.client.heartbeat()
                deps.step(deps.dt_s)
                dec = deps.monitor.step(deps.sample(deps.clock()))
                if dec.level >= 1:
                    aborted = True
                    decision = dec
                    return False
                ticks += 1
            return True
                    
        run_loop_until(lambda: deps.status()["prim_state"] == 2, "takeoff")
        
        if not aborted:
            if not hover_only:
                points = generate(exp.shape, exp.params, exp.profile)
                upload(points, deps.client)
                deps.client.traj_start()
                run_loop_until(lambda: deps.status()["traj_state"] == 4, "trajectory")
            else:
                hover_start = deps.clock()
                run_loop_until(lambda: deps.clock() - hover_start >= deps.hover_s, "hover")
                
        # 7. Land
        if aborted:
            deps.client.traj_stop()
        deps.client.land()
        
        ticks = 0
        landed = False
        while ticks < max_ticks:
            deps.client.heartbeat()
            deps.step(deps.dt_s)
            if deps.status()["prim_state"] == 0:
                landed = True
                break
            ticks += 1
            
        deps.monitor.end_flight()
        last_landing_t = deps.clock()
        last_duration = last_landing_t - flight_start_time
        
        # 8. Analyze
        j = deps.analyze(flight_id) if not aborted else None
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
            reflash_hash=""
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
            
    return CampaignReport(campaign, flights, "complete", "")
