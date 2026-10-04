"""telemetry_groups.py vs API/flight_telemetry.h and its writer Tlm_Snapshot (WP-37)."""
from __future__ import annotations

import re
from pathlib import Path

from ground_station.platform.firmware_contract import SUBSCRIBE_MAX_RANGES, SUBSCRIBE_STREAM_MAX_BYTES
from ground_station.platform.telemetry_groups import GROUPS, SYMBOL, TOTAL_FLOATS, VALUE_SIZE, all_symbols, \
    group, offset_floats

REPO = Path(__file__).resolve().parents[3]
HEADER = REPO / "API" / "flight_telemetry.h"
WRITER = REPO / "TASK" / "StabilizerTask.c"


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.S)


def _structs(code: str) -> dict[str, list[tuple[str, str, int]]]:
    """typedef struct { <type> <name>[N]; ... } NAME;  ->  NAME: [(type, name, N), ...]"""
    out = {}
    for body, name in re.findall(r"typedef struct\s*\{(.*?)\}\s*(\w+)\s*;", code, flags=re.S):
        fields = []
        for ctype, fname, n in re.findall(r"(\w+)\s+(\w+)\s*(?:\[(\d+)\])?\s*;", body):
            fields.append((ctype, fname, int(n) if n else 1))
        out[name] = fields
    return out


def test_groups_match_the_header():
    structs = _structs(_code(HEADER))
    top = structs["FlightTelemetry_t"]
    assert [(t, n) for t, n, _ in top] == [(g.ctype, g.name) for g in GROUPS]
    for g in GROUPS:
        fields = structs[g.ctype]
        assert all(t == "float" for t, _, _ in fields), f"{g.ctype}: every field must be float"
        assert [(n, c) for _, n, c in fields] == list(g.fields), g.name


def test_block_fits_one_range():
    assert TOTAL_FLOATS == 60
    assert TOTAL_FLOATS * VALUE_SIZE <= SUBSCRIBE_STREAM_MAX_BYTES
    assert len(GROUPS) <= SUBSCRIBE_MAX_RANGES
    assert offset_floats("pwr") + group("pwr").float_count == TOTAL_FLOATS


def test_symbols_are_unique_paths_into_g_tlm():
    syms = all_symbols()
    assert len(syms) == TOTAL_FLOATS == len(set(syms))
    assert all(s.startswith(SYMBOL + ".") for s in syms)
    assert group("mrac").symbols()[:2] == ["g_tlm.mrac.e[0]", "g_tlm.mrac.e[1]"]


def test_the_writer_fills_every_field_once():
    code = _code(WRITER)
    body = code[code.index("static void Tlm_Snapshot(void)"):]
    body = body[:body.index("\n}")]
    written = re.findall(r"\bg_tlm\.(\w+)\.(\w+)(?:\[(\d+)\])?\s*=", body)
    paths = [f"{SYMBOL}.{g}.{f}" + (f"[{i}]" if i else "") for g, f, i in written]
    assert sorted(paths) == sorted(all_symbols())
