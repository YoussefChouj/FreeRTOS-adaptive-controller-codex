import sys, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange
import ground_station.comm.wifi_bridge as wb

bridge = WifiBridge(vofa_enabled=False)

# 1. F1
class FakeSchema:
    def __init__(self, ranges): self.ranges = ranges
rng = StreamRange(address=0x20001000, size=4, count=1, name="xTickCount", fmt="I")
bridge.apply_manifest_schema(1, FakeSchema((rng,)))
frame = b"\xAA\xBB\x0A\x00\x08\x01\x00\x00\x00\x00" + struct.pack("<I", 1234567) + b"\x00\x00"
decoded = bridge._decode_stream_frame(1, frame)
print("V1 Before: 0.0")
print("V1 After:", decoded["values"][0])

# 2. I2
payload = struct.pack("<3f3f2ff4HH", 1.0, 2.0, 3.0, 0.01, 0.02, 0.03, 0.5, 0.6, 1.25, 1111, 2222, 3333, 4444, 42)
frame = b"\xAA\xBB\x06" + bytes([(len(payload) >> 8) & 0xFF, len(payload) & 0xFF, 0x00]) + payload + b"\x00\x00"
decoded_c = bridge._decode_frame_c(frame)
print("V2 keys:", {k: v for k, v in decoded_c.items() if "rpm" in k})

# 3. E1
names = [f"s_ekf.x[{i}]" for i in range(9)]
values = [0.1 * (i + 1) for i in range(9)]
out = WifiBridge._slot0_to_sidebar(names, values)
print("V3 ekf mapped:", [k for k in out.keys() if "ekf." in k])
print("V3 absent pos_x in out?", "ekf.pos_x" in out)
