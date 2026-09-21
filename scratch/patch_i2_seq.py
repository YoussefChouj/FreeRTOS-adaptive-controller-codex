from pathlib import Path

fpath = "ground_station/comm/wifi_bridge.py"
content = Path(fpath).read_text(encoding="utf-8")

old_fc = """            rol, pit, yaw = struct.unpack_from("<3f", payload, 0)
            gx, gy, gz = struct.unpack_from("<3f", payload, 12)
            ex, ey = struct.unpack_from("<2f", payload, 24)
            alt = struct.unpack_from("<f", payload, 32)[0]
            rpm_vals = struct.unpack_from("<4H", payload, 40)
            seq = struct.unpack_from("<H", payload, 48)[0]
            return {
                "c.roll": rol, "c.pitch": pit, "c.yaw": yaw,
                "c.gyro_x": gx, "c.gyro_y": gy, "c.gyro_z": gz,
                "c.earth_x": ex, "c.earth_y": ey, "c.altitude": alt,
                "c.rpm": list(rpm_vals),
                "c.seq": float(seq),
            }"""
new_fc = """            rol, pit, yaw = struct.unpack_from("<3f", payload, 0)
            gx, gy, gz = struct.unpack_from("<3f", payload, 12)
            ex, ey = struct.unpack_from("<2f", payload, 24)
            alt = struct.unpack_from("<f", payload, 32)[0]
            rpm_vals = struct.unpack_from("<4H", payload, 36)
            seq = struct.unpack_from("<H", payload, 44)[0]
            res = {
                "c.roll": rol, "c.pitch": pit, "c.yaw": yaw,
                "c.gyro_x": gx, "c.gyro_y": gy, "c.gyro_z": gz,
                "c.earth_x": ex, "c.earth_y": ey, "c.altitude": alt,
                "c.rpm": list(rpm_vals),
                "c.seq": float(seq),
            }
            res.update(_rpm_scalar_keys(list(rpm_vals)))
            return res"""
content = content.replace(old_fc, new_fc)

Path(fpath).write_text(content, encoding="utf-8")
