#!/usr/bin/env python3
"""Direct wifi_bridge subscribe test with real DWARF addresses."""
import sys
sys.path.insert(0, '.')
import time

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.symbols import SymbolResolver

def main():
    print("Creating WifiBridge (no auto-subscribe)...")
    bridge = WifiBridge(wifi_host='192.168.4.1', wifi_port=14550)
    bridge.start(auto_subscribe_boot_default=False)
    time.sleep(0.5)

    # Resolve real firmware addresses
    resolver = SymbolResolver('OBJ/JX_FLY.axf')
    test_vars = ['imu_data.rol', 'imu_data.pit', 'imu_data.yaw']
    
    from ground_station.livewatch.stream import StreamRange
    ranges = []
    for name in test_vars:
        try:
            sym = resolver.resolve(name)
            ranges.append(StreamRange(address=sym.address, size=sym.size, count=1, name=name))
            print(f"  {name}: addr=0x{sym.address:08X} size={sym.size}")
        except Exception as e:
            print(f"  {name}: ERROR {e}")
    resolver.close()

    if not ranges:
        print("No ranges resolved!")
        bridge.stop()
        return

    print(f"\nSubscribing to {len(ranges)} vars on slot 1...")
    try:
        bridge.subscribe_slot(slot=1, divider=4, ranges=ranges, transport=1)
    except Exception as e:
        print(f"subscribe_slot error: {e}")

    print("Waiting 8s for 0x08 response...")
    time.sleep(8)

    print(f"\nAfter wait:")
    print(f"  _recent_requests: {bridge._recent_requests}")
    print(f"  _recent_responses: {bridge._recent_responses}")
    print(f"  _request_states: {bridge._request_states}")
    print(f"  _stream_schemas: {list(bridge._stream_schemas.keys())}")
    print(f"  _stream_stats: {bridge._stream_stats}")
    
    bridge.stop()
    print("\nDone!")

if __name__ == "__main__":
    main()
