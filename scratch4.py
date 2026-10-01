import re

with open("ground_station/service/streams.py", "r") as f:
    content = f.read()

preflight_code = """
    def preflight_check(self, window_s=2.0, min_ratio=0.8) -> tuple[bool, str]:
        bridge = self.service.bridge
        if bridge is None or not hasattr(bridge, "_stream_stats"):
            return (True, "")
        
        specs = self.slot_specs()
        configured = {slot: spec["rate"] for slot, spec in specs.items() if spec.get("rate", 0) > 0}
        
        if not configured:
            return (True, "")
            
        def _get_counts():
            with bridge._stream_lock:
                return {s: bridge._stream_stats.get(s, {}).get("received", 0) for s in configured}
                
        before = _get_counts()
        self._sleep(window_s)
        after = _get_counts()
        
        silent_slots = []
        for slot, rate in configured.items():
            b = before.get(slot, 0)
            a = after.get(slot, 0)
            growth = a - b if a >= b else a
            if growth < min_ratio * rate * window_s:
                silent_slots.append(slot)
                
        if not silent_slots:
            return (True, "")
            
        resent = False
        for slot in silent_slots:
            if hasattr(bridge, "resend_slot"):
                bridge.resend_slot(slot)
                resent = True
                
        if not resent:
            silent_slots.sort()
            msg = "slot %d silent" % silent_slots[0] if len(silent_slots) == 1 else "slots %s silent" % ",".join(map(str, silent_slots))
            return (False, msg)
            
        before2 = _get_counts()
        self._sleep(window_s)
        after2 = _get_counts()
        
        silent_slots2 = []
        for slot in silent_slots:
            rate = configured[slot]
            b = before2.get(slot, 0)
            a = after2.get(slot, 0)
            growth = a - b if a >= b else a
            if growth < min_ratio * rate * window_s:
                silent_slots2.append(slot)
                
        if not silent_slots2:
            return (True, "")
            
        silent_slots2.sort()
        msg = "slot %d silent" % silent_slots2[0] if len(silent_slots2) == 1 else "slots %s silent" % ",".join(map(str, silent_slots2))
        return (False, msg)

    def _await_schemas"""

content = content.replace("    def _await_schemas", preflight_code)

with open("ground_station/service/streams.py", "w") as f:
    f.write(content)
