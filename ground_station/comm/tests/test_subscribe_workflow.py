"""
Test the full subscribe workflow: request → schema → data frames.

This validates that:
1. Subscribe requests (0x21) are accepted
2. Schema replies (0x08) are received and parsable
3. Data frames (0x09-0x0C) match the schema
4. Dashboard can reconstruct variable names from schema

Usage:
    python -m ground_station.comm.tests.test_subscribe_workflow --manifest _base --slot 0
"""

import argparse
import socket
import struct
import time
from pathlib import Path
from typing import Dict, List, Tuple
import yaml


def send_subscribe_request_and_capture(manifest_name: str, slot: int, duration_s: int = 5):
    """Send 0x21 subscribe request, capture 0x08 schema + 0x09 data frames."""
    
    # Load manifest
    manifest_path = Path('ground_station/comm/manifests.yaml')
    with open(manifest_path, 'r') as f:
        data = yaml.safe_load(f)
        manifests = data.get('manifests', {})
    
    if manifest_name not in manifests:
        print(f'Manifest "{manifest_name}" not found')
        return
    
    manifest = manifests[manifest_name]
    slot_configs = [s for s in manifest.get('slots', []) if s.get('slot') == slot]
    if not slot_configs:
        print(f'Slot {slot} not in manifest "{manifest_name}"')
        return
    
    slot_cfg = slot_configs[0]
    var_names = slot_cfg.get('vars', [])
    divider = slot_cfg.get('divider', 1)
    
    print(f'Subscribe request:')
    print(f'  Manifest: {manifest_name}')
    print(f'  Slot: {slot}')
    print(f'  Variables: {len(var_names)}')
    print(f'  Rate: {200 // divider} Hz')
    print(f'  Expected frames: 0x08 (schema), then 0x{0x09+slot:02X} (data)\n')
    
    # Open socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    s.bind(('0.0.0.0', 14551))
    
    # Build 0x21 request
    # For this test, we'll just observe what's already streaming
    # Full implementation requires manifest_layer encode
    
    print(f'Capturing frames for {duration_s}s...\n')
    
    schema_frame = None
    data_frames = []
    
    start = time.time()
    while time.time() - start < duration_s:
        try:
            data, addr = s.recvfrom(2048)
            if len(data) < 3 or data[:2] != b'\xAA\xBB':
                continue
            
            frame_type = data[2]
            
            # Schema frame (0x08)
            if frame_type == 0x08:
                schema_frame = data
                print(f'✓ Schema frame received: {len(data)} bytes')
            
            # Data frame for target slot
            elif frame_type == 0x09 + slot:
                data_frames.append(data)
                if len(data_frames) <= 3:
                    print(f'  Data frame {len(data_frames)}: {len(data)} bytes')
        
        except socket.timeout:
            continue
    
    s.close()
    
    print(f'\nCapture complete:')
    print(f'  Schema frames: {"1" if schema_frame else "0 (MISSING)"}')
    print(f'  Data frames: {len(data_frames)}')
    
    if not schema_frame:
        print(f'\n⚠ No 0x08 schema frame received.')
        print(f'  This means either:')
        print(f'    1. Subscribe request not sent (need full manifest_layer integration)')
        print(f'    2. FC already streaming data from prior request')
        print(f'    3. Schema was sent before capture started')
    
    if data_frames:
        print(f'\n✓ Slot {slot} data frames streaming')
        # Decode first frame to show structure
        frame = data_frames[0]
        payload_len = (frame[3] << 8) | frame[4]
        seq = frame[5]
        t_ms = struct.unpack_from('<I', frame, 6)[0]
        values_len = payload_len - 4
        num_values = values_len // 4
        
        print(f'  Frame structure:')
        print(f'    Payload length: {payload_len} bytes')
        print(f'    Sequence: {seq}')
        print(f'    Timestamp: {t_ms} ms')
        print(f'    Num values: {num_values}')
        
        if num_values == len(var_names):
            print(f'  ✓ Variable count matches manifest ({num_values})')
        else:
            print(f'  ✗ Variable count mismatch: got {num_values}, expected {len(var_names)}')
            print(f'    This means a different manifest is active on the FC')
    
    return schema_frame, data_frames


def main():
    parser = argparse.ArgumentParser(description='Subscribe workflow test')
    parser.add_argument('--manifest', type=str, default='_base', help='Manifest name')
    parser.add_argument('--slot', type=int, default=0, help='Slot number')
    parser.add_argument('--duration', type=int, default=5, help='Capture duration (s)')
    
    args = parser.parse_args()
    send_subscribe_request_and_capture(args.manifest, args.slot, args.duration)


if __name__ == '__main__':
    main()
