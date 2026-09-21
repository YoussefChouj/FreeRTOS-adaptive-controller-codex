#!/usr/bin/env python3
"""Read firmware subscribe and telemetry state."""
import sys, struct
sys.path.insert(0, '.')
from ground_station.livewatch.symbols import SymbolResolver
from ground_station.livewatch.reader import LiveReader

resolver_path = 'OBJ/JX_FLY.axf'
r = SymbolResolver(resolver_path)
addrs = {}
for name in ['UA3RxFrameCnt', 'UA3RxLastLen', 'UA5RxSubscribePending',
             'UA5RxSubscribeLen', 'Subscribe_RxTransport', 'g_telemetry_mode']:
    try:
        s = r.resolve(name)
        addrs[name] = (s.address, s.size)
        print(f'{name}: addr=0x{s.address:08X} size={s.size}')
    except Exception as e:
        print(f'{name}: ERROR {e}')
r.close()

reader = LiveReader(resolver_path)
reader.connect()

for name, (addr, size) in addrs.items():
    try:
        raw = reader.read_raw(addr, size)
        if size == 1:
            val = raw[0]
        elif size == 2:
            val = struct.unpack('<H', raw)[0]
        elif size == 4:
            val = struct.unpack('<I', raw)[0]
        else:
            val = raw.hex()

        if name == 'Subscribe_RxTransport':
            val = f'{val} ({"UART5" if val == 0 else "USART3" if val == 1 else f"UNKNOWN({val})"})'
        elif name == 'g_telemetry_mode':
            val = f'{val} ({"LEGACY" if val == 0 else "MIXED" if val == 1 else "SUBSCRIBE_ONLY" if val == 2 else f"UNKNOWN({val})"})'
        elif name == 'UA5RxSubscribeLen':
            val = f'{val} bytes'
        elif name == 'UA3RxFrameCnt':
            val = f'{val} frames'
        elif name == 'UA3RxLastLen':
            val = f'{val} bytes'

        print(f'{name} = {val}')
    except Exception as e:
        print(f'{name}: READ ERROR {e}')

reader.close()
