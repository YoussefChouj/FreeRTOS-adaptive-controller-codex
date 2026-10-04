#!/usr/bin/env python3
"""Verify subscribe stream data frames are arriving."""
import sys
sys.path.insert(0, '.')
import time

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.symbols import SymbolResolver
from ground_station.livewatch.stream import StreamRange

def main():
    bridge = WifiBridge(wifi_host='192.168.4.1', wifi_port=14550)
    bridge.start(auto_subscribe_boot_default=False)
    time.sleep(0.5)

    resolver = SymbolResolver('OBJ/JX_FLY.axf')
    test_vars = ['imu_data.rol', 'imu_data.pit', 'imu_data.yaw']
    ranges = []
    for name in test_vars:
        sym = resolver.resolve(name)
        ranges.append(StreamRange(address=sym.address, size=sym.size, count=1, name=name))
    resolver.close()

    print(f"Subscribing to {len(ranges)} vars on slot 1...")
    bridge.subscribe_slot(slot=1, divider=4, ranges=ranges, transport=1)
    
    # Wait for schema + a few data frames
    print("Waiting 5s for data frames...")
    time.sleep(5)

    print(f"\nStream schemas: {list(bridge._stream_schemas.keys())}")
    print(f"Stream stats: {bridge._stream_stats}")
    
    # Get telemetry
    telem, ts = bridge.get_telemetry()
    print(f"Telemetry keys: {list(telem.keys())}")
    for k, v in telem.items():
        if k != '_meta':
            print(f"  {k}: {v}")
    
    bridge.stop()

if __name__ == "__main__":
    main()
