"""Debug script for timeout monitor."""
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

bridge = FakeBridge()

# Patch clock
fake = FakeClock()
p_time = patch('ground_station.comm.wifi_bridge.time.time_ns', new=fake.time_ns)
p_mon = patch('ground_station.comm.wifi_bridge.time.monotonic', new=fake.monotonic)
p_event = patch.object(threading.Event, 'wait', new=fake.event_wait)
p_time.start(); p_mon.start(); p_event.start()

# Subscribe
bridge.subscribe_slot(slot=0, divider=4, ranges=[_make_range(0)], transport=1)

print('After subscribe:')
print('  _request_metadata keys:', list(bridge._request_metadata.keys()))
meta = bridge._request_metadata.get(0)
if meta:
    print('  sent_ns:', meta.get('sent_ns'))
    print('  timeout_ns:', meta.get('timeout_ns'))
    print('  retry_count:', meta.get('retry_count'))
print('  _pending_schema_ranges:', dict(bridge._pending_schema_ranges))
print('  _next_expected_batch:', dict(bridge._next_expected_batch))
print('  _request_to_slot:', dict(bridge._request_to_slot))
print('  _request_states:', dict(bridge._request_states))

# Advance clock past timeout
fake.tick(5_000_000_001)
now = fake.time_ns()
cutoff = now - 5_000_000_000
print('\nAfter tick to %d (cutoff=%d):' % (now, cutoff))

# Run timeout monitor
bridge._timeout_monitor()

meta = bridge._request_metadata.get(0)
if meta:
    print('  retry_count:', meta.get('retry_count'))
print('  _request_states:', dict(bridge._request_states))

p_time.stop(); p_mon.stop(); p_event.stop()
