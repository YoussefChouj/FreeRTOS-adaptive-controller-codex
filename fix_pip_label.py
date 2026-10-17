import os
with open("ground_station/analysis/live_tracker.py") as f:
    text = f.read()

pip_code2 = """
def pip_label(z_eff: float, z_m: float) -> str:
    \"\"\"Pure function formatting the landing zoom PiP text.\"\"\"
    return f"LAND ZOOM {z_eff:.1f}x z={z_m*100:.0f}cm"

class Preview:"""

if "def pip_label" not in text:
    text = text.replace("class Preview:", pip_code2)

with open("ground_station/analysis/live_tracker.py", "w") as f:
    f.write(text)

print("done")
