import subprocess
from dataclasses import dataclass
from typing import Callable, Any, Optional
from pathlib import Path

from ground_station.service.campaign_schema import load_campaign, Campaign, Experiment
from ground_station.service.abort_monitor import AbortSample, AbortMonitor
from ground_station.service.trajectory_pipeline import generate
from ground_station.platform.trajectory_upload import upload

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
    experiment: Any
    j: float | None
    abort_level: int
    abort_reason: str
    decision: str
    hover_only: bool
    duration_s: float

@dataclass
class CampaignReport:
    campaign: Any
    flights: list[FlightRecord]
    status: str
    reason: str

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
    
    for i in range(campaign.max_flights):
        exp = queue[i % len(queue)] if queue else None
        if not exp:
            break
        pack_id = campaign.packs[i % len(campaign.packs)]
        flight_id = f"{campaign.campaign}-{i+1:03d}"
        
        # 1. wait_for_go
        if not deps.wait_for_go(pack_id):
            return CampaignReport(campaign, flights, "operator_stop", "")
            
        # 2. Cooldown
        target_elapsed = max(last_duration, deps.cooldown_min_s)
        while deps.clock() - last_landing_t < target_elapsed:
            deps.sleep(deps.dt_s)
        
        wait_start = deps.clock()
        while True:
            elapsed = deps.clock() - last_landing_t
            allowed, reason = deps.packs.next_flight_allowed(pack_id, deps.resting_v(pack_id), elapsed)
            if allowed:
                break
            if deps.clock() - wait_start > deps.flight_timeout_s:
                return CampaignReport(campaign, flights, "operator_needed", reason)
            deps.sleep(deps.dt_s)
            
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
        deps.monitor.begin_flight()
        flight_start_time = deps.clock()
        deps.client.set_hover_z(exp.profile.hover_z_m)
        deps.client.arm()
        deps.client.idle()
        deps.client.takeoff()
        
        aborted = False
        decision = None
        
        def run_loop_until(cond):
            nonlocal aborted, decision
            while not cond():
                deps.client.heartbeat()
                deps.step(deps.dt_s)
                dec = deps.monitor.step(deps.sample(deps.clock()))
                if dec.level >= 1:
                    aborted = True
                    decision = dec
                    break
                    
        run_loop_until(lambda: deps.status()["prim_state"] == 2)
        
        if not aborted:
            if not hover_only:
                points = generate(exp.shape, exp.params, exp.profile)
                upload(points, deps.client)
                deps.client.traj_start()
                run_loop_until(lambda: deps.status()["traj_state"] == 4)
            else:
                hover_start = deps.clock()
                run_loop_until(lambda: deps.clock() - hover_start >= deps.hover_s)
                
        # 7. Land
        if aborted:
            deps.client.traj_stop()
        deps.client.land()
        
        start_landing = deps.clock()
        while deps.status()["prim_state"] != 0:
            deps.client.heartbeat()
            deps.step(deps.dt_s)
            if deps.clock() - start_landing > deps.flight_timeout_s:
                break
                
        deps.monitor.end_flight()
        last_landing_t = deps.clock()
        last_duration = last_landing_t - flight_start_time
        
        # 8. Analyze
        j = deps.analyze(flight_id) if not aborted else None
        deps.tuner.record(params, j, valid=j is not None)
        
        # 9. Gate
        gate_decision = deps.gate.on_flight_result(aborted=aborted, j=j)
        deps.gate.record_flight(flight_id, fw_hash)
        
        flights.append(FlightRecord(
            flight_id=flight_id,
            pack_id=pack_id,
            experiment=exp,
            j=j,
            abort_level=decision.level if aborted and decision else 0,
            abort_reason=decision.reason if aborted and decision else "",
            decision=gate_decision,
            hover_only=hover_only,
            duration_s=last_duration
        ))
        
        # 10. Abort limit
        if (aborted and decision and decision.level >= 3) or deps.monitor.consecutive_aborts >= 2:
            return CampaignReport(campaign, flights, "operator_needed", "abort level 3 or consecutive")
            
    return CampaignReport(campaign, flights, "complete", "")
