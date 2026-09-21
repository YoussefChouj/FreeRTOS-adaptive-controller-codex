"""Execute baseline WifiBridge (3.14 pyc) and run the unknown-slot test frame."""
import marshal, types, sys, struct

f = open(r"ground_station/comm/__pycache__/wifi_bridge.cpython-314.pyc", "rb")
f.read(16)
co = marshal.load(f)

# Stub out imports the module exec needs
import types as t
g = {"__name__": "wifi_bridge_oracle", "__builtins__": __builtins__}

class _Any:
    def __getattr__(self, k): return _Any()
    def __call__(self, *a, **k): return _Any()

sys.modules.setdefault("serial", _Any())
# Execute just enough: provide stubs for ground_station deps
for mod in ("ground_station", "ground_station.platform",
            "ground_station.platform.transactions",
            "ground_station.livewatch", "ground_station.livewatch.stream",
            "ground_station.livewatch.symbols",
            "ground_station.livewatch.transport"):
    sys.modules.setdefault(mod, _Any())

try:
    exec(co, g)
except Exception as e:
    print("exec error (non-fatal):", e)

WifiBridge = g.get("WifiBridge")
print("WifiBridge found:", WifiBridge is not None)

# Reconstruct the test frame exactly as in test_wifi_bridge_stream.py:681
rng = struct.pack("<IHH", 0x20001000, 4, 1)  # 8 B (assume standard rng fixture)
divider, transport, slot, n_ranges = 4, 1, 2, 1
total_bytes = 4
config = bytes([divider, transport, slot, n_ranges,
                 (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
payload = config + rng
payload_len = 13
hi, lo = (payload_len >> 8) & 0xFF, payload_len & 0xFF

def xor_crc(data):
    c = 0
    for b in data: c ^= b
    return c

crcb = xor_crc(bytes([0x08, hi, lo]) + payload)
frame = bytes([0xAA, 0xBB, 0x08, hi, lo]) + payload + bytes([crcb])
print("frame len:", len(frame))

b = WifiBridge.__new__(WifiBridge)
import threading
b._stream_lock = threading.Lock()
b._stream_schemas = {}
b._stream_stats = {}
b._pending_schema_ranges = {}
try:
    result = b._handle_schema_frame(frame)
except Exception as e:
    print("handler raised:", type(e).__name__, e)
    result = "RAISED"
print("baseline handler result:", result)
