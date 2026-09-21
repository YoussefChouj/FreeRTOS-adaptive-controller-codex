"""Compare code objects: newer 3.14 pyc (baseline) vs current source. 3.14 only."""
import marshal, types, sys

def load_pyc(p):
    f = open(p, "rb"); f.read(16)
    return marshal.load(f)

def methods(co):
    """class WifiBridge methods + module funcs: name -> code"""
    out = {}
    for c in co.co_consts:
        if isinstance(c, types.CodeType) and c.co_name == "WifiBridge":
            for m in c.co_consts:
                if isinstance(m, types.CodeType):
                    out["WifiBridge." + m.co_name] = m
        if isinstance(c, types.CodeType):
            out[c.co_name] = c
    return out

old = methods(load_pyc(r"ground_station/comm/__pycache__/wifi_bridge.cpython-314.pyc"))

src = open(r"ground_station/comm/wifi_bridge.py", encoding="utf-8").read()
curco = compile(src, "wifi_bridge.py", "exec")
new = methods(curco)

allk = sorted(set(old) | set(new))
for k in allk:
    a, b = old.get(k), new.get(k)
    if a is None:
        print("ONLY-IN-CURRENT", k)
    elif b is None:
        print("ONLY-IN-BASELINE", k)
    else:
        # compare bytecode + consts structurally
        if a.co_code != b.co_code or a.co_varnames != b.co_varnames or a.co_consts != b.co_consts:
            print("DIFF", k, a.co_firstlineno, "bsz", len(a.co_code), "vs", len(b.co_code))
