import re
from ground_station.research.sim.cascade_rank import cand_rows

def test_firmware_rows():
    expected = cand_rows("F1x+F3+F5w+F6a")
    
    with open("API/pid.c", "r") as f:
        src = f.read()
        
    rows = {}
    for match in re.finditer(r'PID_ROW\((.*?)\)\s*,\s*/\*\s*(.*?)\s+', src):
        vals_str, name = match.groups()
        vals = [float(x) for x in vals_str.replace(" ", "").split(",")]
        rows[name] = {
            "kp": vals[0], "ki": vals[1], "kd": vals[2],
            "umax": vals[3], "upmax": vals[4], "uimax": vals[5],
            "udmax": vals[6], "sumemax": vals[7], "emin": vals[8]
        }
    
    mapping = {
        "gyroxPID": "rate",
        "gyroyPID": "rate",
        "pitchPID": "ang",
        "rollPID": "ang",
        "locxsPID": "vel",
        "locysPID": "vel",
        "locxPID": "pos",
        "locyPID": "pos"
    }
    
    for fw_name, cand_name in mapping.items():
        assert fw_name in rows, f"{fw_name} missing from pid.c"
        c = expected[cand_name]
        r = rows[fw_name]
        
        assert abs(r["kp"] - c.Kp) < 1e-4, f"{fw_name} kp mismatch: {r['kp']} != {c.Kp}"
        assert abs(r["ki"] - c.Ki) < 1e-4, f"{fw_name} ki mismatch: {r['ki']} != {c.Ki}"
        assert abs(r["kd"] - c.Kd) < 1e-4, f"{fw_name} kd mismatch: {r['kd']} != {c.Kd}"
        assert abs(r["uimax"] - c.UiMax) < 1e-4, f"{fw_name} uimax mismatch: {r['uimax']} != {c.UiMax}"
        assert abs(r["sumemax"] - c.SumEMax) < 1e-4, f"{fw_name} sumemax mismatch: {r['sumemax']} != {c.SumEMax}"
        assert abs(r["emin"] - c.EMin) < 1e-4, f"{fw_name} emin mismatch: {r['emin']} != {c.EMin}"
        assert abs(r["umax"] - c.UMax) < 1e-4, f"{fw_name} umax mismatch: {r['umax']} != {c.UMax}"
        assert abs(r["upmax"] - c.UpMax) < 1e-4, f"{fw_name} upmax mismatch: {r['upmax']} != {c.UpMax}"
        assert abs(r["udmax"] - c.UdMax) < 1e-4, f"{fw_name} udmax mismatch: {r['udmax']} != {c.UdMax}"

