"""Parse /state output."""
import sys, json

data = json.load(sys.stdin)
print("schema_id:", data.get("schema_id", "?"))
print("samples:", data.get("samples", "?"))
streams = data.get("streams", {})
print("streams:", list(streams.keys()))

if "0" in streams:
    s = streams["0"]
    vals = s.get("values", {})
    tag = s.get("tag", "?")
    received = s.get("received", "?")
    loss = s.get("loss_pct", "?")
    print(f"stream 0: tag={tag}, received={received}, loss={loss}")
    print(f"  values keys ({len(vals)}): {list(vals.keys())}")
    critical = ["status.roll_deg", "status.pitch_deg", "status.yaw_deg",
                "status.arm", "status.vbat", "mrac.pitch.e", "mrac.roll.e"]
    for k in critical:
        if k in vals:
            print(f"  OK   {k:25s} = {vals[k]}")
        else:
            print(f"  MISS {k}")

print()
print("JSON excerpt (first 3000 chars):")
print(json.dumps(vals, indent=2)[:3000])
