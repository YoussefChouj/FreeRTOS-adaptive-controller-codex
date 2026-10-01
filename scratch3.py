import re

with open("ground_station/service/streams.py", "r") as f:
    content = f.read()

# 1. RLock and reset
content = content.replace("        self._lock = threading.Lock()", "        self._lock = threading.RLock()")

content = content.replace("""        self._slots: dict[int, dict] = {}
        self._stop_at = None""",
"""        self._slots: dict[int, dict] = {}
        self._stop_at = None
        self._first_rx: dict[int, float] = {}
        self.slot_faults: list[int] = []
        self._faults_checked = False""")

# 2. check_faults method
check_faults_code = """
    def check_faults(self, now=None) -> list[int]:
        with self._lock:
            if self._faults_checked or not self._active:
                return self.slot_faults
            t_now = time.monotonic() if now is None else now
            if self.t0 is not None and t_now - self.t0 >= 2.0:
                self._faults_checked = True
                import logging
                for slot, spec in self._slots.items():
                    rate = spec.get("rate")
                    if rate is not None and rate > 0 and self.rows.get(slot, 0) == 0:
                        self.slot_faults.append(slot)
                        logging.warning("slot %d: 0 rows 2 s after log start", slot)
            return self.slot_faults

    def note(self, slot, sample, now: Optional[float] = None):"""

content = content.replace("    def note(self, slot, sample, now: Optional[float] = None):", check_faults_code)

# 3. note method logic
old_note_loop = """            for var in self._cols[slot]:
                v = _lookup(values, slot, var)
                row.append("" if v is None else v)
            w.write(t_host, row)
            self.rows[slot] = self.rows.get(slot, 0) + 1
            expired = self._stop_at is not None and mono >= self._stop_at"""

new_note_loop = """            all_empty = True
            for var in self._cols[slot]:
                v = _lookup(values, slot, var)
                row.append("" if v is None else v)
                if v is not None and v != "":
                    all_empty = False
            
            self.check_faults(now=mono)
            if not all_empty:
                w.write(t_host, row)
                self.rows[slot] = self.rows.get(slot, 0) + 1
            expired = self._stop_at is not None and mono >= self._stop_at"""

content = content.replace(old_note_loop, new_note_loop)

# 4. stop
old_stop = """    def stop(self) -> dict:
        with self._lock:
            if not self._active:
                return self.status_locked()
            self._active = False
            for w in self._writers.values():"""

new_stop = """    def stop(self) -> dict:
        with self._lock:
            if not self._active:
                return self.status_locked()
            self.check_faults()
            self._active = False
            for w in self._writers.values():"""

content = content.replace(old_stop, new_stop)

# 5. _write_meta
old_meta = """        meta = {"name": self.name, "mode": self.mode,
                "seconds": self.seconds, "window_s": self.window_s,
                "started": self.started, "finished": finished,
                "slots": {str(k): v for k, v in self._slots.items()},
                "rows": {str(k): v for k, v in self.rows.items()},
                "files": self.files, "source": "dashboard-streams"}"""

new_meta = """        slot_status = {}
        for k in self._slots.keys():
            if k in self.slot_faults:
                slot_status[str(k)] = "fault"
            elif self.rows.get(k, 0) == 0:
                slot_status[str(k)] = "empty"
            else:
                slot_status[str(k)] = "ok"

        meta = {"name": self.name, "mode": self.mode,
                "seconds": self.seconds, "window_s": self.window_s,
                "started": self.started, "finished": finished,
                "slots": {str(k): v for k, v in self._slots.items()},
                "rows": {str(k): v for k, v in self.rows.items()},
                "slot_status": slot_status,
                "slot_faults": self.slot_faults,
                "files": self.files, "source": "dashboard-streams"}"""

content = content.replace(old_meta, new_meta)

# 6. status_locked
old_status = """                "elapsed_s": elapsed, "rows": dict(self.rows),
                "files": list(self.files)}"""

new_status = """                "elapsed_s": elapsed, "rows": dict(self.rows),
                "slot_faults": self.slot_faults,
                "files": list(self.files)}"""

content = content.replace(old_status, new_status)

with open("ground_station/service/streams.py", "w") as f:
    f.write(content)
