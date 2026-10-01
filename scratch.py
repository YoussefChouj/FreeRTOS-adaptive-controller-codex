import re

with open("ground_station/comm/wifi_bridge.py", "r") as f:
    content = f.read()

# 1. __init__
content = content.replace("        self._last_resubscribe = 0.0", 
"""        self._last_resubscribe = 0.0
        self._last_slot_rx: dict[int, float] = {}
        self._last_slot_resend: dict[int, float] = {}
        self._slot_requests: dict[int, tuple[float, bytes, tuple]] = {}""")

# 2. constants
content = content.replace("    _RESUBSCRIBE_AFTER_S = 3.0",
"""    _SLOT_SILENT_MIN_S = 3.0
    _SLOT_RESEND_EVERY_S = 5.0
    _SLOT_SILENT_PERIODS = 5

    _RESUBSCRIBE_AFTER_S = 3.0""")

# 3. _check_resubscribe
content = content.replace("""    def _check_resubscribe(self) -> None:
        \"\"\"Re-send the auto-subscribe when the FC has evidently forgotten it.

        Called on the RX thread for every non-subscribe frame. The request
        resolves DWARF symbols, so it runs on its own thread.
        \"\"\"
        layout = self._resubscribe_layout""",
"""    def _check_slot_watchdog(self) -> list[int]:
        resent = []
        now = time.monotonic()
        for slot, (rate_hz, req_bytes, ranges) in list(self._slot_requests.items()):
            if rate_hz <= 0:
                continue
            last_rx = self._last_slot_rx.get(slot, 0.0)
            silent = now - last_rx > max(self._SLOT_SILENT_MIN_S, self._SLOT_SILENT_PERIODS / rate_hz)
            if silent:
                with self._stream_lock:
                    self._slot_states[slot] = "stale"
                last_resend = self._last_slot_resend.get(slot, float('-inf'))
                if now - last_resend >= self._SLOT_RESEND_EVERY_S:
                    try:
                        self._wifi_send.sendto(req_bytes, (self._wifi_host, self._wifi_port))
                    except OSError:
                        pass
                    with self._stream_lock:
                        self._pending_schema_ranges[slot] = ranges
                    self._last_slot_resend[slot] = now
                    print(f"[wifi_bridge] slot {slot} silent {now - last_rx:.1f}s, re-requesting",
                          file=sys.stderr, flush=True)
                    resent.append(slot)
        return resent

    def resend_slot(self, slot: int) -> bool:
        if slot not in self._slot_requests:
            return False
        rate_hz, req_bytes, ranges = self._slot_requests[slot]
        if rate_hz <= 0:
            return False
        try:
            self._wifi_send.sendto(req_bytes, (self._wifi_host, self._wifi_port))
        except OSError:
            pass
        with self._stream_lock:
            self._pending_schema_ranges[slot] = ranges
        self._last_slot_resend[slot] = time.monotonic()
        return True

    def _check_resubscribe(self) -> None:
        \"\"\"Re-send the auto-subscribe when the FC has evidently forgotten it.

        Called on the RX thread for every non-subscribe frame. The request
        resolves DWARF symbols, so it runs on its own thread.
        \"\"\"
        self._check_slot_watchdog()
        layout = self._resubscribe_layout""")

# 4. _request_stream_schema
content = content.replace("""            with self._stream_lock:
                self._pending_schema_ranges[slot] = tuple(ranges)

            # S15 instrumentation: log the 0x21 request bytes so we can""",
"""            with self._stream_lock:
                self._pending_schema_ranges[slot] = tuple(ranges)

            rate_hz = self._expected_rate_for_slot(slot, divider)
            if divider > 0:
                self._slot_requests[slot] = (rate_hz, request, tuple(ranges))
                self._last_slot_rx[slot] = time.monotonic()
            else:
                self._slot_requests.pop(slot, None)
                self._last_slot_rx.pop(slot, None)

            # S15 instrumentation: log the 0x21 request bytes so we can""")

# 5. _send_subscribe_bytes
content = content.replace("""        self._slot_states[slot] = "sent"
        self._wifi_send.sendto(request, (self._wifi_host, self._wifi_port))
        return request""",
"""        self._slot_states[slot] = "sent"
        self._wifi_send.sendto(request, (self._wifi_host, self._wifi_port))
        
        rate_hz = self._expected_rate_for_slot(slot, divider)
        if divider > 0:
            self._slot_requests[slot] = (rate_hz, request, tuple(ranges))
            self._last_slot_rx[slot] = time.monotonic()
        else:
            self._slot_requests.pop(slot, None)
            self._last_slot_rx.pop(slot, None)

        return request""")

# 6. _rx_loop dispatch
content = content.replace("""                    if decoded is not None:
                        self._last_stream_rx = time.monotonic()
                        # Two downstream sinks: dashboard JSON mirror and""",
"""                    if decoded is not None:
                        self._last_stream_rx = time.monotonic()
                        self._last_slot_rx[slot] = self._last_stream_rx
                        # Two downstream sinks: dashboard JSON mirror and""")

with open("ground_station/comm/wifi_bridge.py", "w") as f:
    f.write(content)
