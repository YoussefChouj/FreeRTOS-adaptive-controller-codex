"""Decode 0x09 frames using the actual firmware decoder to confirm dashboard vars flow."""
import socket, struct, time
from ground_station.livewatch.stream import (
    DATA_FRAME, decode_schema, MultiStreamDecoder, FRAME_OVERHEAD,
)
from ground_station.livewatch.transport import pop_frame
from ground_station.livewatch.symbols import SymbolResolver

resolver = SymbolResolver("OBJ/JX_FLY.axf")

# Build expected schema manually (since we can't get a 0x08 reply because slot 0 is busy)
# Use the boot_default_layout
from ground_station.comm.boot_default_layout import DASHBOARD_FRAME_A_VARS, DASHBOARD_FRAME_A_DIVIDER

ranges = []
for name in DASHBOARD_FRAME_A_VARS:
    try:
        sym = resolver.resolve(name)
        from ground_station.livewatch.stream import StreamRange
        ranges.append(StreamRange(address=sym.address, size=sym.size, count=1, name=name, fmt="f" if sym.size == 4 else ("B" if sym.size == 1 else "H")))
    except Exception as e:
        print(f'fail: {name}: {e}')

print(f'Built {len(ranges)} StreamRange objects')

# Manually construct a StreamSchema (skip 0x08)
from ground_station.livewatch.stream import StreamSchema
total_bytes = sum(r.size * r.count for r in ranges)
schema = StreamSchema(
    divider=DASHBOARD_FRAME_A_DIVIDER,
    transport=1,  # USART3
    total_bytes=total_bytes,
    ranges=tuple(ranges),
    slot=0,
)
print(f'Expected: {len(ranges)} vars, {total_bytes} B payload, frame {FRAME_OVERHEAD + total_bytes} B')
print(f'Expected: 1 (sync) + 1 (type) + 2 (len) + 1 (SEQ) + {total_bytes + 4} (timestamp+payload) + 2 (CRC16) = {6 + total_bytes + 4 + 2} B total')

# Capture frames and decode
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0', 14550))
s.settimeout(0.5)
s.sendto(b'\x00', ('192.168.4.1', 14550))

rx = bytearray()
deadline = time.monotonic() + 3.0
while time.monotonic() < deadline:
    try:
        data, _ = s.recvfrom(8192)
        rx.extend(data)
    except socket.timeout:
        pass

# Use MultiStreamDecoder to parse
decoder = MultiStreamDecoder([schema])
samples = decoder.feed(bytes(rx))
print(f'\nDecoded {len(samples)} samples from 0x09 frames')
if samples:
    slot, seq, t_ms, values = samples[0]
    print(f'\nFirst sample: slot={slot} seq={seq} t_ms={t_ms}')
    print(f'Values (count={len(values)}):')
    for k, v in values.items():
        if isinstance(v, list):
            print(f'  {k} = {v}')
        else:
            print(f'  {k:30s} = {v}')

    # Verify a few critical keys
    print(f'\n=== Dashboard-needed key check ===')
    critical = ['imu_data.rol', 'imu_data.pit', 'imu_data.yaw',
                'DroneStatus.ARM_Status', 'DroneStatus.FlyMode',
                'real_voltage', 'mrac_state.pitch.e', 'mrac_state.roll.u_ad',
                'mrac_state.z_rate.e']
    for c in critical:
        if c in values:
            print(f'  OK   {c:30s} = {values[c]}')
        else:
            print(f'  MISS {c}')

s.close()