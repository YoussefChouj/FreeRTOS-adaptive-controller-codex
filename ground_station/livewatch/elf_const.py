"""Read initialised const data out of the firmware ELF, by DWARF name. No probe.

`symbols.SymbolResolver` turns a name into (address, type); the ELF's PT_LOAD
segments hold the bytes. Only objects that are resident in the flash image can be
read this way -- `const` tables and their string literals. A RAM variable has no
value until the target runs, and asking for one raises instead of guessing.

Used for firmware descriptors that the host must size itself from (the MRAC feature
count and descriptor table): the ELF is the contract, so nothing is hard-coded
host-side and the values cannot drift from the build that is being analysed.
"""
from __future__ import annotations

from pathlib import Path

from .symbols import SymbolResolver
from .verify import flash_segments

DEFAULT_ELF = Path(__file__).resolve().parents[2] / "OBJ" / "JX_FLY.axf"

# Longest C string read_cstring() accepts. Descriptor names are short identifiers;
# a missing NUL inside this window means the pointer is not a string.
CSTRING_LIMIT = 64


def read_flash(segments: list[tuple[int, bytes]], address: int, size: int) -> bytes:
    """`size` bytes at `address` from (paddr, contents) flash segments."""
    for base, data in segments:
        if base <= address and address + size <= base + len(data):
            return data[address - base: address - base + size]
    raise ValueError(f"0x{address:08X} +{size} B is not initialised flash data in the ELF "
                     f"(a RAM variable, or an address outside the image)")


def read_cstring(segments: list[tuple[int, bytes]], address: int,
                 limit: int = CSTRING_LIMIT) -> str:
    """NUL-terminated ASCII string at `address` from flash segments."""
    for base, data in segments:
        if base <= address < base + len(data):
            start = address - base
            window = data[start: start + limit]
            end = window.find(b"\x00")
            if end < 0:
                raise ValueError(f"no NUL within {limit} B of 0x{address:08X}; "
                                 f"not a C string")
            return window[:end].decode("ascii")
    raise ValueError(f"0x{address:08X} is not initialised flash data in the ELF")


class ElfConstReader:
    """Names -> const values read from one firmware ELF."""

    def __init__(self, elf_path: str | Path = DEFAULT_ELF):
        self.elf_path = Path(elf_path)
        self._segments = flash_segments(self.elf_path)
        self.resolver = SymbolResolver(self.elf_path)

    def close(self) -> None:
        self.resolver.close()

    def __enter__(self) -> "ElfConstReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def scalar(self, path: str) -> int | float | bool:
        """Value of a scalar (integer, float, enum or pointer) const at `path`."""
        sym = self.resolver.resolve(path)
        return sym.decode(read_flash(self._segments, sym.address, sym.size))

    def cstring(self, address: int) -> str:
        """String literal that a const pointer value (`scalar()` of a `char *`) points at."""
        return read_cstring(self._segments, address)

    def counted_array(self, array_path: str, count_symbol: str) -> list[str]:
        """`array_path[0] .. array_path[N-1]`, N read from the const `count_symbol`.

        The DWARF array length is the storage capacity; N is how many elements are
        live. Asking for more elements than the array holds is refused, because
        DWARF indexing is unchecked and would address the next struct member.
        """
        count = int(self.scalar(count_symbol))
        capacity = len(self.resolver.fields_of(array_path))
        if count > capacity:
            raise ValueError(f"{count_symbol} = {count} exceeds {array_path} "
                             f"capacity {capacity}")
        return [f"{array_path}[{i}]" for i in range(count)]
