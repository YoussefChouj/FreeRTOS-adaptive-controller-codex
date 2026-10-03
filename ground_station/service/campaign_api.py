import threading
import time
import dataclasses
import json
from pathlib import Path
from ground_station.service.campaign_runner import RunnerControl, run_campaign, CampaignReport
from ground_station.service.agent import AgentDisabledError, PlanBusyError

class CampaignService:
    def __init__(self, agent=None, deps_factory=None, knobs=(), param_timeout_s=30.0, stream_check=None,
                 go_log_path=None):
        self.agent = agent
        self.deps_factory = deps_factory
        self.knobs = knobs
        self.param_timeout_s = param_timeout_s
        self.stream_check = stream_check
        self.lock = threading.RLock()
        
        self.runner_thread = None
        self.control = RunnerControl()
        self.deps = None
        self.campaign_path = None
        self.waiting_pack = None
        self.report = None
        self._live_flights = []
        self.arm_refusal = None
        
        self.condition = threading.Condition(self.lock)
        self.go_grant = None
        # every accepted Go, with who gave it; an agent Go carries the operator's chat confirmation quote
        self.go_log_path = Path(go_log_path) if go_log_path else None
        self.go_log = []

    def go(self, campaign_path, pack_id, checklist, source, confirmation=None):
        if not source:
            return 400, {"error": "source missing"}
        quote = confirmation.strip() if isinstance(confirmation, str) else ""
        if source.startswith("agent:") and not quote:
            return 403, {"error": "agent Go needs the operator's chat confirmation quote (confirmation)"}
            
        if not isinstance(checklist, dict) or not checklist or not all(v is True for v in checklist.values()):
            return 409, {"error": "checklist incomplete"}
            
        if self.deps_factory is None:
            return 503, {"error": "live campaign wiring not built: hardware path needs operator approval"}
            
        with self.lock:
            if self.runner_thread and self.runner_thread.is_alive():
                self.go_grant = pack_id
                self.condition.notify_all()
                self._log_go(campaign_path, pack_id, source, quote)
                return 200, self.state()
                
            self.campaign_path = campaign_path
            self.control = RunnerControl()
            self.report = None
            self._live_flights = []
            self.waiting_pack = None
            self.go_grant = pack_id
            
            try:
                deps = self.deps_factory()
            except Exception as exc:  # live factory refuses without gateway / fresh g_wfb_status
                self.go_grant = None
                return 409, {"error": f"campaign deps not ready: {exc}"}
            self.deps = dataclasses.replace(
                deps,
                wait_for_go=self.wait_for_go,
                arm_allowed=self.arm_allowed,
                control=self.control,
                apply_params=self.apply_params,
                on_flight=self.on_flight,
                say=self.say
            )
            
            self.runner_thread = threading.Thread(
                target=self._run_wrapper,
                args=(campaign_path, self.deps),
                daemon=True
            )
            self.runner_thread.start()
            self._log_go(campaign_path, pack_id, source, quote)
            return 200, self.state()

    def _log_go(self, campaign_path, pack_id, source, quote):
        entry = {"time_ns": time.time_ns(), "source": source, "campaign_path": campaign_path,
                 "pack_id": pack_id, "confirmation": quote}
        self.go_log.append(entry)
        if self.go_log_path is not None:
            try:
                self.go_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.go_log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
        say = getattr(self.agent, "add_agent_message", None)
        if callable(say) and source.startswith("agent:"):
            try:
                say(f"campaign Go ({pack_id}) on operator confirmation: \"{quote}\"", source=source)
            except Exception:
                pass

    def _run_wrapper(self, campaign_path, deps):
        try:
            report = run_campaign(campaign_path, deps)
        except Exception as exc:
            report = CampaignReport(
                campaign=None,
                flights=self._live_flights,
                status="error",
                reason=f"{type(exc).__name__}: {exc}"
            )
        with self.lock:
            self.report = report

    def on_flight(self, rec):
        with self.lock:
            self._live_flights.append(rec)

    def say(self, text):
        """Runner result lines to the operator chat (agent message); dropped when no agent is wired."""
        add = getattr(self.agent, "add_agent_message", None)
        if callable(add):
            try:
                add(text, source="agent:runner")
            except Exception:
                pass

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
        agent_ok = bool(self.agent is not None and self.agent.allow_agent_arm)
        if not agent_ok:
            return False

        check = self.stream_check or getattr(
            getattr(getattr(self.agent, "service", None), "streams", None),
            "preflight_check", None)
        if check is None:
            self.arm_refusal = None
            return True

        ok, reason = check()
        if not ok:
            self.arm_refusal = reason
            return False

        self.arm_refusal = None
        return True

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
            
        plan_id = getattr(plan, "plan_id", None)
                
        if not plan_id:
            return False
            
        start_t = time.time()
        while time.time() - start_t < self.param_timeout_s:
            if self.control.get() in ("land", "abort"):
                return False
                
            p = self.agent.get_plan(plan_id)
            st = getattr(p, "status", None) if p else None
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
            elif self.runner_thread and self.runner_thread.is_alive():
                flights = [dataclasses.asdict(f) for f in self._live_flights]
                
            return {
                "status": st,
                "waiting_pack": self.waiting_pack,
                "campaign_path": self.campaign_path if (self.runner_thread and self.runner_thread.is_alive()) or self.report else None,
                "reason": self.report.reason if self.report else "",
                "flights": flights,
                "control": self.control.get() if self.runner_thread and self.runner_thread.is_alive() else None,
                "arm_refusal": self.arm_refusal,
                "last_go": self.go_log[-1] if self.go_log else None
            }
