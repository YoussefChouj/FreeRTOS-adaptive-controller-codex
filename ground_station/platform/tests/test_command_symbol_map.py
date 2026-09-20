"""Guard the D2 command-parameter -> firmware-symbol readback mapping.

A wrong readback is worse than no readback: it tells the operator a gain is
0.8 when it is 1.4. The mapping is therefore allowed to be small, but every
row in it must name a symbol that genuinely exists in the firmware and is
readable over the probe path. These tests make a guessed row a test failure
rather than something a reviewer has to catch by eye.
"""

from pathlib import Path

import pytest

from ground_station.platform.firmware_contract import COMMAND_TABLE

ELF = Path(__file__).resolve().parents[3] / "OBJ" / "JX_FLY.axf"

# Reads on the probe path are restricted to these widths.
ALLOWED_WIDTHS = {1, 2, 4}


def _mapped_params():
    """Every (command, param) pair that claims a firmware symbol."""
    for spec in COMMAND_TABLE.values():
        for param in spec.params:
            if param.symbol:
                yield spec, param


def test_known_rows_are_present():
    """The 0x1E rows are the mapping's first consumers (E2)."""
    spec = COMMAND_TABLE[0x1E]
    by_index = {p.index: p.symbol for p in spec.params}
    assert by_index[0] == "g_of_bias_mode"
    assert by_index[1] == "g_of_bias_ema_freeze"


def test_mapping_is_not_silently_empty():
    """Catches the mapping being dropped wholesale by a refactor."""
    assert list(_mapped_params()), "no command parameter carries a symbol"


@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_every_mapped_symbol_resolves_in_the_elf():
    """No row may name a symbol the firmware does not export.

    This is the anti-guessing guard. Adding a plausible-looking symbol name
    to the table without checking it against the build fails here.
    """
    from ground_station.livewatch.symbols import SymbolResolver

    resolver = SymbolResolver(ELF)
    try:
        unresolved = []
        for spec, param in _mapped_params():
            try:
                resolver.resolve(param.symbol)
            except Exception as exc:  # resolver raises its own error types
                unresolved.append(
                    f"0x{spec.id:02X}/{param.name} -> {param.symbol}: {exc}"
                )
        assert not unresolved, "unresolvable readback symbols:\n" + "\n".join(unresolved)
    finally:
        resolver.close()


@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_every_mapped_symbol_is_a_probe_readable_scalar():
    """Structs and aggregates cannot be read; only {1,2,4}-byte scalars can."""
    from ground_station.livewatch.symbols import SymbolResolver

    resolver = SymbolResolver(ELF)
    try:
        bad = []
        for spec, param in _mapped_params():
            sym = resolver.resolve(param.symbol)
            if sym.size not in ALLOWED_WIDTHS:
                bad.append(
                    f"0x{spec.id:02X}/{param.name} -> {param.symbol}: "
                    f"{sym.size} bytes, not in {sorted(ALLOWED_WIDTHS)}"
                )
        assert not bad, "readback symbols that the probe cannot read:\n" + "\n".join(bad)
    finally:
        resolver.close()


@pytest.mark.skipif(not ELF.exists(), reason="firmware ELF not built")
def test_u8_symbol_carries_an_integer_format_not_float():
    """The F1 trap: a u8 must not decode through the float32 fallback.

    Bug F1 was a StreamRange built without ``fmt``, so a u32 tick count fell
    through to float32 and decoded as a denormal 0.0. The same fallback would
    turn bias mode 2 into a meaningless float, so prove the resolver hands us
    an integer format for the u8 rows.
    """
    from ground_station.livewatch.symbols import SymbolResolver

    resolver = SymbolResolver(ELF)
    try:
        sym = resolver.resolve("g_of_bias_mode")
        assert sym.size == 1
        assert sym.fmt in ("B", "b"), f"expected u8 integer format, got {sym.fmt!r}"
    finally:
        resolver.close()
