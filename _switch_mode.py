#!/usr/bin/env python3
"""Switch the drone from SUBSCRIBE_ONLY back to MIXED telemetry mode."""

import socket
import struct
import time
import sys

WIFI_HOST = "192.168.4.1"
WIFI_PORT = 14550
MY_PORT = 14554

def build_telemetry_mode_command(index: int) -> bytes:
    """Build a 0xCC 0xDD CMD 0x0F telemetry mode command frame.
    
    Format: [0xCC][0xDD][CMD=0x0F][INDEX=index][VALUE=float32][CRC8]
    Total: 9 bytes
    """
    sync = bytes([0xCC, 0xDD])
    cmd = bytes([0x0F])
    idx = bytes([index])
    value = struct.pack('<f', 0.0)  # value is ignored for CMD 0x0F
    
    body = cmd + idx + value
    crc = 0
    for b in body:
        crc ^= b
    
    return sync + body + bytes([crc])

def raw_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc

def main():
    # Build MIXED mode command (index 101)
    mode_cmd = build_telemetry_mode_command(101)  # MIXED
    print(f"MIXED mode cmd: {mode_cmd.hex()} ({len(mode_cmd)} bytes)")
    body = mode_cmd[2:8]  # cmd + idx + value
    crc_check = raw_crc8(body)
    print(f"  CRC check: {crc_check:02X} (frame last byte: {mode_cmd[-1]:02X}) {'OK' if crc_check == mode_cmd[-1] else 'MISMATCH'}")

    # Also build LEGACY (100) and SUBSCRIBE_ONLY (102) for reference
    for idx, name in [(100, "LEGACY"), (101, "MIXED"), (102, "SUBSCRIBE_ONLY")]:
        frame = build_telemetry_mode_command(idx)
        print(f"  {name} (idx={idx}): {frame.hex()}")
    
    # Send the MIXED mode switch
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(2.0)
    sock.bind(("0.0.0.0", MY_PORT))
    
    print(f"\nSending MIXED mode switch to {WIFI_HOST}:{WIFI_PORT}...")
    sock.sendto(mode_cmd, (WIFI_HOST, WIFI_PORT))
    print("Sent!")
    
    # Wait a bit for the mode to take effect
    time.sleep(0.5)
    
    # Now send a subscribe request and wait for 0x08 response
    # Use a tiny 2-range subscribe to slot 1
    # Build: 0xCC 0xDE [0x21] [LEN=13] [divider=4][transport=1][slot=1] [addr4][size2][count2] * 1 [CRC]
    n_ranges = 1
    divider = 4
    transport = 1
    slot = 1
    test_addr = 0x08004000  # test address
    test_size = 4
    test_count = 1
    
    payload = bytes([divider, transport, slot])
    addr_b = struct.pack('<I', test_addr)
    size_b = struct.pack('<H', test_size)
    count_b = struct.pack('<H', test_count)
    payload += addr_b + size_b + count_b
    payload_len = len(payload)
    
    crc = raw_crc8(payload)
    
    subscribe_frame = bytes([0xCC, 0xDE, 0x21]) + struct.pack('<H', payload_len) + payload + bytes([crc])
    print(f"\nSubscribe frame: {subscribe_frame.hex()} ({len(subscribe_frame)} bytes)")
    
    print("\nWaiting for responses (5 seconds)...")
    sock.settimeout(5.0)
    deadline = time.time() + 5.0
    response_count = 0
    has_schema = False
    
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            response_count += 1
            
            if len(data) >= 2 and data[0] == 0xCC and data[1] == 0xDE:
                cmd = data[2]
                if cmd == 0x08:
                    n_ranges = data[5] if len(data) > 5 else 0
                    print(f"\n*** SCHEMA REPLY received! ***")
                    print(f"  n_ranges: {n_ranges}")
                    has_schema = True
                    # Decode first range
                    if len(data) >= 14 and n_ranges >= 1:
                        addr_r = struct.unpack('<I', data[6:10])[0]
                        size_r = struct.unpack('<H', data[10:12])[0]
                        count_r = struct.unpack('<H', data[12:14])[0]
                        print(f"  range[0]: addr=0x{addr_r:08X} size={size_r} count={count_r}")
                    break
                elif cmd == 0x7F:
                    msg_end = data.find(0, 6)
                    if msg_end < 0:
                        msg_end = len(data)
                    print(f"\nSubscribe REJECTED (0x7F): {data[5:msg_end]}")
                elif cmd == 0x07:
                    count = data[5] if len(data) > 5 else 0
                    print(f"\nOne-shot reply (0x07): count={count}")
                else:
                    print(f"\n0xCC 0xDE cmd=0x{cmd:02X} len={len(data)}")
            elif len(data) >= 2 and data[0] == 0xAA and data[1] == 0xBB:
                ftype = data[2]
                plen = (data[3] << 8) | data[4]
                print(f"  Frame A/B/C type=0x{ftype:02X} len={plen}", flush=True)
            else:
                print(f"  Unknown: {data[:20].hex()}")
        except socket.timeout:
            break
    
    print(f"\nTotal responses: {response_count}")
    print(f"Schema received: {has_schema}")
    sock.close()

if __name__ == "__main__":
    main()
