"""Test UDP connectivity to MicoAir and check for telemetry."""
import socket, time

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(3.0)
sock.bind(("0.0.0.0", 14552))  # Different port than the service (14550)

# Send a nudge to aim the MicoAir downlink at us on port 14552
sock.sendto(b"\x00", ("192.168.4.1", 14550))
print("Sent nudge to 192.168.4.1:14550")

# Try to receive any response
count = 0
deadline = time.time() + 3
while time.time() < deadline:
    sock.settimeout(deadline - time.time())
    try:
        data, addr = sock.recvfrom(1024)
        print(f"Got {len(data)} bytes from {addr}: {data[:30].hex()}")
        count += 1
        if count >= 5:
            break
    except socket.timeout:
        if count > 0:
            break
        print("No frames received in 3s")
        break

sock.close()
