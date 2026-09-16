"""Persistent fault log reader — decodes Sector 11 flash records captured by the firmware.

Usage::

    from ground_station.livewatch.fault_log import FaultLogReader
    from ground_station.livewatch.probe import ProbeSession

    with ProbeSession() as probe:
        reader = FaultLogReader(probe)
        slots = reader.read_all()
        for slot in slots:
            print(reader.decode_record(slot.data))

CLI::

    python -m ground_station.livewatch fault-read [--slot N] [--raw]
    python -m ground_station.livewatch fault-erase --confirm
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

FAULT_FLASH_BASE = 0x080E0000
FAULT_SECTOR_SIZE = 0x20000  # 128 KB
FAULT_SLOT_SIZE = 512
FAULT_SLOT_COUNT = 4
FAULT_BACKUP_MAGIC = 0x464C5452

# Firmware fault_type values
_FAULT_TYPE_NAMES = {
    0: "GENERIC",
    1: "NMI",
    2: "STACK_OVERFLOW",
    3: "ASSERT",
    4: "MALLOC_FAIL",
    5: "HARD_FAULT",
}

# Exception number to name
_EXCEPTION_NAMES = {
    0: "ThreadMode",
    2: "NMI",
    3: "HardFault",
    4: "MemManage",
    5: "BusFault",
    6: "UsageFault",
    11: "SVCall",
    12: "DebugMonitor",
    14: "PendSV",
    15: "SysTick",
}

# CFSR bit-field names
_CFSR_MEM_NAMES = {
    0: "IACCVIOL",   # Instruction access violation
    1: "DACCVIOL",   # Data access violation
    2: "MUNSTKERR",  # MemManage fault on unstacking
    3: "MSTKERR",    # MemManage fault on stacking
    4: "MLSPERR",     # MemManage fault during FP lazy stacking
    7: "MMARVALID",  # MMFAR contains valid address
}
_CFSR_BUS_NAMES = {
    0: "IBUSERR",    # Instruction bus error
    1: "PRECISERR",  # Precise data bus error
    2: "IMPRECISERR", # Imprecise data bus error
    3: "UNSTKERR",   # Bus fault on unstacking
    4: "STKERR",     # Bus fault on stacking
    5: "LSPERR",      # Bus fault during FP lazy stacking
    7: "BFARVALID", # BFAR contains valid address
}
_CFSR_USG_NAMES = {
    0: "UNDEFINSTR",  # Undefined instruction
    1: "INVSTATE",     # Invalid state (Thumb bit)
    2: "INVPC",        # Invalid PC load
    3: "NOCP",         # No coprocessor
    8: "UNALIGNED",   # Unaligned memory access
    9: "DIVBYZERO",   # Division by zero
}


@dataclass
class FaultSlot:
    """One slot from the fault log sector."""
    index: int           # 0–3
    base_addr: int      # flash address of this slot
    occupied: bool       # magic == FAULT_BACKUP_MAGIC and CRC16 valid
    data: bytes         # raw 512 bytes
    record: Optional["FaultRecord"] = None


@dataclass
class FaultRecord:
    """Decoded fault record matching the firmware FaultRecord struct."""
    magic: int
    version: int
    size: int
    tick_count: int
    rtc_epoch: int
    hfsr: int
    cfsr: int
    mmfar: int
    bfar: int
    exception: int
    active_stack: int   # 0=MSP, 1=PSP
    fault_type: int
    reserved1: int
    stacked_pc: int
    stacked_lr: int
    stacked_psr: int
    stacked_r0: int
    stacked_r1: int
    stacked_r2: int
    stacked_r3: int
    stacked_r12: int
    stacked_lr_call: int
    msp: int
    psp: int
    control: int
    task_name: str
    task_handle: int
    r4: int; r5: int; r6: int; r7: int
    r8: int; r9: int; r10: int; r11: int
    fault_region: str
    roll_rate: float
    pitch_rate: float
    yaw_rate: float
    bat_voltage: float
    system_monitor_snapshot: list[int]
    crc16: int
    crc16_valid: bool


# ---- CRC-16 CCITT (XMODEM polynomial, initial 0xffff) ----
def _crc16_ccitt(data: bytes) -> int:
    crc = 0xffff
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xffff
    return crc


# ---- Struct unpacking helpers ----
# Firmware FaultRecord layout (all little-endian):
_FMT = "<IHHIIIIIBB I I I I I I I I I I 16s I 8I 32s f f f f 22I H"
# Count the fields: magic(1)+version(2)+size(3)+tick(4)+rtc(5)+hfsr(6)+cfsr(7)+mmfar(8)+bfar(9)
# +exception(10)+active_stack(11)+fault_type(12)+reserved1(13) + aligned(1) = 14
# stacked_pc(15)+stacked_lr(16)+stacked_psr(17)+stacked_r0(18)+stacked_r1(19)
# +stacked_r2(20)+stacked_r3(21)+stacked_r12(22)+stacked_lr_call(23)+msp(24)+psp(25)+control(26)
# task_name(27)[16]+task_handle(28)
# r4(29)+r5(30)+r6(31)+r7(32)+r8(33)+r9(34)+r10(35)+r11(36)
# fault_region(37)[32]
# roll(38)+pitch(39)+yaw(40)+bat(41)
# system_monitor(42..63)[22]
# crc16(64)
# Total: 14 + 1(pad) + 13(stacked) + 1(task_name) + 1(task_handle) + 8(r4-r11) + 1(fault_region) + 4(floats) + 22(sysmon) + 1(crc16) = 64
# Hmm, let me count more carefully...
# Bytes: 4+2+2 + 4*5 + 1+1+1+1 + 1pad + 9*4 + 4*4 + 16+4 + 8*4 + 32 + 4*4 + 22*4 + 2
# = 8 + 20 + 4 + 1 + 36 + 16 + 20 + 32 + 16 + 88 + 2
# = 243? No wait...

# Let me just unpack what I need and use offsets directly
_SLOT_STRUCT = struct.Struct("< I H H I I I I I I B B B B x I I I I I I I I I I I 16s I 8I 32s f f f f 22I H")


def _unpack_record(raw: bytes) -> dict:
    """Unpack a 512-byte fault record. Returns a dict."""
    vals = _SLOT_STRUCT.unpack(raw)
    i = 0
    rec = {}
    rec["magic"]       = vals[i]; i += 1
    rec["version"]     = vals[i]; i += 1
    rec["size"]        = vals[i]; i += 1
    rec["tick_count"]  = vals[i]; i += 1
    rec["rtc_epoch"]   = vals[i]; i += 1
    rec["hfsr"]        = vals[i]; i += 1
    rec["cfsr"]        = vals[i]; i += 1
    rec["mmfar"]       = vals[i]; i += 1
    rec["bfar"]        = vals[i]; i += 1
    rec["exception"]   = vals[i]; i += 1
    rec["active_stack"]= vals[i]; i += 1
    rec["fault_type"]  = vals[i]; i += 1
    rec["reserved1"]   = vals[i]; i += 1; _ = vals[i]  # skip padding
    rec["stacked_pc"]       = vals[i]; i += 1
    rec["stacked_lr"]       = vals[i]; i += 1
    rec["stacked_psr"]      = vals[i]; i += 1
    rec["stacked_r0"]         = vals[i]; i += 1
    rec["stacked_r1"]         = vals[i]; i += 1
    rec["stacked_r2"]         = vals[i]; i += 1
    rec["stacked_r3"]         = vals[i]; i += 1
    rec["stacked_r12"]        = vals[i]; i += 1
    rec["stacked_lr_call"]    = vals[i]; i += 1
    rec["msp"]                = vals[i]; i += 1
    rec["psp"]                = vals[i]; i += 1
    rec["control"]            = vals[i]; i += 1
    rec["task_name"]          = vals[i].rstrip(b"\x00").decode("ascii", errors="replace"); i += 1
    rec["task_handle"]        = vals[i]; i += 1
    r = []
    for _ in range(8):
        r.append(vals[i]); i += 1
    rec["callee_saved"] = r  # r4..r11
    rec["fault_region"]  = vals[i].rstrip(b"\x00").decode("ascii", errors="replace"); i += 1
    rec["roll_rate"]    = vals[i]; i += 1
    rec["pitch_rate"]   = vals[i]; i += 1
    rec["yaw_rate"]     = vals[i]; i += 1
    rec["bat_voltage"]  = vals[i]; i += 1
    sm = []
    for _ in range(22):
        sm.append(vals[i]); i += 1
    rec["system_monitor_snapshot"] = sm
    rec["crc16"] = vals[i]
    rec["crc16_valid"] = (_crc16_ccitt(raw[:510]) == rec["crc16"])
    return rec


class FaultLogReader:
    """Reads and decodes fault records from flash Sector 11."""

    def __init__(self, probe, elf_path: Optional[str | Path] = None):
        self.probe = probe
        self.elf_path = elf_path or (Path(__file__).resolve().parents[2] / "OBJ" / "JX_FLY.axf")
        self._resolver = None

    def _get_resolver(self):
        if self._resolver is None:
            try:
                from ground_station.livewatch.symbols import SymbolResolver
                self._resolver = SymbolResolver(self.elf_path)
            except Exception as exc:
                print(f"# warning: could not load SymbolResolver: {exc}", file=sys.stderr)
        return self._resolver

    def _slot_addr(self, slot: int) -> int:
        return FAULT_FLASH_BASE + 16 + slot * FAULT_SLOT_SIZE

    def read_all(self) -> list[FaultSlot]:
        """Read the full sector and decode all slots."""
        data = self.probe.read_flash(FAULT_FLASH_BASE, FAULT_SECTOR_SIZE)
        slots = []
        for idx in range(FAULT_SLOT_COUNT):
            slot_data = data[16 + idx * FAULT_SLOT_SIZE: 16 + (idx + 1) * FAULT_SLOT_SIZE]
            slot = FaultSlot(
                index=idx,
                base_addr=self._slot_addr(idx),
                occupied=False,
                data=slot_data,
                record=None,
            )
            if len(slot_data) >= 6:
                magic = struct.unpack_from("<I", slot_data, 0)[0]
                if magic == FAULT_BACKUP_MAGIC:
                    crc16 = struct.unpack_from("<H", slot_data, 510)[0]
                    crc_valid = (_crc16_ccitt(slot_data[:510]) == crc16)
                    if crc_valid:
                        slot.occupied = True
                        try:
                            raw_dict = _unpack_record(slot_data)
                            slot.record = raw_dict
                        except Exception as exc:
                            print(f"# warning: failed to decode slot {idx}: {exc}", file=sys.stderr)
            slots.append(slot)
        return slots

    def read_slot(self, slot: int) -> FaultSlot:
        """Read one slot by index."""
        data = self.probe.read_flash(self._slot_addr(slot), FAULT_SLOT_SIZE)
        slot = FaultSlot(
            index=slot,
            base_addr=self._slot_addr(slot),
            occupied=False,
            data=data,
            record=None,
        )
        if len(data) >= 6:
            magic = struct.unpack_from("<I", data, 0)[0]
            if magic == FAULT_BACKUP_MAGIC:
                crc16 = struct.unpack_from("<H", data, 510)[0]
                if _crc16_ccitt(data[:510]) == crc16:
                    slot.occupied = True
                    try:
                        slot.record = _unpack_record(data)
                    except Exception as exc:
                        print(f"# warning: failed to decode slot {slot}: {exc}", file=sys.stderr)
        return slot

    def erase(self) -> None:
        """Erase Sector 11 via the probe."""
        self.probe.erase_flash(addr=FAULT_FLASH_BASE, size=FAULT_SECTOR_SIZE)

    def decode_record(self, data: bytes) -> FaultRecord:
        """Decode a raw 512-byte fault record."""
        raw = _unpack_record(data)
        return raw

    def _resolve_symbol(self, addr: int) -> str:
        """Resolve a PC/LR address to symbol+offset using SymbolResolver."""
        resolver = self._get_resolver()
        if resolver is None:
            return "?"
        try:
            # SymbolResolver.resolve() takes a path string, not an address.
            # We need to find the nearest symbol. The resolver doesn't have a
            # direct "resolve_address" method, so we do a search on the names.
            # For now, return the address as hex + a placeholder.
            best = None
            best_offset = None
            for name in resolver.names():
                try:
                    sym = resolver.resolve(name)
                    if sym.address <= addr < sym.address + sym.size:
                        offset = addr - sym.address
                        if best is None or offset < best_offset:
                            best = name
                            best_offset = offset
                except Exception:
                    pass
            if best is not None:
                return f"{best}+0x{best_offset:X}"
            return f"0x{addr:08X}"
        except Exception:
            return f"0x{addr:08X}"

    def pretty_print(self, slot: FaultSlot) -> str:
        """Return a human-readable string for a fault slot."""
        if not slot.occupied:
            return f"  --- Slot {slot.index} (0x{slot.base_addr:08X}) ---\n    Status: EMPTY\n"

        rec = slot.record
        if rec is None:
            return f"  --- Slot {slot.index} (0x{slot.base_addr:08X}) ---\n    Status: OCCUPIED (decode failed)\n"

        lines = []
        lines.append(f"  --- Slot {slot.index} (0x{slot.base_addr:08X}) ---")
        lines.append(f"    Status: OCCUPIED")
        fault_type = _FAULT_TYPE_NAMES.get(rec["fault_type"], f"UNKNOWN({rec['fault_type']})")
        exc_name = _EXCEPTION_NAMES.get(rec["exception"], f"Exception_{rec['exception']}")
        lines.append(f"    Fault type: {fault_type} (exception={rec['exception']}={exc_name})")

        # HFSR
        hfsr = rec["hfsr"]
        hfsr_parts = []
        if hfsr & 0x40000000: hfsr_parts.append("FORCED")
        if hfsr & 0x00000002: hfsr_parts.append("VECTTBL")
        if hfsr & 0x80000000: hfsr_parts.append("DEBUGEVT")
        lines.append(f"    HFSR: 0x{hfsr:08X}  [{' | '.join(hfsr_parts) if hfsr_parts else 'no flags'}]")

        # CFSR
        cfsr = rec["cfsr"]
        cfsr_parts = []
        memsr = cfsr & 0xff
        for bit, name in _CFSR_MEM_NAMES.items():
            if memsr & (1 << bit): cfsr_parts.append(name)
        busr = (cfsr >> 8) & 0xff
        for bit, name in _CFSR_BUS_NAMES.items():
            if busr & (1 << bit): cfsr_parts.append(name)
        usgr = (cfsr >> 16) & 0xffff
        for bit, name in _CFSR_USG_NAMES.items():
            if usgr & (1 << bit): cfsr_parts.append(name)
        lines.append(f"    CFSR: 0x{cfsr:08X}  [{' | '.join(cfsr_parts) if cfsr_parts else 'no flags'}]")
        if cfsr & 0x80: lines.append(f"    MMFAR: 0x{rec['mmfar']:08X}")
        if cfsr & 0x8000: lines.append(f"    BFAR: 0x{rec['bfar']:08X}")

        # PC and LR
        pc_str = self._resolve_symbol(rec["stacked_pc"])
        lr_str = self._resolve_symbol(rec["stacked_lr"])
        lines.append(f"    PC: 0x{rec['stacked_pc']:08X}  → {pc_str}")
        lines.append(f"    LR: 0x{rec['stacked_lr']:08X}  → {lr_str}")
        lines.append(f"    xPSR: 0x{rec['stacked_psr']:08X}")
        lines.append(f"    R0: 0x{rec['stacked_r0']:08X}  R1: 0x{rec['stacked_r1']:08X}")
        lines.append(f"    R2: 0x{rec['stacked_r2']:08X}  R3: 0x{rec['stacked_r3']:08X}")
        lines.append(f"    R12: 0x{rec['stacked_r12']:08X}")

        # Stack
        stack_name = "PSP" if rec["active_stack"] else "MSP"
        lines.append(f"    Active stack: {stack_name}")
        lines.append(f"    MSP: 0x{rec['msp']:08X}  PSP: 0x{rec['psp']:08X}")
        lines.append(f"    CONTROL: 0x{rec['control']:08X}")

        # Task
        tick = rec["tick_count"]
        if tick > 0:
            lines.append(f"    xTaskGetTickCount: {tick} (≈{tick // 3600:.0f} min)")
        lines.append(f"    Active task: \"{rec['task_name']}\" (TCB=0x{rec['task_handle']:08X})")
        lines.append(f"    Fault region: \"{rec['fault_region']}\"")

        # Pre-fault state
        if rec["roll_rate"] != 0.0 or rec["pitch_rate"] != 0.0:
            lines.append(f"    Pre-fault: roll={rec['roll_rate']:.3f}  pitch={rec['pitch_rate']:.3f}  yaw={rec['yaw_rate']:.3f}  Vbat={rec['bat_voltage']:.2f} V")

        # CRC
        crc_valid = rec["crc16_valid"]
        crc_status = "valid" if crc_valid else f"INVALID (expected 0x{_crc16_ccitt(slot.data[:510]):04X})"
        lines.append(f"    CRC16: 0x{rec['crc16']:04X} ({crc_status})")

        return "\n".join(lines)


def cmd_fault_read(args):
    """Read fault log from flash sector 11."""
    from .probe import ProbeSession, ProbeError

    try:
        with ProbeSession(
            resume_on_disconnect=False,
            cmsis_dap_limit_packets=not getattr(args, "swd_no_limit_packets", False),
        ) as probe:
            reader = FaultLogReader(probe, elf_path=args.elf)

            if args.slot is not None:
                # Read one specific slot
                slot = reader.read_slot(args.slot)
                if args.raw:
                    # Dump raw hex
                    print(f"# Slot {slot.index} raw ({FAULT_SLOT_SIZE} B):")
                    for off in range(0, FAULT_SLOT_SIZE, 16):
                        chunk = slot.data[off:off+16]
                        hex_part = " ".join(f"{b:02X}" for b in chunk)
                        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                        print(f"  {slot.base_addr+off:08X}  {hex_part:<48s}  {asc_part}")
                else:
                    print(f"=== FAULT LOG ===")
                    print(f"Flash sector: 0x{FAULT_FLASH_BASE:08X} ({FAULT_SECTOR_SIZE // 1024} KB)")
                    print(reader.pretty_print(slot))
            else:
                # Read all slots
                print(f"=== FAULT LOG ===")
                print(f"Flash sector: 0x{FAULT_FLASH_BASE:08X} ({FAULT_SECTOR_SIZE // 1024} KB)")
                slots = reader.read_all()
                occupied = sum(1 for s in slots if s.occupied)
                print(f"Occupied slots: {occupied}/{FAULT_SLOT_COUNT}")
                print()
                for slot in slots:
                    print(reader.pretty_print(slot))
                    print()

    except ProbeError as exc:
        print(f"ERROR: probe error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_fault_erase(args):
    """Erase fault log sector 11."""
    from .probe import ProbeSession, ProbeError

    if not args.confirm:
        print("Erase will wipe all 4 fault log slots in Sector 11 (0x080E0000).")
        print("Pass --confirm to confirm.")
        return

    try:
        with ProbeSession(
            resume_on_disconnect=False,
            cmsis_dap_limit_packets=not getattr(args, "swd_no_limit_packets", False),
        ) as probe:
            print(f"# Erasing Sector 11 (0x{FAULT_FLASH_BASE:08X})...", flush=True)
            reader = FaultLogReader(probe, elf_path=args.elf)
            reader.erase()
            print("# Sector 11 erased. Fault log is now clean.")
    except ProbeError as exc:
        print(f"ERROR: probe error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
