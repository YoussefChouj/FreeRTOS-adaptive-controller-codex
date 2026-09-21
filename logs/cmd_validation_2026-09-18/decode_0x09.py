"""Capture one real 0x09 frame and decode the dashboard sidebar values.

The 0x09+slot frames are 0xAA 0xBB 0x09 LEN_HI LEN_LO body CRC8.
The body has a known layout from API/subscribe.h:
  byte 0:    n_ranges
  bytes 1..: (per range: addr(4) size(1) count(1) name hash(2))
  then:      SD_ID(4) + reserved(2) + source_timestamp(4) + values(float32 each)

Actually the layout differs. Let me parse and just print values.
"""
import socket, struct, time

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 14550))
s.settimeout(0.5)
s.sendto(b'\x00', ('192.168.4.1', 14550))

# Collect a bunch of 0x09 frames
collected = []
deadline = time.monotonic() + 3.0
rx = bytearray()
while time.monotonic() < deadline:
    try:
        data, _ = s.recvfrom(8192)
        rx.extend(data)
    except socket.timeout:
        pass

i = 0
while i < len(rx) - 6:
    if rx[i] == 0xAA and rx[i+1] == 0xBB:
        ftype = rx[i+2]
        hi, lo = rx[i+3], rx[i+4]
        length = (hi << 8) | lo
        total = 6 + length
        if i + total > len(rx):
            break
        frame = bytes(rx[i:i+total])
        if ftype == 0x09:
            collected.append(frame)
        i += total
    else:
        i += 1

print(f'Collected {len(collected)} 0x09 frames')
if collected:
    frame = collected[0]
    body = frame[5:-1]  # skip sync, type, len, last byte = CRC
    print(f'First frame total {len(frame)} B, body {len(body)} B')
    print(f'body hex: {body.hex()}')

    # Try to find the SD_ID + source_timestamp layout
    # Common: byte 0 is n_ranges, then per-range, then SD_ID (4), res (2), ts (4), values
    # Let me just decode all 4-byte floats
    n_ranges = body[0]
    print(f'\nFirst byte = n_ranges = {n_ranges}')
    print(f'\nFloats in body:')
    for j in range(0, len(body), 4):
        if j+4 <= len(body):
            f = struct.unpack('<f', body[j:j+4])[0]
            u = struct.unpack('<I', body[j:j+4])[0]
            if abs(f) > 1e-6 and abs(f) < 1e6:
                print(f'  [{j:3d}] float = {f:14.4f}  (raw 0x{u:08X})')
            else:
                print(f'  [{j:3d}] raw    0x{u:08X}')

s.close()