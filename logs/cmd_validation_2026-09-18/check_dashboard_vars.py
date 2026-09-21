"""Verify ALL DASHBOARD_FRAME_A_VARS resolve against the live ELF.

This uses the canonical list from boot_default_layout.py — the same list
the wifi_bridge._request_slot0_schema() sends to the firmware.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Import the canonical list
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ground_station.comm.boot_default_layout import DASHBOARD_FRAME_A_VARS

from ground_station.livewatch.symbols import SymbolResolver


def main():
    resolver = SymbolResolver("OBJ/JX_FLY.axf")
    resolved = []
    failed = []
    for name in DASHBOARD_FRAME_A_VARS:
        try:
            sym = resolver.resolve(name)
            resolved.append((name, sym.address, sym.size))
        except Exception as e:
            failed.append((name, str(e)))

    print(f"\n{'='*60}")
    print(f"DASHBOARD FRAME A VARS - DWARF resolution test")
    print(f"{'='*60}")
    print(f"Total declared: {len(DASHBOARD_FRAME_A_VARS)}")
    print(f"Resolved:       {len(resolved)}")
    print(f"Failed:         {len(failed)}")
    print()

    if resolved:
        print(f"{'VAR':35s} {'ADDR':12s} {'SIZE':5s}")
        print(f"{'-'*35} {'-'*12} {'-'*5}")
        for name, addr, size in resolved:
            print(f"{name:35s} 0x{addr:08X}   {size}B")
    if failed:
        print(f"\nFAILED:")
        for name, err in failed:
            print(f"  {name}: {err}")

    # Save to file
    out_path = Path(__file__).parent / "dashboard_var_resolution.txt"
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(f"DASHBOARD FRAME A VARS - DWARF resolution test\n")
        fh.write(f"Total declared: {len(DASHBOARD_FRAME_A_VARS)}\n")
        fh.write(f"Resolved:       {len(resolved)}\n")
        fh.write(f"Failed:         {len(failed)}\n\n")
        for name, addr, size in resolved:
            fh.write(f"OK    {name:35s} 0x{addr:08X}   {size}B\n")
        for name, err in failed:
            fh.write(f"FAIL  {name:35s} {err}\n")
    print(f"\nResults saved to: {out_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())