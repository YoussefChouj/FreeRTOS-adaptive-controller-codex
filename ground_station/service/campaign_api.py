import threading
import time
import dataclasses
import json
from pathlib import Path
from ground_station.service.campaign_runner import RunnerControl, run_campaign, CampaignReport
from ground_station.service.agent import AgentDisabledError, PlanBusyError

class CampaignService:
    def __init__(self, agent=None, deps_factory=None, knobs=(), param_timeout_s=30.0, stream_check=None,
                 go_log_path=None, outputs=None):
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
        # outputs(campaign_path, report) -> folder: summary, plots, metrics after every run (live only)
        self.outputs = outputs
        self.outputs_dir = None
        self.phase = ""            # runner's one-line "what now" (RunnerDeps.on_phase)
        self.total_flights = None  # flights the campaign will fly, for "flight n/m"

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
                # A Go counts only while the runner waits. Before WP-23 a Go during a flight was kept as a grant,
                # so after a failed auto-next check the next flight started without the operator's decision.
                if not self.waiting_pack:
                    return 409, {"error": "the runner is flying, not waiting for a go: Go counts only while "
                                          "campaign_state says waiting_for_go"}
                # a Go for another pack never matches wait_for_go: the runner would wait forever
                if pack_id != self.waiting_pack:
                    return 409, {"error": f"the runner waits for pack {self.waiting_pack}, not {pack_id}"}
                self.go_grant = pack_id
                self.condition.notify_all()
                self._log_go(campaign_path, pack_id, source, quote)
                return 200, self.state()
                
            first_pack = _first_pack(campaign_path)
            if first_pack is not None and pack_id != first_pack:
                return 409, {"error": f"the campaign's first flight uses pack {first_pack}, not {pack_id}: "
                                      f"relaunch with campaign_launch --pack {pack_id}"}
            self.campaign_path = campaign_path
            self.control = RunnerControl()
            self.report = None
            self.outputs_dir = None
            self._live_flights = []
            self.waiting_pack = None
            self.go_grant = pack_id
            self.arm_refusal = None
            self.phase = "starting"
            self.total_flights = _total_flights(campaign_path)

            try:
                deps = self.deps_factory()
            except Exception as exc:  # live factory refuses without gateway / fresh g_wfb_status
                self.go_grant = None
                self.phase = ""
                return 409, {"error": f"campaign deps not ready: {exc}"}
            self.deps = dataclasses.replace(
                deps,
                wait_for_go=self.wait_for_go,
                arm_allowed=self.arm_allowed,
                control=self.control,
                apply_params=self.apply_params,
                on_flight=self.on_flight,
                say=self.say,
                on_phase=self.on_phase,
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
        if report.status == "arm_refused" and self.arm_refusal:
            report.reason = f"stream check failed before takeoff: {self.arm_refusal}"
        out_dir = None
        if self.outputs is not None:
            try:
                out_dir = str(self.outputs(campaign_path, report))
            except Exception as exc:
                self.say(f"campaign outputs failed: {type(exc).__name__}: {exc}")
            else:
                self.say(f"campaign {report.status}: summary in {out_dir}/summary.md")
        with self.lock:
            self.outputs_dir = out_dir
            self.report = report

    def on_flight(self, rec):
        with self.lock:
            self._live_flights.append(rec)

    def on_phase(self, text):
        with self.lock:
            self.phase = text

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
        # Go is the operator's consent to arm and disarm for this campaign (operator decision 2026-10-03):
        # no separate allow_agent_arm tick. The stream preflight below still gates every flight.
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
            reason = self.report.reason if self.report else ""
            alive = bool(self.runner_thread and self.runner_thread.is_alive())

            return {
                "status": st,
                "banner": _banner(st, reason, self.waiting_pack, len(flights), self.total_flights,
                                  self.outputs_dir, self.phase),
                "phase": self.phase if alive else "",
                "total_flights": self.total_flights,
                "waiting_pack": self.waiting_pack,
                "campaign_path": self.campaign_path if alive or self.report else None,
                "reason": reason,
                "flights": flights,
                "control": self.control.get() if alive else None,
                "arm_refusal": self.arm_refusal,
                "last_go": self.go_log[-1] if self.go_log else None,
                "outputs_dir": self.outputs_dir
            }


def _first_pack(campaign_path):
    """packs[0] of the campaign, or None when it does not load (the runner then reports the load error)."""
    from ground_station.service.campaign_schema import load_campaign
    try:
        packs = load_campaign(campaign_path).packs
    except Exception:
        return None
    return packs[0] if packs else None


def _total_flights(campaign_path):
    """Flights run_campaign will fly (fly: the queue capped by max_flights; tune: max_flights); None if unreadable."""
    from ground_station.service.campaign_schema import load_campaign
    try:
        c = load_campaign(campaign_path)
    except Exception:
        return None
    queue = sum(e.repeats for e in c.experiments)
    return min(c.max_flights, queue) if c.mode == "fly" else c.max_flights


def _banner(status, reason, waiting_pack, n_done, total, outputs_dir, phase):
    """One line for the panel banner and the agent: idle / waiting for go / flying n/m / paused: reason / done."""
    of = f"/{total}" if total else ""
    if status == "waiting_for_go":
        return f"waiting for go: pack {waiting_pack}, flight {n_done + 1}{of}"
    if status == "running":
        return phase or f"running flight {n_done + 1}{of}"
    if status == "idle":
        return "idle"
    if status == "complete":
        return f"done: {outputs_dir}" if outputs_dir else "done: outputs not written"
    return f"paused ({status}): {reason or 'no reason given'}"
