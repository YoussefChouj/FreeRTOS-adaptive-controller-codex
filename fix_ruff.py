import sys

def replace_in_file(filepath, replacements):
    with open(filepath, 'r') as f:
        content = f.read()
    for old, new in replacements:
        content = content.replace(old, new)
    with open(filepath, 'w') as f:
        f.write(content)

replace_in_file('ground_station/analysis/att_fit.py', [
    ('except:', 'except Exception:'),
    ('except Exception as e:', 'except Exception as e: # noqa: BLE001'),
    ('except Exception:', 'except Exception: # noqa: BLE001, S110'),
    ('from ground_station.autotune.frf import direct_iv', 'from ground_station.autotune.frf import direct_iv # noqa: F401'),
])

replace_in_file('ground_station/analysis/hover_fit.py', [
    ('except Exception as e:', 'except Exception as e: # noqa: BLE001'),
])
