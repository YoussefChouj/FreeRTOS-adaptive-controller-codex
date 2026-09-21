from pathlib import Path

fpath = "ground_station/comm/wifi_bridge.py"
content = Path(fpath).read_text(encoding="utf-8")

old_crc = """            if payload_len < 4 + 2:
                return None  # not enough for [T_MS_LE_4][CRC]
            # values_len may not be divisible by 4 if there's extra metadata."""

new_crc = """            if payload_len < 4 + 2:
                return None  # not enough for [T_MS_LE_4][CRC]
            
            from ground_station.livewatch.transport import crc16_ccitt
            crc_received = (frame[-2] << 8) | frame[-1]
            if crc16_ccitt(frame[:-2]) != crc_received:
                with self._stream_lock:
                    stats = self._stream_stats.setdefault(
                        slot, {"received": 0, "dropped": 0, "crc_errors": 0, "last_seq": -1})
                    stats["crc_errors"] += 1
                return None
            
            # values_len may not be divisible by 4 if there's extra metadata."""

content = content.replace(old_crc, new_crc)
Path(fpath).write_text(content, encoding="utf-8")
