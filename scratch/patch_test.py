from pathlib import Path

fpath = "ground_station/comm/tests/test_wifi_bridge_stream.py"
content = Path(fpath).read_text(encoding="utf-8")

old_test = """        # Config (6 B): n_ranges, divider, transport, slot, total_hi, total_lo
        config = bytes([n_ranges, divider, transport, slot,
                        (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
        # Range (8 B): address=0x100000, size=4, count=1
        rng = struct.pack("<IHH", 0x00100000, 4, 1)
        payload = config + rng  # 14 B (6+8); firmware expects 13 B (5+n*8).
        # Fix: trim to what firmware will emit: 5+n_ranges*8 = 13 B = config[6]+rng[7].
        # The range in firmware is 8 B but config already contains n_ranges=1 at payload[0],
        # so the firmware writes the range at out[11..18] (8 bytes: 4 addr + 2 size + 2 count).
        # Total payload = 5 (config excluding n_ranges) + 8 (range) = 13.
        # The test payload has 6 config bytes; we need 5 config + 8 range = 13.
        # Corrected: 5 config bytes (divider, transport, slot, total_hi, total_lo) + 8 range = 13.
        # Config matches firmware out[5]..out[10]: n_ranges + divider + transport +
        # slot + total_bytes_hi + total_bytes_lo = 6 bytes.
        config_fixed = bytes([n_ranges, divider, transport, slot,
                             (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])"""

new_test = """        config_fixed = bytes([divider, transport, slot, n_ranges,
                             (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])"""
                             
content = content.replace(old_test, new_test)
Path(fpath).write_text(content, encoding="utf-8")
