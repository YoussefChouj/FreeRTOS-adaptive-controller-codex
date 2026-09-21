import sys
sys.path.insert(0, r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex")
from ground_station.livewatch.symbols import SymbolResolver
from ground_station.comm.boot_default_layout import DASHBOARD_FRAME_A_VARS

elf_path = r"C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\OBJ\JX_FLY.axf"
resolver = SymbolResolver(elf_path)

print("=== DWARF Resolution for DASHBOARD_FRAME_A_VARS ===")
print(f"Total vars: {len(DASHBOARD_FRAME_A_VARS)}")
resolved = 0
unresolved = []
for var in DASHBOARD_FRAME_A_VARS:
    try:
        sym = resolver.resolve(var)
        resolved += 1
        print(f"  [OK]   {var:<40} addr=0x{sym.address:08X} size={sym.size}")
    except Exception as e:
        unresolved.append(var)
        print(f"  [MISS] {var:<40} -> {e}")

print(f"\nResolved: {resolved}/{len(DASHBOARD_FRAME_A_VARS)}")
print(f"\nUnresolved ({len(unresolved)}):")
for u in unresolved:
    print(f"  {u}")
