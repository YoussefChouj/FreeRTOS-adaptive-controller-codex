"""Prove the ELF matches the firmware actually running on the target.

Every livewatch read resolves a NAME to an ADDRESS out of OBJ/JX_FLY.axf. If that
ELF is not the build that is flashed, the addresses are wrong and the tool returns
plausible-looking garbage rather than an error -- a float is a float whatever it
points at. That is the one way a strictly read-only tool can still mislead you, and
it is not hypothetical: the 2026-07-26 rebuild shifted .bss by 12 bytes and silently
invalidated two pinned test goldens.

So: compare bytes of the ELF's loadable flash segments against the same addresses
read back from the target. Any relink moves code, so a handful of sampled chunks is
enough to catch a stale ELF. This operation intentionally remains SWD-only because
the UART5 observation protocol cannot read internal flash. Reads flash only -- no
write, no halt, no reset.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from elftools.elf.elffile import ELFFile

import struct

# STM32F407 internal flash. Segments outside this (RAM-loaded .data copies, etc.)
# have no stable on-target image to compare against, so they are skipped.
FLASH_BASE = 0x08000000
FLASH_END = 0x08100000

FW_IDENTITY_ADDR = 0x20016BF8  # default known offset in SRAM
FW_IDENTITY_SIZE = 24          # magic + version + base + len + crc + status = 6 * 4
FW_IDENTITY_MAGIC = 0x44495746
FW_IDENTITY_STATUS_VALID = 1
FW_IDENTITY_IMAGE_BASE = 0x08000000


@dataclass(frozen=True)
class Sample:
    address: int
    expected: bytes


def flash_segments(elf_path: str | Path) -> list[tuple[int, bytes]]:
    """(paddr, contents) for each loadable segment resident in internal flash."""
    out: list[tuple[int, bytes]] = []
    with open(elf_path, "rb") as f:
        for seg in ELFFile(f).iter_segments():
            h = seg.header
            if h.p_type != "PT_LOAD" or h.p_filesz == 0:
                continue
            if not (FLASH_BASE <= h.p_paddr < FLASH_END):
                continue
            out.append((int(h.p_paddr), seg.data()[: h.p_filesz]))
    return out


def plan_samples(segments: list[tuple[int, bytes]], n: int = 5,
                 chunk: int = 64, full: bool = False) -> list[Sample]:
    """Pick sample chunks across the flash image.

    Default behaviour (`n=5`, even spread) samples a few evenly-spread chunks
    per flash segment, which is enough to catch a relink where bytes shift.
    On a ~750 kB image this is ~320 B total -- 0.04 % coverage. Fast, but
    misses a divergence that is localised to one function (the 2026-08-20
    `OBJ/JX_FLY.axf`-vs-flashed-image drift was a regional change that the
    5-chunk sampling never landed on).

    `n=20` (4x denser) is the new recommended floor for the standard
    `verify` run -- 1.3 kB sampled, still well under a second over wireless
    SWD, and enough density that a single displaced function moves the
    needle.

    `--full` (see `cmd_verify`) samples every chunk in every segment, which
    is the only way to guarantee detection of a localised drift. It costs
    O(image_bytes / chunk) reads and is impractical over the wireless
    debugger -- use it with a wired ST-Link only.

    The spread is deterministic so two runs compare the same bytes and a
    mismatch is reproducible. The first and last chunks are always sampled
    (where a relink shows up most reliably). Spread beats sampling one
    contiguous block -- a stale ELF can share a prefix with the flashed
    image and diverge only later.
    """
    samples: list[Sample] = []
    for base, data in segments:
        if not data:
            continue
        usable = max(len(data) - chunk, 0)
        if full:
            count = max(1, usable // chunk + 1)
        else:
            count = min(n, max(1, usable // chunk + 1))
        for i in range(count):
            off = 0 if count == 1 else (usable * i) // (count - 1)
            samples.append(Sample(base + off, bytes(data[off: off + chunk])))
    return samples


@dataclass
class VerifyResult:
    checked: int
    mismatched: int
    first_bad: int | None      # address of the first differing chunk
    bytes_compared: int

    @property
    def ok(self) -> bool:
        return self.checked > 0 and self.mismatched == 0

    def describe(self) -> str:
        if self.checked == 0:
            return "no loadable flash segments found in the ELF - cannot verify"
        if self.ok:
            return (f"ELF matches target: {self.checked} chunk(s), "
                    f"{self.bytes_compared} B compared, 0 mismatches")
        return (f"STALE ELF: {self.mismatched}/{self.checked} chunk(s) differ "
                f"(first at 0x{self.first_bad:08X}). Symbol addresses are NOT "
                f"trustworthy - rebuild or reflash before reading anything.")


def compare(samples: list[Sample], read_block) -> VerifyResult:
    """Compare planned samples against the target. `read_block(addr, size) -> bytes`."""
    bad = 0
    first_bad = None
    total = 0
    for s in samples:
        actual = bytes(read_block(s.address, len(s.expected)))
        total += len(s.expected)
        if actual != s.expected:
            bad += 1
            if first_bad is None:
                first_bad = s.address
    return VerifyResult(checked=len(samples), mismatched=bad,
                        first_bad=first_bad, bytes_compared=total)


@dataclass
class IdentityCheck:
    """Result of g_fw_identity read + CRC comparison."""
    ok: bool
    magic: int
    version: int
    image_base: int
    image_len: int
    firmware_crc: int
    host_crc: int
    status: int
    message: str


def compute_crc_from_hex(hex_path: str | Path) -> tuple[int, int, int]:
    """Read an Intel HEX file and compute the STM32 hardware CRC-32.

    Gaps between records are filled with 0xFF. The last byte is padded to a
    multiple of 4 with 0xFF so the word count is exact. Returns
    ``(computed_crc, image_len, image_base)``.
    """
    from ground_station.livewatch.transport import crc32_mpeg2_words

    hex_path = Path(hex_path)
    data: dict[int, int] = {}
    ext_addr = 0

    with open(hex_path, "r", encoding="ascii") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith(":"):
                continue
            rec = bytes.fromhex(line[1:])
            if len(rec) < 5:
                continue
            byte_count = rec[0]
            addr = (rec[1] << 8) | rec[2]
            rec_type = rec[3]
            rec_data = rec[4: 4 + byte_count]

            if rec_type == 0x04:       # Extended Linear Address
                ext_addr = (rec_data[0] << 8) | rec_data[1]
                continue
            if rec_type == 0x05:       # Extended Segment Address
                ext_addr = ((rec_data[0] << 24) | (rec_data[1] << 16) |
                            (rec_data[2] << 8) | rec_data[3])
                continue
            if rec_type != 0x00:       # Data record
                continue

            base = (ext_addr << 16) | addr
            for i, b in enumerate(rec_data):
                data[base + i] = b

    if not data:
        return 0xFFFFFFFF, 0, 0

    image_base = min(data.keys())
    max_addr = max(data.keys())
    raw_len = max_addr - image_base + 1
    image_len = (raw_len + 3) & ~3

    buf = bytearray(b"\xFF" * image_len)
    for addr, b in data.items():
        buf[addr - image_base] = b

    n_words = image_len // 4
    words = list(struct.unpack(f"<{n_words}I", buf))
    crc = crc32_mpeg2_words(words)

    return crc, image_len, image_base


def read_identity(target_read, expected_base=FW_IDENTITY_IMAGE_BASE,
                  identity_addr=None, elf_path=None, hex_path=None) -> IdentityCheck:
    """Read g_fw_identity from target and compare CRC against computed value.

    ``target_read(addr, size) -> bytes`` is a raw memory read via SWD.
    """
    from pathlib import Path

    if identity_addr is None:
        if elf_path is not None:
            try:
                from .symbols import SymbolResolver
                sym = SymbolResolver(elf_path).resolve("g_fw_identity")
                if sym:
                    identity_addr = sym.address
            except Exception:
                pass
        if identity_addr is None:
            default_elf = Path(__file__).resolve().parents[2] / "OBJ" / "JX_FLY.axf"
            if default_elf.exists():
                try:
                    from .symbols import SymbolResolver
                    sym = SymbolResolver(default_elf).resolve("g_fw_identity")
                    if sym:
                        identity_addr = sym.address
                except Exception:
                    pass
        if identity_addr is None:
            identity_addr = FW_IDENTITY_ADDR

    data = bytes(target_read(identity_addr, FW_IDENTITY_SIZE))
    if len(data) < FW_IDENTITY_SIZE:
        return IdentityCheck(False, 0, 0, 0, 0, 0, 0, 0,
                             f"g_fw_identity: could not read {FW_IDENTITY_SIZE} B "
                             f"at 0x{identity_addr:08X} (got {len(data)} B)")

    magic, version, image_base, image_len, firmware_crc, status = \
        struct.unpack("<IIIIIi", data)

    if hex_path is None:
        hex_path = Path(__file__).resolve().parents[2] / "OBJ" / "JX_FLY.hex"
    hex_path = Path(hex_path)
    if not hex_path.exists():
        return IdentityCheck(False, magic, version, image_base, image_len,
                              firmware_crc, 0, status,
                              f"hex file not found: {hex_path}")

    host_crc, host_len, host_base = compute_crc_from_hex(hex_path)

    ok = True
    issues: list[str] = []
    if magic != FW_IDENTITY_MAGIC:
        ok = False
        issues.append(f"magic mismatch: 0x{magic:08X} != 0x{FW_IDENTITY_MAGIC:08X}")
    if version != 1:
        ok = False
        issues.append(f"version mismatch: {version}")
    if status != FW_IDENTITY_STATUS_VALID:
        ok = False
        issues.append(f"status not valid: {status}")
    if image_base != expected_base:
        ok = False
        issues.append(f"image_base mismatch: 0x{image_base:08X} != 0x{expected_base:08X}")
    if host_len != image_len:
        ok = False
        issues.append(f"image_len mismatch: host={host_len} vs firmware={image_len}")
    if host_crc != firmware_crc:
        ok = False
        issues.append(
            f"CRC mismatch: host=0x{host_crc:08X} firmware=0x{firmware_crc:08X}")

    msg = "identity OK" if ok else "; ".join(issues)
    return IdentityCheck(ok, magic, version, image_base, image_len,
                         firmware_crc, host_crc, status, msg)

