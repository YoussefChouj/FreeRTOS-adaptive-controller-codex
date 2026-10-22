"""ground_station/analysis/mrac_xt.py: layout parity with API/mrac.h MRAC_Xt_t and the decode of each field."""
import re
from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis import mrac_xt as xt

HDR = (Path(__file__).resolve().parents[3] / "API" / "mrac.h").read_text()


def _struct_fields():
    body = dict((n, b) for b, n in re.findall(r"typedef struct\s*\{([^{}]*)\}\s*(\w+);", HDR))["MRAC_Xt_t"]
    return re.findall(r"int16_t\s+(\w+)", re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S))


def test_layout_matches_header():
    assert _struct_fields() == ["pat", "phase", "period", "e", "u_ad", "trap", "grp"]
    for name, val in (("MRAC_XT_E_SCALE", xt.E_SCALE), ("MRAC_XT_U_SCALE", xt.U_SCALE)):
        assert float(re.search(r"#define\s+%s\s+([0-9.]+)" % name, HDR).group(1)) == val
    assert xt.WORDS == 81 and xt.TRAP_WORDS == 3          # the C host test asserts MRAC_XT_WORDS == 81 (MULTI)
    assert len(xt.COLUMNS) == 81 and xt.SUBSCRIBE == "mrac_xt.pat:81"


def _sample():
    w = np.zeros(xt.WORDS, dtype=np.int64)
    w[xt.OFF_PAT] = 4 | 1 | (1 << (3 + 4))                 # inner, slow, running, pitch excited
    w[xt.OFF_PHASE], w[xt.OFF_PERIOD] = 12000, 9
    w[xt.OFF_E + 0] = -349 & 0xFFFF                         # unsigned on the wire
    w[xt.OFF_U + 0] = 1658
    w[xt.OFF_U + 1] = -32767 & 0xFFFF
    w[xt.OFF_TRAP + 0 * xt.TRAP_WORDS + 0] = 1 << 6         # pitch feature 6 (ext 0)
    w[xt.OFF_TRAP + 5 * xt.TRAP_WORDS + 2] = 1 << (45 - 32)  # y feature 45
    w[xt.OFF_GRP + 0 * 8 + 1] = -200 & 0xFFFF               # pitch rate group
    return w


def test_decode_fields():
    d = xt.decode(_sample())
    assert d["phase"][0] == 12000 and d["period"][0] == 9 and d["running"][0]
    assert d["e"][0, 0] == pytest.approx(-0.349) and d["u_ad"][0, 0] == pytest.approx(1.658)
    assert d["u_ad"][0, 1] == pytest.approx(-32.767) and d["railed"][0].sum() == 1
    assert np.flatnonzero(d["trapped"][0, 0]).tolist() == [6]
    assert np.flatnonzero(d["trapped"][0, 5]).tolist() == [45]
    assert d["trapped"][0].sum() == 2
    assert d["grp"][0, 0, 1] == pytest.approx(-0.2) and d["grp"][0].sum() == pytest.approx(-0.2)


def test_pattern_bits_follow_the_stabilizer_packing():
    # StabilizerTask.c: (layer&1) | (speed&1)<<1 | running<<2 | (mask&0x3F)<<3, mask bit = Excite_Axis_e index
    pat = 0 | (1 << 1) | (0 << 2) | (((1 << 0) | (1 << 5)) << 3)
    assert xt.pattern_bits(pat) == {"layer": "outer", "speed": "fast", "running": False, "axes": ["x", "roll"]}


def test_from_columns_and_csv(tmp_path):
    rows = np.vstack([_sample(), np.zeros(xt.WORDS, dtype=np.int64)])
    p = tmp_path / "run.slot0.csv"
    p.write_text("t_s," + ",".join(xt.COLUMNS) + "\n" + "\n".join(
        "%d," % i + ",".join(str(int(v)) for v in r) for i, r in enumerate(rows)) + "\n")
    d = xt.read_csv(str(p))
    assert d["e"].shape == (2, 6) and d["e"][0, 0] == pytest.approx(-0.349) and d["e"][1, 0] == 0
    assert "pitch" in xt.summary(d)
    with pytest.raises(KeyError):
        xt.from_columns({"mrac_xt.pat[0]": [0]})
    with pytest.raises(ValueError):
        xt.decode(np.zeros(80))
