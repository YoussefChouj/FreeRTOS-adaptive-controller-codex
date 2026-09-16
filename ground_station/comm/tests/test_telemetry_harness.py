"""
Telemetry testing harness — capture, replay, decode, validate.

Usage:
    # Capture 10s of real frames from FC
    python -m ground_station.comm.tests.test_telemetry_harness capture --duration 10

    # Replay captured frames and validate decoding
    python -m ground_station.comm.tests.test_telemetry_harness replay --file frames_2026-08-27.bin

    # Test specific frame type
    python -m ground_station.comm.tests.test_telemetry_harness test-frame 0x01

    # Validate all manifests against captured frames
    python -m ground_station.comm.tests.test_telemetry_harness validate-manifest
"""

import argparse
import socket
import struct
import time
from pathlib import Path
from typing import Dict, List, Tuple
import yaml

FRAME_HEADER = b'\xAA\xBB'

def capture_frames(duration_s: int = 10, output_file: Path = None) -> Path:
    """Capture raw UDP frames from FC for specified duration."""
    if output_file is None:
        timestamp = time.strftime('%Y-%m-%d_%H-%M-%S')
        output_file = Path(f'frames_{timestamp}.bin')
    
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    s.bind(('0.0.0.0', 14551))
    
    print(f'Capturing frames for {duration_s}s → {output_file}')
    s.sendto(b'\x00', ('192.168.4.1', 14550))
    
    frames = []
    start = time.time()
    while time.time() - start < duration_s:
        try:
            data, addr = s.recvfrom(2048)
            if len(data) > 2 and data[:2] == FRAME_HEADER:
                frames.append(data)
                print(f'  [{len(frames):3d}] Frame 0x{data[2]:02X}, {len(data)} bytes')
        except socket.timeout:
            continue
    
    s.close()
    
    with open(output_file, 'wb') as f:
        for frame in frames:
            f.write(struct.pack('<H', len(frame)))
            f.write(frame)
    
    print(f'\nCaptured {len(frames)} frames → {output_file}')
    return output_file


def load_frames(file: Path) -> List[bytes]:
    """Load captured frames from binary file."""
    frames = []
    with open(file, 'rb') as f:
        while True:
            len_data = f.read(2)
            if not len_data:
                break
            frame_len = struct.unpack('<H', len_data)[0]
            frame = f.read(frame_len)
            frames.append(frame)
    return frames


def decode_subscribe_frame(frame: bytes) -> Dict:
    """Decode subscribe data frame (0x09-0x0C).
    
    Layout: [0xAA][0xBB][TYPE][LEN_HI][LEN_LO][SEQ][T_MS uint32 LE][values...][CRC16 BE]
    TYPE = 0x09 + slot (0x09=slot0, 0x0A=slot1, 0x0B=slot2, 0x0C=slot3)
    """
    if len(frame) < 12:
        return {'error': 'frame too short'}
    
    frame_type = frame[2]
    if frame_type < 0x09 or frame_type > 0x0C:
        return {'error': f'not a subscribe frame: 0x{frame_type:02X}'}
    
    slot = frame_type - 0x09
    payload_len = (frame[3] << 8) | frame[4]
    seq = frame[5]
    
    if len(frame) != 6 + payload_len + 2:
        return {'error': f'length mismatch: expected {6 + payload_len + 2}, got {len(frame)}'}
    
    if payload_len < 6:  # min: 4-byte timestamp + 2-byte CRC
        return {'error': 'payload too short'}
    
    t_ms = struct.unpack_from('<I', frame, 6)[0]
    
    # Extract float values
    values_len = payload_len - 4  # exclude timestamp
    if values_len % 4 != 0:
        return {'error': f'values_len {values_len} not multiple of 4'}
    
    num_values = values_len // 4
    try:
        values = struct.unpack_from(f'<{num_values}f', frame, 10)
    except struct.error:
        return {'error': 'failed to unpack float values'}
    
    return {
        'frame_type': f'0x{frame_type:02X}',
        'slot': slot,
        'seq': seq,
        't_ms': t_ms,
        'payload_len': payload_len,
        'num_values': num_values,
        'values': list(values),
        'frame_size': len(frame),
    }


def decode_legacy_frame(frame: bytes) -> Dict:
    """Decode legacy frames (0x01-0x08) for comparison."""
    if len(frame) < 4:
        return {'error': 'frame too short'}
    
    frame_type = frame[2]
    result = {
        'frame_type': f'0x{frame_type:02X}',
        'length': frame[3] if len(frame) > 3 else None,
        'raw_payload': frame[4:].hex() if len(frame) > 4 else '',
    }
    
    # Frame 0x01 — ARM/FlyMode (legacy)
    if frame_type == 0x01 and len(frame) > 5:
        result['ARM_Status'] = frame[4]
        result['FlyMode'] = frame[5]
    
    return result



def replay_frames(file: Path):
    """Replay captured frames and decode each one."""
    frames = load_frames(file)
    print(f'\nReplaying {len(frames)} frames from {file}\n')
    
    frame_counts = {}
    subscribe_frames = []
    legacy_frames = []
    
    for i, frame in enumerate(frames):
        if len(frame) < 3:
            continue
        
        frame_type = f'0x{frame[2]:02X}'
        frame_counts[frame_type] = frame_counts.get(frame_type, 0) + 1
        
        # Decode based on type
        if 0x09 <= frame[2] <= 0x0C:
            result = decode_subscribe_frame(frame)
            subscribe_frames.append(result)
        else:
            result = decode_legacy_frame(frame)
            legacy_frames.append(result)
    
    # Summary
    print('Frame type distribution:')
    for ft, cnt in sorted(frame_counts.items()):
        print(f'  {ft}: {cnt} frames')
    
    # Subscribe frames analysis
    if subscribe_frames:
        print(f'\n=== Subscribe Frames ({len(subscribe_frames)} total) ===')
        slots_seen = set(f['slot'] for f in subscribe_frames if 'slot' in f)
        print(f'Slots active: {sorted(slots_seen)}')
        
        for slot in sorted(slots_seen):
            slot_frames = [f for f in subscribe_frames if f.get('slot') == slot]
            if not slot_frames:
                continue
            
            print(f'\nSlot {slot}: {len(slot_frames)} frames')
            first = slot_frames[0]
            if 'error' not in first:
                print(f'  Num values: {first["num_values"]}')
                print(f'  Frame size: {first["frame_size"]} bytes')
                print(f'  Sample values (first frame): {first["values"][:5]}...')
            else:
                print(f'  ERROR: {first["error"]}')
    
    # Legacy frames
    if legacy_frames:
        print(f'\n=== Legacy Frames ({len(legacy_frames)} total) ===')
        for frame in legacy_frames[:3]:  # Show first 3
            print(f'  {frame}')


def test_frame_type(file: Path, frame_type_hex: str):
    """Test decoding of specific frame type."""
    frames = load_frames(file)
    frame_type_int = int(frame_type_hex, 16)
    
    matching = [f for f in frames if len(f) > 2 and f[2] == frame_type_int]
    print(f'\nFound {len(matching)} frames of type {frame_type_hex}')
    
    if not matching:
        print('No frames to test')
        return
    
    # Decode first frame
    frame = matching[0]
    print(f'\nFirst frame ({len(frame)} bytes):')
    print(f'  Raw (first 64 bytes): {frame[:64].hex()}')
    
    if 0x09 <= frame_type_int <= 0x0C:
        result = decode_subscribe_frame(frame)
    else:
        result = decode_legacy_frame(frame)
    
    print(f'\n  Decoded:')
    for k, v in result.items():
        if k == 'values' and isinstance(v, list) and len(v) > 10:
            print(f'    {k}: [{v[0]}, {v[1]}, ..., {v[-1]}] ({len(v)} values)')
        else:
            print(f'    {k}: {v}')




def validate_manifest(file: Path, manifest_name: str = None):
    """Validate that captured frames match manifest expectations."""
    manifest_path = Path('ground_station/comm/manifests.yaml')
    if not manifest_path.exists():
        print(f'Manifest not found: {manifest_path}')
        return
    
    with open(manifest_path, 'r') as f:
        data = yaml.safe_load(f)
        manifests = data.get('manifests', {})
    
    frames = load_frames(file)
    print(f'\nValidating {len(frames)} frames against manifests\n')
    
    # Group subscribe frames by slot
    by_slot = {}
    for frame in frames:
        if len(frame) > 2 and 0x09 <= frame[2] <= 0x0C:
            slot = frame[2] - 0x09
            if slot not in by_slot:
                by_slot[slot] = []
            by_slot[slot].append(frame)
    
    print(f'Subscribe slots active: {sorted(by_slot.keys())}')
    
    # Validate each slot
    for slot, slot_frames in sorted(by_slot.items()):
        print(f'\n=== Slot {slot}: {len(slot_frames)} frames ===')
        
        # Decode first frame
        decoded = decode_subscribe_frame(slot_frames[0])
        if 'error' in decoded:
            print(f'  Decode error: {decoded["error"]}')
            continue
        
        num_vars = decoded['num_values']
        print(f'  Variables per frame: {num_vars}')
        print(f'  Frame size: {decoded["frame_size"]} bytes')
        
        # Check if any manifest matches this slot
        if manifest_name and manifest_name in manifests:
            manifest = manifests[manifest_name]
            slot_configs = [s for s in manifest.get('slots', []) if s.get('slot') == slot]
            if slot_configs:
                slot_cfg = slot_configs[0]
                expected_vars = len(slot_cfg.get('vars', []))
                print(f'  Manifest "{manifest_name}" slot {slot}: expects {expected_vars} vars')
                if num_vars == expected_vars:
                    print(f'  ✓ Variable count matches')
                else:
                    print(f'  ✗ Mismatch: got {num_vars}, expected {expected_vars}')
        
        # Check sequence consistency
        sequences = [decode_subscribe_frame(f).get('seq', -1) for f in slot_frames]
        gaps = sum(1 for i in range(1, len(sequences)) if sequences[i] != (sequences[i-1] + 1) % 256)
        print(f'  Sequence gaps: {gaps} / {len(sequences)-1} frames')


def send_subscribe_request(manifest_name: str = '_base', slot: int = 0):
    """Send 0x21 subscribe request and capture response."""
    manifest_path = Path('ground_station/comm/manifests.yaml')
    if not manifest_path.exists():
        print(f'Manifest not found: {manifest_path}')
        return
    
    with open(manifest_path, 'r') as f:
        data = yaml.safe_load(f)
        manifests = data.get('manifests', {})
    
    if manifest_name not in manifests:
        print(f'Manifest "{manifest_name}" not found. Available: {list(manifests.keys())}')
        return
    
    manifest = manifests[manifest_name]
    slot_configs = [s for s in manifest.get('slots', []) if s.get('slot') == slot]
    if not slot_configs:
        print(f'Slot {slot} not defined in manifest "{manifest_name}"')
        return
    
    slot_cfg = slot_configs[0]
    var_names = slot_cfg.get('vars', [])
    divider = slot_cfg.get('divider', 1)
    
    print(f'\nSending subscribe request:')
    print(f'  Manifest: {manifest_name}')
    print(f'  Slot: {slot}')
    print(f'  Variables: {len(var_names)}')
    print(f'  Rate: {200 // divider} Hz (divider={divider})')
    
    # Build 0x21 request (structure from API/subscribe.h)
    # [0xCC 0xDE 0x21 slot divider n_vars var_name_bytes...]
    # This is simplified — real implementation needs proper encoding
    
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5.0)
    s.bind(('0.0.0.0', 14551))
    
    # For now, just report what would be sent
    print(f'\n  (Full subscribe request encoding requires manifest_layer integration)')
    print(f'  Expected response: 0x08 schema frame, then 0x{0x09+slot:02X} data frames')
    
    s.close()


def uplink_echo_test():
    """Send command to FC and verify response."""
    print('\nUplink echo test — send FlyMode change command')
    
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    s.bind(('0.0.0.0', 14551))
    
    # Send FlyMode command (0xCC 0xDE 0x04 idx=1 val=0.0) — SDK mode
    # Structure: [0xCC 0xDE CMD idx_u8 val_f32_LE]
    test_cmd = struct.pack('<3sBf', b'\xCC\xDE\x04', 1, 0.0)
    
    print(f'Sending SDK mode command: {test_cmd.hex()}')
    s.sendto(test_cmd, ('192.168.4.1', 14550))
    
    try:
        data, addr = s.recvfrom(2048)
        print(f'Response: {data.hex()} ({len(data)} bytes)')
        
        # Check if it's a telemetry frame
        if len(data) > 2 and data[:2] == b'\xAA\xBB':
            frame_type = data[2]
            print(f'  Frame type: 0x{frame_type:02X}')
            if 0x09 <= frame_type <= 0x0C:
                decoded = decode_subscribe_frame(data)
                print(f'  Subscribe slot {frame_type - 0x09}: {decoded.get("num_values", 0)} values')
    except socket.timeout:
        print('No response (timeout)')
    
    s.close()


def main():
    parser = argparse.ArgumentParser(description='Telemetry testing harness')
    subparsers = parser.add_subparsers(dest='command', help='Command')
    
    # Capture
    p_capture = subparsers.add_parser('capture', help='Capture frames from FC')
    p_capture.add_argument('--duration', type=int, default=10, help='Capture duration (s)')
    p_capture.add_argument('--output', type=Path, help='Output file')
    
    # Replay
    p_replay = subparsers.add_parser('replay', help='Replay captured frames')
    p_replay.add_argument('--file', type=Path, required=True, help='Captured frames file')
    
    # Test frame type
    p_test = subparsers.add_parser('test-frame', help='Test specific frame type')
    p_test.add_argument('frame_type', help='Frame type (e.g. 0x09)')
    p_test.add_argument('--file', type=Path, required=True, help='Captured frames file')
    
    # Validate manifest
    p_validate = subparsers.add_parser('validate-manifest', help='Validate manifest')
    p_validate.add_argument('--file', type=Path, required=True, help='Captured frames file')
    p_validate.add_argument('--manifest', type=str, help='Manifest name to validate against')
    
    # Subscribe request
    p_sub = subparsers.add_parser('subscribe', help='Send subscribe request')
    p_sub.add_argument('--manifest', type=str, default='_base', help='Manifest name')
    p_sub.add_argument('--slot', type=int, default=0, help='Slot number')
    
    # Uplink test
    subparsers.add_parser('uplink', help='Uplink echo test')
    
    args = parser.parse_args()
    
    if args.command == 'capture':
        capture_frames(args.duration, args.output)
    elif args.command == 'replay':
        replay_frames(args.file)
    elif args.command == 'test-frame':
        test_frame_type(args.file, args.frame_type)
    elif args.command == 'validate-manifest':
        validate_manifest(args.file, args.manifest)
    elif args.command == 'subscribe':
        send_subscribe_request(args.manifest, args.slot)
    elif args.command == 'uplink':
        uplink_echo_test()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

