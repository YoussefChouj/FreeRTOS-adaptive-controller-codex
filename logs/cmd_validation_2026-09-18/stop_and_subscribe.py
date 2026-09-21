"""Stop current slot-0 subscribe, then fresh-subscribe with the dashboard layout."""
import socket, struct, time, sys
from ground_station.livewatch.symbols import SymbolResolver
from ground_station.livewatch.stream import build_stream_request, StreamRange

WIFI_HOST = "192.168.4.1"
WIFI_PORT = 14550
LOCAL_PORT = 14551

# Build stop request for slot 0
def build_stop(slot=0):
    ranges = []
    request = build_stream_request(ranges, divider=0, transport=1, slot=slot, other_bps=0)
    return request

# Build dashboard subscribe request
def build_subscribe(slot=0):
    from ground_station.comm.boot_default_layout import DASHBOARD_FRAME_A_VARS
    resolver = SymbolResolver("OBJ/JX_FLY.axf")
    ranges = []
    for name in DASHBOARD_FRAME_A_VARS:
        sym = resolver.resolve(name)
        ranges.append(StreamRange(address=sym.address, size=sym.size, count=1,
                                  name=name, fmt="f" if sym.size == 4 else ("B" if sym.size == 1 else "H")))
    request = build_stream_request(ranges, divider=4, transport=1, slot=slot, other_bps=0)
    return request

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.settimeout(0.1)
sock.bind(("0.0.0.0", LOCAL_PORT))
sock.sendto(b"\x00", (WIFI_HOST, WIFI_PORT))
time.sleep(0.05)

# Step 1: Stop slot 0
print("Sending stop (divider=0) for slot 0...")
stop_frame = build_stop(0)
sock.sendto(stop_frame, (WIFI_HOST, WIFI_PORT))
print(f"Stop frame sent: {stop_frame.hex()[:40]}...")
time.sleep(1.0)

# Collect any incoming data after stop
rx = bytearray()
for _ in range(20):
    try:
        data, _ = sock.recvfrom(4096)
        rx.extend(data)
    except socket.timeout:
        pass

# Count frames
i = 0
frame_counts = {}
while i < len(rx) - 1:
    if rx[i] == 0xAA and rx[i+1] == 0xBB:
        ftype = rx[i+2]
        frame_counts[ftype] = frame_counts.get(ftype, 0) + 1
        hi, lo = rx[i+3], rx[i+4]
        length = (hi << 8) | lo
        i += 6 + length
    else:
        i += 1

print(f"Frames after stop: {dict(frame_counts)}")

# Step 2: Fresh subscribe
print("\nSending fresh dashboard subscribe...")
subscribe_frame = build_subscribe(0)
sock.sendto(subscribe_frame, (WIFI_HOST, WIFI_PORT))
print(f"Subscribe frame: {subscribe_frame.hex()[:60]}...")
time.sleep(0.2)

# Collect 0x08 replies
rx2 = bytearray()
deadline = time.monotonic() + 3.0
got_08 = False
while time.monotonic() < deadline:
    try:
        data, _ = sock.recvfrom(4096)
        rx2.extend(data)
    except socket.timeout:
        pass

i = 0
while i < len(rx2) - 6:
    if rx2[i] == 0xAA and rx2[i+1] == 0xBB:
        ftype = rx2[i+2]
        hi, lo = rx2[i+3], rx2[i+4]
        length = (hi << 8) | lo
        total = 6 + length
        if i + total > len(rx2):
            break
        frame = bytes(rx2[i:i+total])
        if ftype == 0x08:
            print(f"\nGot 0x08 schema reply!")
            print(f"  length={length} B, frame={frame.hex()[:80]}...")
            got_08 = True
        elif ftype in (0x09, 0x0A, 0x0B, 0x0C):
            print(f"  data frame 0x{ftype:02X}: {length} B")
        elif ftype in (0x30, 0x31, 0x32):
            print(f"  result frame 0x{ftype:02X}: {length} B")
        elif ftype == 0x7F:
            print(f"  ERROR frame 0x7F: {frame[6:-1]}")
        i += total
    else:
        i += 1

if not got_08:
    print("\nNo 0x08 schema reply within 3s!")
    print(f"Buffer: {bytes(rx2).hex()[:100]}")

sock.close()
sys.exit(0 if got_08 else 1)