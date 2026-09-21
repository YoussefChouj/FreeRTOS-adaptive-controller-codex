"""Try a slot-1 subscribe with a simple manifest to see if the firmware accepts."""
import socket, struct, time, sys

# Build a 0x21 subscribe request directly
# Format: 0xCC 0xDE 0x21 LEN_HI LEN_LO [divider transport slot n_ranges] (addr u32 size u8 count u8)...

def build_request(ranges, divider, transport=1, slot=1):
    payload = bytes((divider, transport, slot))
    for addr, size, count in ranges:
        payload += struct.pack("<IBB", addr, size, count)
    body = bytes((0x21, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF, len(ranges))) + payload
    crc = 0
    for byte in body:
        crc ^= byte
    return b"\xCC\xDE" + body + bytes((crc,))


# Use simple FW symbols that we know exist
from ground_station.livewatch.symbols import SymbolResolver
r = SymbolResolver("OBJ/JX_FLY.axf")
ranges = []
for name in ["imu_data.rol", "imu_data.pit", "imu_data.yaw",
             "real_voltage", "xTickCount"]:
    sym = r.resolve(name)
    ranges.append((sym.address, sym.size, 1))
    print(f"  {name}: 0x{sym.address:08X} size={sym.size}")

# divider=4 -> 80/4 = 20 Hz
frame = build_request(ranges, divider=4, transport=1, slot=1)
print(f"\nBuilt 0x21 subscribe frame ({len(frame)} B): {frame.hex()[:80]}...")

# Send via UDP
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('0.0.0.0', 14550))
sock.settimeout(0.1)
sock.sendto(b'\x00', ('192.168.4.1', 14550))  # nudge
time.sleep(0.05)

# Send subscribe
sock.sendto(frame, ('192.168.4.1', 14550))
print(f"Sent subscribe request to slot 1")

# Wait for 0x08 schema reply
print("Waiting for 0x08 schema reply...")
rx = bytearray()
deadline = time.monotonic() + 2.0
got_schema = False
while time.monotonic() < deadline:
    try:
        data, _ = sock.recvfrom(8192)
        rx.extend(data)
    except socket.timeout:
        pass

    # Scan for 0xAA 0xBB 0x08
    i = 0
    while i < len(rx) - 6:
        if rx[i] == 0xAA and rx[i+1] == 0xBB and rx[i+2] == 0x08:
            hi, lo = rx[i+3], rx[i+4]
            length = (hi << 8) | lo
            total = 6 + length
            if len(rx) >= total:
                frame_bytes = bytes(rx[i:i+total])
                del rx[:i+total]
                got_schema = True
                print(f"Got 0x08 schema reply ({length} B): {frame_bytes.hex()[:120]}...")
                break
            else:
                break
        elif rx[i] == 0xAA and rx[i+1] == 0xBB:
            hi, lo = rx[i+3], rx[i+4]
            length = (hi << 8) | lo
            if i + 6 + length <= len(rx):
                i += 6 + length
                continue
        i += 1

    if got_schema:
        break

if not got_schema:
    print("No 0x08 schema reply within 2s")
    print(f"Last 30 bytes of rx buffer: {bytes(rx[-30:]).hex()}")

sock.close()
sys.exit(0 if got_schema else 1)