import threading
import time
import dataclasses
from ground_station.service.campaign_runner import RunnerControl, run_campaign
from ground_station.service.agent import AgentDisabledError, PlanBusyError

class CampaignService:
    def __init__(self, agent=None, deps_factory=None, knobs=(), param_timeout_s=30.0):
        self.agent = agent
        self.deps_factory = deps_factory
        self.knobs = knobs
        self.param_timeout_s = param_timeout_s
        self.lock = threading.RLock()
        
        self.runner_thread = None
        self.control = RunnerControl()
        self.deps = None
        self.campaign_path = None
        self.waiting_pack = None
        self.report = None
        
        self.condition = threading.Condition(self.lock)
        self.go_grant = None

    def go(self, campaign_path, pack_id, checklist, source):
        if not source:
            return 400, {"error": "source missing"}
        if source.startswith("agent:"):
            return 403, {"error": "agent cannot start campaign"}
            
        if not isinstance(checklist, dict) or not checklist or not all(v is True for v in checklist.values()):
            return 409, {"error": "checklist incomplete"}
            
        if self.deps_factory is None:
            return 503, {"error": "deps_factory None"}
            
        with self.lock:
            if self.runner_thread and self.runner_thread.is_alive():
                self.go_grant = pack_id
                self.condition.notify_all()
                return 200, self.state()
                
            self.campaign_path = campaign_path
            self.control = RunnerControl()
            self.report = None
            self.waiting_pack = None
            self.go_grant = pack_id
            
            deps = self.deps_factory()
            self.deps = dataclasses.replace(
                deps,
                wait_for_go=self.wait_for_go,
                arm_allowed=self.arm_allowed,
                control=self.control,
                apply_params=self.apply_params
            )
            
            self.runner_thread = threading.Thread(
                target=self._run_wrapper,
                args=(campaign_path, self.deps),
                daemon=True
            )
            self.runner_thread.start()
            
            return 200, self.state()

    def _run_wrapper(self, campaign_path, deps):
        report = run_campaign(campaign_path, deps)
        with self.lock:
            self.report = report

    def wait_for_go(self, pack_id):
        with self.condition:
            if self.go_grant == pack_id:
                self.go_grant = None
                return True
                
            self.waiting_pack = pack_id
            while True:
                if self.go_grant == pack_id:
                    self.go_grant = None
                    self.waiting_pack = None
                    return True
                if self.control.get() is not None:
                    self.waiting_pack = None
                    return False
                self.condition.wait()

    def arm_allowed(self):
        return bool(self.agent is not None and self.agent.allow_agent_arm)

    def apply_params(self, params):
        if not params:
            return True
            
        knob_map = {k.symbol: k for k in self.knobs}
        steps = []
        for sym, val in params.items():
            if sym not in knob_map:
                return False
            k = knob_map[sym]
            steps.append({
                "action": "command",
                "args": {
                    "command_id": k.cmd_id,
                    "index": k.idx,
                    "value": val
                }
            })
            
        if self.agent is None:
            return False
            
        try:
            plan = self.agent.create_plan({
                "title": "campaign params",
                "source": "agent:campaign",
                "steps": steps
            })
        except (AgentDisabledError, PlanBusyError, ValueError):
            return False
            
        plan_id = getattr(plan, "id", None)
                
        if not plan_id:
            return False
            
        start_t = time.time()
        while time.time() - start_t < self.param_timeout_s:
            if self.control.get() in ("land", "abort"):
                return False
                
            st = self.agent.get_plan(plan_id).get("status")
            if st == "done":
                return True
            elif st in ("failed", "cancelled"):
                return False
                
            time.sleep(0.1)
            
        return False

    def request(self, cmd):
        with self.lock:
            if not (self.runner_thread and self.runner_thread.is_alive()):
                return 409, {"error": "no active run"}
                
            self.control.request(cmd)
            self.condition.notify_all()
            return 200, {"ok": True}

    def state(self):
        with self.lock:
            if self.runner_thread and self.runner_thread.is_alive():
                if self.waiting_pack:
                    st = "waiting_for_go"
                else:
                    st = "running"
            else:
                st = self.report.status if self.report else "idle"
                
            flights = []
            if self.report:
                flights = [dataclasses.asdict(f) for f in self.report.flights]
                
            return {
                "status": st,
                "waiting_pack": self.waiting_pack,
                "campaign_path": self.campaign_path if (self.runner_thread and self.runner_thread.is_alive()) or self.report else None,
                "reason": self.report.reason if self.report else "",
                "flights": flights,
                "control": self.control.get() if self.runner_thread and self.runner_thread.is_alive() else None
            }
