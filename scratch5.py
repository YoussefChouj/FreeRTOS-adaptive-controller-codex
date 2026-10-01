import re

with open("ground_station/service/streams.py", "r") as f:
    content = f.read()

old_start = """    if route == "/api/streams/log/start":
        try:
            want = body.get("slots")
            specs = mgr.slot_specs()"""

new_start = """    if route == "/api/streams/log/start":
        try:
            if not body.get("skip_preflight", False):
                ok, reason = mgr.preflight_check()
                if not ok:
                    return 409, {"error": reason}
            want = body.get("slots")
            specs = mgr.slot_specs()"""

content = content.replace(old_start, new_start)

with open("ground_station/service/streams.py", "w") as f:
    f.write(content)
