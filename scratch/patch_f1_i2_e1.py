import sys, struct
from pathlib import Path

fpath = "ground_station/comm/wifi_bridge.py"
content = Path(fpath).read_text(encoding="utf-8")

# I2
content = content.replace('_FRAME_A_TAIL = b"\\x00\\x00\\x80\\x7f"', '_FRAME_A_TAIL = b"\\x00\\x00\\x80\\x7f"\n\n\ndef _rpm_scalar_keys(rpm_list: list) -> dict:\n    return {f"motor.rpm_{i}": int(rpm_list[i]) for i in range(len(rpm_list))}')

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
                "c.seq": seq,
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
                "c.seq": seq,
            }
            res.update(_rpm_scalar_keys(list(rpm_vals)))
            return res"""
content = content.replace(old_fc, new_fc)

# E1
old_map = """            "mrac_state.z_rate.e":   "mrac.z.e",
            "mrac_state.z_rate.u_ad": "mrac.z.u_ad",
        }"""
new_map = """            "mrac_state.z_rate.e":   "mrac.z.e",
            "mrac_state.z_rate.u_ad": "mrac.z.u_ad",
            "s_ekf.x[0]": "ekf.vel_x",
            "s_ekf.x[1]": "ekf.vel_y",
            "s_ekf.x[2]": "ekf.vel_z",
            "s_ekf.x[3]": "ekf.bias_accel_x",
            "s_ekf.x[4]": "ekf.bias_accel_y",
            "s_ekf.x[5]": "ekf.bias_accel_z",
            "s_ekf.x[6]": "ekf.bias_gyro_x",
            "s_ekf.x[7]": "ekf.bias_gyro_y",
            "s_ekf.x[8]": "ekf.bias_gyro_z",
        }"""
content = content.replace(old_map, new_map)

# F1
content = content.replace(
    'count=1,\n                        name=var_name\n                    ))',
    'count=1,\n                        name=var_name,\n                        fmt=symbol.fmt\n                    ))'
)

old_dsf = """            # values_len may not be divisible by 4 if there's extra metadata.
            # Decode as many complete floats as possible.
            n_floats = values_len // 4
            seq = frame[5]
            t_ms = struct.unpack_from("<I", frame, 6)[0]
            values = struct.unpack_from(f"<{n_floats}f", frame, 10) if n_floats > 0 else ()
        except (struct.error, IndexError):
            return None

        names: list[str] = []
        schema = None
        with self._stream_lock:
            schema = self._stream_schemas.get(slot)
        if schema is not None and hasattr(schema, "ranges"):"""
new_dsf = """            # values_len may not be divisible by 4 if there's extra metadata.
            seq = frame[5]
            t_ms = struct.unpack_from("<I", frame, 6)[0]

            schema = None
            with self._stream_lock:
                schema = self._stream_schemas.get(slot)
            
            values_list = []
            if schema is not None and hasattr(schema, "ranges"):
                offset = 10
                _SIZE_FMT = {1: "b", 2: "h", 4: "f", 8: "d"}
                for rng in schema.ranges:
                    code = rng.fmt or _SIZE_FMT.get(rng.size, "f")
                    fmt_str = f"<{rng.count}{code}"
                    try:
                        unpacked = struct.unpack_from(fmt_str, frame, offset)
                        values_list.extend(unpacked)
                    except struct.error:
                        pass
                    offset += struct.calcsize(fmt_str)
                values = tuple(values_list)
            else:
                n_floats = values_len // 4
                values = struct.unpack_from(f"<{n_floats}f", frame, 10) if n_floats > 0 else ()
        except (struct.error, IndexError):
            return None

        names: list[str] = []
        if schema is not None and hasattr(schema, "ranges"):"""
content = content.replace(old_dsf, new_dsf)

Path(fpath).write_text(content, encoding="utf-8")
