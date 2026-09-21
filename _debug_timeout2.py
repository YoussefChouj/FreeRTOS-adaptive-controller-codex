"""Debug script for timeout monitor with detailed tracing."""
import sys, time, threading
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path('.').resolve()))

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange

def _make_range(i):
    return StreamRange(address=0x20000000 + i*4, size=4, count=1, name='test_%d' % i)

class FakeClock:
    def __init__(self, start_ns=1_000_000_000):
        self._ns = start_ns
    def time_ns(self): return self._ns
    def monotonic(self): return self._ns / 1e9
    def event_wait(self, timeout=None): return True
    def tick(self, delta_ns): self._ns += delta_ns

class FakeBridge(WifiBridge):
    def __init__(self):
        super().__init__()
        import socket
        self._wifi = MagicMock()
        self._wifi_send = MagicMock()
        self._cmd_udp = MagicMock()
        self._telem_udp = MagicMock()
        self._udp_send = MagicMock()
        self._wifi.recvfrom = MagicMock(return_value=(b'', ('0.0.0.0', 0)))

    def _timeout_monitor(self):
        """Copy of the real _timeout_monitor with debug output."""
        import time
        while not self._stop.wait(timeout=self._timeout_check_interval):
            now_ns = time.time_ns()
            cutoff_ns = now_ns - self._schema_timeout_ns
            stale_cutoff_ns = now_ns - self._stale_threshold_ns
            print('[DEBUG] now_ns=%d, cutoff_ns=%d' % (now_ns, cutoff_ns))

            # Stale period detection
            if self._last_valid_frame_ns and self._last_valid_frame_ns < stale_cutoff_ns:
                self._counter_stale_periods += 1
                self._last_valid_frame_ns = 0

            timed_out_slots = []
            with self._stream_lock:
                pending_slots = dict(self._pending_schema_ranges)
            print('[DEBUG] pending_slots=%s' % pending_slots)

            for slot, ranges_tuple in pending_slots.items():
                with self._stream_lock:
                    expected = self._next_expected_batch.get(slot, 0)
                    sent_req_id = None
                    for rid, req_slot in list(self._request_to_slot.items()):
                        if req_slot == slot:
                            state = self._request_states.get(rid)
                            meta = self._request_metadata.get(rid)
                            if state == 'sent' and meta is not None:
                                bi = meta.get('batch_index', -1)
                                if bi == expected:
                                    sent_req_id = rid
                                    break
                print('[DEBUG] slot=%d, expected=%d, sent_req_id=%s' % (slot, expected, sent_req_id))

                if sent_req_id is None:
                    continue

                with self._stream_lock:
                    meta = self._request_metadata.get(sent_req_id)
                if meta is None:
                    continue

                sent_ns = meta.get('sent_ns') or 0
                print('[DEBUG] sent_ns=%d, cutoff_ns=%d, timed_out=%s' % (
                    sent_ns, cutoff_ns, (sent_ns < cutoff_ns)))
                if sent_ns and sent_ns >= cutoff_ns:
                    continue  # not yet timed out

                # Request timed out
                retry_count = (meta.get('retry_count') or 0) + 1
                meta['retry_count'] = retry_count
                print('[DEBUG] Retry count now=%d' % retry_count)

                if retry_count >= self._MAX_SCHEMA_RETRIES:
                    print('[DEBUG] Degrading slot %d' % slot)
                else:
                    print('[DEBUG] Retrying slot %d' % slot)

bridge = FakeBridge()

# Patch clock
fake = FakeClock()
p_time = patch('ground_station.comm.wifi_bridge.time.time_ns', new=fake.time_ns)
p_mon = patch('ground_station.comm.wifi_bridge.time.monotonic', new=fake.monotonic)
p_event = patch.object(threading.Event, 'wait', new=fake.event_wait)
p_time.start(); p_mon.start(); p_event.start()

# Subscribe
bridge.subscribe_slot(slot=0, divider=4, ranges=[_make_range(0)], transport=1)

print('\nAfter subscribe:')
print('  sent_ns:', bridge._request_metadata[0].get('sent_ns'))
print('  _pending_schema_ranges:', dict(bridge._pending_schema_ranges))

# Advance clock past timeout
fake.tick(5_000_000_001)
print('\nAfter tick to %d (cutoff=%d):' % (fake.time_ns(), fake.time_ns() - 5_000_000_000))

# Run timeout monitor
bridge._timeout_monitor()

print('\nFinal retry_count:', bridge._request_metadata[0].get('retry_count'))

p_time.stop(); p_mon.stop(); p_event.stop()
