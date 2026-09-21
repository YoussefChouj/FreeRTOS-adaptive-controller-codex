"""Classify incoming USART3 frames by sync header."""
import socket, time

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 14550))
s.settimeout(1.0)
s.sendto(b'\x00', ('192.168.4.1', 14550))

frames = []
deadline = time.monotonic() + 4.0
rx = bytearray()
while time.monotonic() < deadline:
    try:
        data, _ = s.recvfrom(8192)
        rx.extend(data)
    except socket.timeout:
        pass

i = 0
counts = {}
examples = {}
while i < len(rx) - 1:
    sync0, sync1 = rx[i], rx[i+1]
    if sync0 == 0xAA and sync1 == 0xBB:
        ftype = rx[i+2]
        counts[ftype] = counts.get(ftype, 0) + 1
        if ftype not in examples:
            hi, lo = rx[i+3], rx[i+4]
            length = (hi << 8) | lo
            ex = rx[i:i+min(length+6, len(rx)-i)].hex()[:80]
            examples[ftype] = (length, ex)
        if i+5 < len(rx):
            hi, lo = rx[i+3], rx[i+4]
            length = (hi << 8) | lo
            i += 6 + length
        else:
            break
    elif sync0 == 0xAA and sync1 == 0xAA and i+2 < len(rx) and rx[i+2] == 0x01:
        counts['A'] = counts.get('A', 0) + 1
        if 'A' not in examples:
            examples['A'] = (68, rx[i:i+68].hex()[:80])
        i += 68
    elif sync0 == 0xFE:
        counts['mav'] = counts.get('mav', 0) + 1
        if 'mav' not in examples:
            examples['mav'] = (0, rx[i:i+8].hex())
        i += 8 + (rx[i+1] if i+1 < len(rx) else 0) + 2
    else:
        i += 1

print('Frame type counts:')
for k, v in sorted(counts.items(), key=lambda x: str(x[0])):
    label = f'0x{k:02X}' if isinstance(k, int) else k
    print(f'  {label}: {v} frames')
    if k in examples:
        print(f'    example ({examples[k][0]} B): {examples[k][1]}')
s.close()