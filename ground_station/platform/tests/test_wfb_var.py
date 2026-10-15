"""CMD 0x2D VAR_WRITE: the allowlist parsed from TASK/AutoflyTask.c, local checks, and the frame bytes."""

from __future__ import annotations

import re

import pytest

from ground_station.platform import wfb_var
from ground_station.platform.transactions import Outcome
from ground_station.platform.wfb_commands import WfbClient

C = wfb_var.SOURCE.read_text(encoding="utf-8")
STAB = (wfb_var.SOURCE.parent / "StabilizerTask.c").read_text(encoding="utf-8")


def _client(frames: list[bytes]) -> WfbClient:
    return WfbClient(lambda f: frames.append(f) or int(Outcome.APPLIED))


def test_frame_bytes_float_row() -> None:
    """traj_p.move_s is row 14: frame = cmd 0x2D, index 14, float32 12.0 (00 00 40 41), txid 1."""
    frames: list[bytes] = []
    assert _client(frames).var_write("traj_p.move_s", 12) is True
    assert frames == [bytes.fromhex("ccdf010001002d0e04000000404126")]


def test_frame_bytes_u8_row() -> None:
    """traj_go is row 2, a u8: the value still goes as float32 (1.0 = 00 00 80 3f)."""
    frames: list[bytes] = []
    client = _client(frames)
    client._next_txid = 2
    assert client.var_write("traj_go", 1) is True
    assert frames == [bytes.fromhex("ccdf010002002d0204000000803f97")]


@pytest.mark.parametrize("name,value", [
    ("traj_p.nope", 1.0),          # unknown name
    ("kp_active", 1.0),            # a real variable that is not in the allowlist
    ("traj_p.move_s", 25.5),       # above max (TRAJ_TOTAL_MAX_S 25)
    ("traj_p.start_z_m", 0.4),     # below min 0.5
    ("traj_id", 2.5),              # u8 row, not integral
    ("traj_p.zz_hz", float("nan")),
])
def test_refused_locally_nothing_sent(name: str, value: float) -> None:
    frames: list[bytes] = []
    with pytest.raises(wfb_var.VarWriteError):
        _client(frames).var_write(name, value)
    assert frames == []


def test_every_c_row_parses() -> None:
    """Every VAR_WRITE_ROW line of the table parses (load_rows refuses repeated names and ids)."""
    lines = re.findall(r"^[ \t]*VAR_WRITE_ROW\(", C, re.M)
    rows = wfb_var.load_rows()
    assert len(rows) == len(lines) == 39
    assert all(1 <= r.id <= 254 and r.min <= r.max for r in rows.values())


def test_row_types_match_c_declarations() -> None:
    """VW_U8 rows are uint8_t variables and VW_F32 rows float ones: a wrong type writes the wrong bytes."""
    body = re.search(r"typedef struct \{(.*?)\} TrajParams_t;", C, re.S).group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    fields = {n: t for t, n in re.findall(r"\b(uint8_t|float)\s+(\w+)\s*;", body)}
    rows = wfb_var.load_rows()
    traj = {n.split(".", 1)[1] for n in rows if n.startswith("traj_p.")}
    assert traj == set(fields)                          # every traj_p field has a row, and only those
    for row in rows.values():
        if row.name.startswith("traj_p."):
            ctype = fields[row.name.split(".", 1)[1]]
        else:
            decl = re.search(r"^volatile (uint8_t|float) [^;]*\b%s\s*=" % row.name, C + STAB, re.M)
            assert decl, row.name
            ctype = decl.group(1)
        assert (ctype == "uint8_t") == row.integer, row.name


def test_traj_defaults_cover_every_traj_p_row() -> None:
    """The boot values parsed from the traj_p initializer: one per row, each one writable to its own row."""
    d = wfb_var.traj_defaults()
    assert set(d) == {n for n in wfb_var.rows() if n.startswith("traj_p.")}
    for name, value in d.items():
        wfb_var.check(name, value)


def test_preset_duration_counts_goto_only_with_a_fixed_start() -> None:
    d = dict(wfb_var.traj_defaults(), **{"traj_p.goto_s": 4.0, "traj_p.settle_s": 2.0, "traj_p.move_s": 10.0,
                                         "traj_p.ret_s": 3.0, "traj_p.hold_s": 1.0})
    assert wfb_var.preset_duration_s(dict(d, **{"traj_p.start_fixed": 1.0})) == 20.0
    assert wfb_var.preset_duration_s(dict(d, **{"traj_p.start_fixed": 0.0})) == 14.0
    assert wfb_var.traj_total_max_s() == max(r.max for n, r in wfb_var.rows().items() if n == "traj_p.move_s")
