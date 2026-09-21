import sys
sys.path.append('.')
from ground_station.livewatch.stream import StreamRange
import struct

rng = StreamRange(address=0, size=1, count=1, name="g_of_bias_mode", fmt="B")
raw = struct.pack("<B", 2)
print("DECODED u8:", rng.decode(raw))
