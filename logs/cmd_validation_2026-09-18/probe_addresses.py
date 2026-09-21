"""Probe raw bytes vs DWARF read for imu_data.rol."""
from ground_station.livewatch.reader import LiveReader
import struct

r = LiveReader("OBJ/JX_FLY.axf")
r.connect()

# Read raw bytes around imu_data.rol address (per DWARF)
addr = 0x200002F8
data = r.peek(addr, 64)
print(f"Raw bytes at 0x{addr:08X} (64 B):")
print(data.hex())

# First 4 bytes as float (LE)
f = struct.unpack("<f", data[0:4])[0]
print(f"First 4 bytes as float LE: {f}")

# Last 4 bytes of previous 8-byte window
f2 = struct.unpack("<f", data[4:8])[0]
print(f"Bytes [4:8] as float LE: {f2}")

# Show all 16 floats
print("\nAll 16 floats in the 64-byte window:")
for i in range(0, 64, 4):
    fl = struct.unpack("<f", data[i:i+4])[0]
    print(f"  [+{i:2d}] 0x{addr+i:08X}: float={fl:14.4f}")

# Now read imu_data.rol by name
plan = r.plan(["imu_data.rol"])
sample = r.sample(plan)
rol = sample["imu_data.rol"]
print(f"\nDWARF read imu_data.rol: {rol}")

# And imu_data.pit
plan = r.plan(["imu_data.pit"])
sample = r.sample(plan)
pit = sample["imu_data.pit"]
print(f"DWARF read imu_data.pit: {pit}")

# Read raw bytes at pit address
addr_pit = 0x200002FC
data_pit = r.peek(addr_pit, 16)
print(f"\nRaw bytes at 0x{addr_pit:08X}: {data_pit.hex()}")
print(f"As float LE: {struct.unpack('<f', data_pit[0:4])[0]}")
r.close()