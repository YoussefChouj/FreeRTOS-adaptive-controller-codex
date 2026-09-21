#!/usr/bin/env python3
"""Manual WiFi subscribe test: send a tiny 0x21 request and capture the response."""

import socket
import struct
import time
import sys

WIFI_HOST = "192.168.4.1"
WIFI_PORT = 14550
MY_PORT = 14553

def build_subscribe_request(address: int, size: int, count: int, 
                           slot: int, divider: int, transport: int) -> bytes:
    """Build a 0xCC 0xDE 0x21 subscribe request frame."""
    # Header
    sync = bytes([0xCC, 0xDE])
    cmd = bytes([0x21])
    
    # Payload for 0x21: [divider, transport, slot, N_ranges * (addr4, size2, count2)]
    n_ranges = 1
    payload = bytes([divider, transport, slot])  # config bytes
    # One range: address (LE32) + size (LE16) + count (LE16)
    addr_bytes = struct.pack('<I', address)
    size_bytes = struct.pack('<H', size)
    count_bytes = struct.pack('<H', count)
    payload += addr_bytes + size_bytes + count_bytes
    
    payload_len = len(payload)
    len_bytes = struct.pack('<H', payload_len)
    
    # CRC8 XOR of payload
    crc = 0
    for b in payload:
        crc ^= b
    crc_byte = bytes([crc])
    
    return sync + cmd + len_bytes + payload + crc_byte

def raw_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
    return crc

def main():
    # Subscribe to one simple variable: "imu_data.rol" (single float32)
    # We'll use a known address. For now, use a test address 0x08004000 (flash)
    test_addr = 0x08004000
    test_size = 4  # 4 bytes (float32)
    test_count = 1
    
    request = build_subscribe_request(
        address=test_addr,
        size=test_size,
        count=test_count,
        slot=1,  # Use slot 1 to not interfere with slot 0
        divider=4,
        transport=1  # USART3/WiFi
    )
    
    print(f"Request: {request.hex()}")
    print(f"Request len: {len(request)} bytes")
    print(f"  sync=0xCC 0xDE cmd=0x21 payload_len={len(request)-6} divider=4 slot=1 transport=1")
    print(f"  addr=0x{test_addr:08X} size={test_size} count={test_count}")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(2.0)
    
    # Bind to receive responses
    sock.bind(("0.0.0.0", MY_PORT))
    
    # Send the request
    print(f"\nSending to {WIFI_HOST}:{WIFI_PORT}...")
    sock.sendto(request, (WIFI_HOST, WIFI_PORT))
    
    # Listen for responses for 5 seconds
    deadline = time.time() + 5.0
    response_count = 0
    while time.time() < deadline:
        sock.settimeout(deadline - time.time())
        try:
            data, addr = sock.recvfrom(4096)
            response_count += 1
            print(f"\n=== Response #{response_count} from {addr} ({len(data)} bytes) ===")
            print(f"  Raw hex: {data.hex()}")
            
            # Parse as subscribe reply
            if len(data) >= 4 and data[0] == 0xCC and data[1] == 0xDE:
                cmd = data[2]
                payload_len = (data[3] << 8) | data[4]
                print(f"  Frame: 0xCC 0xDE cmd=0x{cmd:02X} payload_len={payload_len}")
                if cmd == 0x08:
                    n_ranges = data[5] if len(data) > 5 else 0
                    print(f"  Schema reply: n_ranges={n_ranges}")
                elif cmd == 0x7F:
                    msg_end = data.find(0, 5)
                    if msg_end < 0:
                        msg_end = len(data)
                    print(f"  Error reply: {data[5:msg_end]}")
                elif cmd == 0x07:
                    count = data[5] if len(data) > 5 else 0
                    print(f"  Reply: count={count}")
            elif len(data) >= 2 and data[0] == 0xAA and data[1] == 0xBB:
                ftype = data[2]
                plen = (data[3] << 8) | data[4]
                print(f"  Frame A/B/C type=0x{ftype:02X} len={plen}")
            else:
                print(f"  Unknown frame type: 0x{data[0]:02X} 0x{data[1]:02X}")
        except socket.timeout:
            print("\nTimeout waiting for response")
            break
    
    print(f"\nTotal responses: {response_count}")
    sock.close()

if __name__ == "__main__":
    main()
