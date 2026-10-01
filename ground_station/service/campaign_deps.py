import math
import time
import tempfile
from typing import Callable
from unittest.mock import Mock

from ground_station.service.campaign_runner import RunnerDeps
from ground_station.analysis.controller_descriptor import load as load_descriptor, Knob, CONTROLLERS_DIR
from ground_station.analysis.battery_model import PackRegistry
from ground_station.analysis.tuner import Tuner
from ground_station.flashtool.code_gate import CodeGate
from ground_station.service.abort_monitor import AbortMonitor, AbortSample, AbortLimits
from ground_station.service.fake_drone import FakeDrone
from ground_station.platform.wfb_commands import WfbClient

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt

def sim_knobs(controller: str = "pid") -> tuple[Knob, ...]:
    descriptor = load_descriptor(CONTROLLERS_DIR / f"{controller}.yaml")
    return descriptor.knobs

def sim_deps_factory(controller: str = "pid", seed: int = 0, dt_s: float = 0.1, workdir: str | None = None) -> Callable[[], RunnerDeps]:
    def factory() -> RunnerDeps:
        drone = FakeDrone()
        client = WfbClient(drone.send)
        clock = FakeClock()
        
        packs = PackRegistry.load(None)
        
        descriptor = load_descriptor(CONTROLLERS_DIR / f"{controller}.yaml")
        tuner = Tuner(descriptor, seed)
        
        _orig_record = tuner.record
        # aborted flights pass J=None, so we map it to math.inf
        def safe_record(params, J, valid):
            _orig_record(params, J if J is not None else math.inf, valid)
        tuner.record = safe_record
        
        _orig_propose = tuner.propose
        def safe_propose(history):
            safe_hist = [
                {"params": h["params"], "J": h["J"] if h["J"] is not None else math.inf, "valid": h["valid"]}
                for h in history
            ]
            return _orig_propose(safe_hist)
        tuner.propose = safe_propose
        
        ledger_path = f"{workdir}/ledger.jsonl" if workdir else f"{tempfile.mkdtemp(prefix='ledger_')}/ledger.jsonl"
        gate = CodeGate(
            build=Mock(),
            ram_check=Mock(),
            ledger_path=ledger_path,
            clock=clock,
            obj_dir="OBJ",
            ram_limit_bytes=256000,
            tolerance_frac=0.1,
            custody=Mock(),
        )
        
        monitor = AbortMonitor(limits=AbortLimits())
        
        def sample(t_s):
            # setting ref_m=position disables the position-error abort in sim
            return AbortSample(
                t_s=t_s, age_s=0.1, airborne=drone.status()["prim_state"] > 0,
                pos_m=drone.position, ref_m=drone.position, roll_deg=drone.roll_deg,
                pitch_deg=drone.pitch_deg, rate_err_dps=(0.0, 0.0, 0.0), sat_frac=0.0,
                safety_trip=int(drone.status()["safety_trip"]), soc_pct=100.0
            )

        def step(dt):
            clock.sleep(dt)
            drone.step(dt)
            time.sleep(0.001)

        def resting_v(pack_id: str) -> float:
            p = packs._packs.get(pack_id)
            if p:
                return float(p.cells) * 4.2
            return 16.8
            
        return RunnerDeps(
            client=client,
            step=step,
            clock=clock,
            sleep=clock.sleep,
            status=drone.status,
            sample=sample,
            packs=packs,
            monitor=monitor,
            tuner=tuner,
            gate=gate,
            flash=lambda: "fake_hash_123",
            analyze=lambda flight_id: 1.23,
            wait_for_go=lambda pack_id: True,
            resting_v=resting_v,
            change_request=lambda: None,
            dt_s=dt_s,
            cooldown_min_s=0.0,
        )
    return factory
