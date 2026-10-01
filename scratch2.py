import re

with open("ground_station/service/streams.py", "r") as f:
    content = f.read()

# 1. StreamsManager constants
content = content.replace("    MAX_RETRIES = 3\n    TIMEOUT_S = 1.5",
"""    MAX_RETRIES = 3
    TIMEOUT_S = 1.5
    REPLAY_TIMEOUT_S = 0.5
    REPLAY_RETRIES = 3
    REPLAY_GAP_S = 0.15""")

# 2. _await_schemas
old_await = """    def _await_schemas(self, bridge, sent: dict, specs: dict):
        def _ok(slot):
            div, n = sent[slot]
            with bridge._stream_lock:
                sch = bridge._stream_schemas.get(slot)
            return sch is not None and sch.divider == div and \\
                len(sch.ranges) == n

        self._sleep(self.TIMEOUT_S)
        for attempt in range(1, self.MAX_RETRIES + 1):
            missing = [s for s in sent if not _ok(s)]
            if not missing:
                return
            for slot in missing:
                bridge.subscribe_slot(slot=slot, divider=sent[slot][0],
                                      ranges=list(specs[slot]["vars"]))
            self._sleep(self.TIMEOUT_S)
        missing = [s for s in sent if not _ok(s)]
        if missing:
            raise RuntimeError("no schema reply for slot(s) %s after %d "
                               "retries" % (missing, self.MAX_RETRIES))"""

new_await = """    def _await_schemas(self, bridge, sent: dict, specs: dict, timeout_s=None, retries=None, raise_on_fail=True):
        if timeout_s is None: timeout_s = self.TIMEOUT_S
        if retries is None: retries = self.MAX_RETRIES
        
        def _ok(slot):
            div, n = sent[slot]
            with bridge._stream_lock:
                sch = bridge._stream_schemas.get(slot)
            if n is None:
                return sch is not None and sch.divider == div
            return sch is not None and sch.divider == div and len(sch.ranges) == n

        step_s = 0.05 if timeout_s < self.TIMEOUT_S else None

        def _wait():
            if step_s:
                t = 0.0
                while t < timeout_s:
                    if not [s for s in sent if not _ok(s)]:
                        break
                    s_amt = min(step_s, timeout_s - t)
                    self._sleep(s_amt)
                    t += s_amt
            else:
                self._sleep(timeout_s)

        _wait()
        for attempt in range(1, retries + 1):
            missing = [s for s in sent if not _ok(s)]
            if not missing:
                return []
            for slot in missing:
                if sent[slot][1] is None:
                    from ground_station.service.streams import default_slots
                    d = next((x for x in default_slots() if x["slot"] == slot), None)
                    if d:
                        bridge._request_stream_schema(d["slot"], tuple(d["vars"]), d["divider"], d["name"])
                else:
                    bridge.subscribe_slot(slot=slot, divider=sent[slot][0], ranges=list(specs[slot]["vars"]))
            _wait()
        
        missing = [s for s in sent if not _ok(s)]
        if missing and raise_on_fail:
            raise RuntimeError("no schema reply for slot(s) %s after %d "
                               "retries" % (missing, retries))
        return missing"""

content = content.replace(old_await, new_await)

# 3. _replay
old_replay = """    def _replay(self):
        \"\"\"Watchdog replay after an FC reboot: defaults for untouched slots,
        the swapped assignments for the rest. Defaults go through the bridge's
        own request so the schema names are remembered per slot.\"\"\"
        bridge = self.service.bridge
        defaults = default_slots()
        with self._lock:
            overrides = {n: dict(o) for n, o in self._overrides.items()}
        for d in defaults:
            if d["slot"] in overrides:
                continue
            bridge._request_stream_schema(d["slot"], tuple(d["vars"]),
                                          d["divider"], d["name"])
        for slot, ov in overrides.items():
            bridge.subscribe_slot(slot=slot, divider=ov["divider"],
                                  ranges=list(ov["vars"]))"""

new_replay = """    def _replay(self):
        \"\"\"Watchdog replay after an FC reboot: defaults for untouched slots,
        the swapped assignments for the rest. Defaults go through the bridge's
        own request so the schema names are remembered per slot.\"\"\"
        bridge = self.service.bridge
        defaults = default_slots()
        with self._lock:
            overrides = {n: dict(o) for n, o in self._overrides.items()}
            
        failed_slots = []
        for d in defaults:
            slot = d["slot"]
            if slot in overrides:
                continue
            with bridge._stream_lock:
                bridge._stream_schemas.pop(slot, None)
            bridge._request_stream_schema(slot, tuple(d["vars"]), d["divider"], d["name"])
            missing = self._await_schemas(
                bridge, {slot: (d["divider"], None)}, None,
                timeout_s=self.REPLAY_TIMEOUT_S,
                retries=self.REPLAY_RETRIES,
                raise_on_fail=False
            )
            if missing: failed_slots.extend(missing)
            self._sleep(self.REPLAY_GAP_S)
            
        for slot, ov in overrides.items():
            with bridge._stream_lock:
                bridge._stream_schemas.pop(slot, None)
            bridge.subscribe_slot(slot=slot, divider=ov["divider"], ranges=list(ov["vars"]))
            missing = self._await_schemas(
                bridge, {slot: (ov["divider"], len(ov["vars"]))}, {slot: {"vars": ov["vars"]}},
                timeout_s=self.REPLAY_TIMEOUT_S,
                retries=self.REPLAY_RETRIES,
                raise_on_fail=False
            )
            if missing: failed_slots.extend(missing)
            self._sleep(self.REPLAY_GAP_S)
            
        if failed_slots:
            import sys
            print("[streams] replay failed for slot(s): %s" % failed_slots, file=sys.stderr)
        return failed_slots"""

content = content.replace(old_replay, new_replay)

with open("ground_station/service/streams.py", "w") as f:
    f.write(content)
