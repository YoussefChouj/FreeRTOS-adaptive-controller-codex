"""Offline tests for the MRAC descriptor reader (`elf_const` + `mrac_features`).

No hardware. The flash-reading helpers are pure and run on synthetic segments. The
descriptor itself is read from the real firmware ELF and cross-checked against the C
source (`API/mrac.c`, `API/mrac_variant.h`), so a reader that mis-parses the table, or
an ELF that has gone stale against the source, fails here. The ELF-backed tests skip
(with the reason) when OBJ/JX_FLY.axf is absent or predates the descriptor.
"""
import re
from pathlib import Path

import pytest

from ground_station.livewatch import cli
from ground_station.livewatch.elf_const import (
    CSTRING_LIMIT, ElfConstReader, read_cstring, read_flash,
)
from ground_station.livewatch.mrac_features import (
    COUNT_SYMBOL, DESC_SYMBOL, MracFeature, features_from, read_mrac_features,
    read_mrac_n_features,
)

ROOT = Path(__file__).resolve().parents[3]
ELF = ROOT / "OBJ" / "JX_FLY.axf"
MRAC_C = ROOT / "API" / "mrac.c"
MRAC_VARIANT_H = ROOT / "API" / "mrac_variant.h"


# ---- pure: flash segment helpers ------------------------------------------

SEGMENTS = [(0x08000000, b"\x01\x02\x03\x04hello\x00tail"), (0x08010000, b"\xAA\xBB")]


def test_read_flash_returns_the_bytes_at_an_address():
    assert read_flash(SEGMENTS, 0x08000002, 2) == b"\x03\x04"
    assert read_flash(SEGMENTS, 0x08010000, 2) == b"\xAA\xBB"


def test_read_flash_refuses_ram_and_out_of_image_reads():
    with pytest.raises(ValueError, match="not initialised flash data"):
        read_flash(SEGMENTS, 0x20000000, 4)          # a RAM variable
    with pytest.raises(ValueError, match="not initialised flash data"):
        read_flash(SEGMENTS, 0x08010001, 4)          # runs past the segment end


def test_read_cstring_stops_at_the_nul():
    assert read_cstring(SEGMENTS, 0x08000004) == "hello"


def test_read_cstring_refuses_unterminated_and_unmapped_pointers():
    blob = [(0x08000000, b"A" * (CSTRING_LIMIT + 8))]
    with pytest.raises(ValueError, match="no NUL within"):
        read_cstring(blob, 0x08000000)
    with pytest.raises(ValueError, match="not initialised flash data"):
        read_cstring(SEGMENTS, 0x20000000)


# ---- ELF-backed --------------------------------------------------------------

@pytest.fixture(scope="module")
def features():
    """Descriptor rows from the real ELF, or a skip if the ELF cannot supply them."""
    if not ELF.exists():
        pytest.skip(f"{ELF} not present")
    try:
        return read_mrac_features(ELF)
    except (KeyError, ValueError, TypeError) as exc:
        pytest.skip(f"{ELF.name} has no MRAC descriptor (predates S2a?): {exc}")


@pytest.fixture(scope="module")
def reader(features):
    """One open ElfConstReader for the read-only tests (features guarantees the ELF is usable)."""
    with ElfConstReader(ELF) as r:
        yield r


def _source_rows():
    """`(index, name, block, group)` rows of `mrac_feature_desc[]` parsed from API/mrac.c."""
    text = MRAC_C.read_text(encoding="latin-1")
    body = re.search(r"mrac_feature_desc\[MRAC_N_FEATURES\]\s*=\s*\{(.*?)\};", text, re.S)
    assert body, "mrac_feature_desc initialiser not found in API/mrac.c"
    row = re.compile(r'\{\s*(\d+)\s*,\s*"(\w+)"\s*,\s*(MRAC_BLK_\w+)\s*,\s*(MRAC_GRP_\w+)\s*\}')
    return [(int(i), n, b, g) for i, n, b, g in row.findall(body.group(1))]


def test_descriptor_matches_the_c_source(features):
    """The ELF's table is the C source's table, row for row (names, blocks, groups)."""
    assert [(f.index, f.name, f.block, f.group) for f in features] == _source_rows()


def test_feature_count_is_mrac_n_features(features):
    define = re.search(r"#\s*define\s+MRAC_N_FEATURES\s+(\d+)",
                       MRAC_VARIANT_H.read_text(encoding="latin-1"))
    assert define, "MRAC_N_FEATURES not defined in API/mrac_variant.h"
    assert len(features) == int(define.group(1))
    assert read_mrac_n_features(ELF) == len(features)


def test_rows_are_dense_and_typed(features):
    assert [f.index for f in features] == list(range(len(features)))
    assert all(isinstance(f, MracFeature) and f.name.isidentifier() for f in features)
    assert len({f.name for f in features}) == len(features), "feature names must be unique"


def test_counted_array_expands_to_the_live_count_not_the_capacity(reader, features):
    paths = reader.counted_array("mrac_state.pitch.Theta", COUNT_SYMBOL)
    assert paths == [f"mrac_state.pitch.Theta[{i}]" for i in range(len(features))]
    # every expanded path is a real DWARF location inside the (possibly larger) array
    for p in paths:
        reader.resolver.resolve(p)


def test_counted_array_refuses_a_count_beyond_the_array(reader, monkeypatch):
    capacity = len(reader.resolver.fields_of("mrac_state.pitch.Theta"))
    monkeypatch.setattr(reader, "scalar", lambda path: capacity + 1)
    with pytest.raises(ValueError, match="exceeds"):
        reader.counted_array("mrac_state.pitch.Theta", COUNT_SYMBOL)


def test_scalar_refuses_a_ram_variable(reader):
    """A RAM variable has no value in the image; the reader must not invent one."""
    with pytest.raises(ValueError, match="not initialised flash data"):
        reader.scalar("mrac_state.pitch.Theta[0]")


def test_enumerators_decode_the_descriptor_enums(reader):
    blocks = reader.resolver.enumerators(f"{DESC_SYMBOL}[0].block")
    groups = reader.resolver.enumerators(f"{DESC_SYMBOL}[0].group")
    assert blocks[0] == "MRAC_BLK_STRUCT"
    assert "MRAC_GRP_BIAS" in groups.values()
    with pytest.raises(TypeError, match="not enum-typed"):
        reader.resolver.enumerators(f"{DESC_SYMBOL}[0].index")


def test_features_from_matches_read_mrac_features(reader, features):
    assert features_from(reader) == features


def test_missing_elf_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        read_mrac_features(tmp_path / "absent.axf")


# ---- CLI -----------------------------------------------------------------------

def test_cli_lists_the_descriptor(features, capsys):
    assert cli.main(["--elf", str(ELF), "mrac-features"]) == 0
    out = capsys.readouterr().out
    assert f"mrac_n_features = {len(features)}" in out
    for f in features:
        assert re.search(rf"^\s*{f.index}\s+{f.name}\s+{f.group}\s+{f.block}$", out, re.M)


@pytest.mark.parametrize("bad", ["absent", "junk"])
def test_cli_reports_an_unreadable_elf_without_a_traceback(bad, tmp_path, capsys):
    """A missing file or a non-ELF file: exit 1 and a message on stderr, not a stack trace."""
    path = tmp_path / f"{bad}.axf"
    if bad == "junk":
        path.write_bytes(b"not an elf")
    assert cli.main(["--elf", str(path), "mrac-features"]) == 1
    assert "ERROR" in capsys.readouterr().err
