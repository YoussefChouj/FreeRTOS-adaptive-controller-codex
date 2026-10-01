import re

with open("ground_station/service/campaign_api.py", "r") as f:
    content = f.read()

# 1. __init__
old_init = """    def __init__(self, agent=None, deps_factory=None, knobs=(), param_timeout_s=30.0):
        self.agent = agent
        self.deps_factory = deps_factory
        self.knobs = knobs
        self.param_timeout_s = param_timeout_s
        self.lock = threading.RLock()"""

new_init = """    def __init__(self, agent=None, deps_factory=None, knobs=(), param_timeout_s=30.0, stream_check=None):
        self.agent = agent
        self.deps_factory = deps_factory
        self.knobs = knobs
        self.param_timeout_s = param_timeout_s
        self.stream_check = stream_check
        self.lock = threading.RLock()"""

content = content.replace(old_init, new_init)

old_init2 = """        self.report = None
        self._live_flights = []
        
        self.condition = threading.Condition(self.lock)"""

new_init2 = """        self.report = None
        self._live_flights = []
        self.arm_refusal = None
        
        self.condition = threading.Condition(self.lock)"""
        
content = content.replace(old_init2, new_init2)

# 2. arm_allowed
old_arm = """    def arm_allowed(self):
        return bool(self.agent is not None and self.agent.allow_agent_arm)"""

new_arm = """    def arm_allowed(self):
        agent_ok = bool(self.agent is not None and self.agent.allow_agent_arm)
        if not agent_ok:
            return False
            
        if self.stream_check is not None:
            ok, reason = self.stream_check()
        else:
            try:
                ok, reason = self.agent.service.streams.preflight_check()
            except AttributeError:
                ok, reason = True, ""
                
        if not ok:
            self.arm_refusal = reason
            return False
            
        return True"""

content = content.replace(old_arm, new_arm)

with open("ground_station/service/campaign_api.py", "w") as f:
    f.write(content)
