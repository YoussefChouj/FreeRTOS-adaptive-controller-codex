"""tools/row_meta.py: the firmware tables pass; a value out of range or a missing legend line fails."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import row_meta  # noqa: E402

TABLE = """
/* legend
   @Kp   U/E  [0, 10]   gain
   @UMax U    [0, 1e3]  output limit */
#define FOO_ROW(Kp, UMax) \\
    { 0, Kp, UMax }
static const Foo foo[2] = {
    FOO_ROW(3.0f,  0.15f*600),  /* a */
    FOO_ROW(%s,  200)           /* b */
};
"""


def _check(tmp_path, text, mp):
    f = tmp_path / "t.c"
    f.write_text(text)
    mp.setattr(row_meta, "REPO", tmp_path)
    cells = []
    return row_meta.check_file(f, cells), cells


def test_firmware_tables_pass():
    assert row_meta.main([]) == 0


def test_in_range(tmp_path, monkeypatch):
    errors, cells = _check(tmp_path, TABLE % "9.5", monkeypatch)
    assert errors == []
    assert [c["value"] for c in cells] == [3.0, 90.0, 9.5, 200.0]
    assert cells[0]["unit"] == "U/E" and cells[1]["max"] == 1000.0


def test_out_of_range(tmp_path, monkeypatch):
    errors, _ = _check(tmp_path, TABLE % "12.0f", monkeypatch)
    assert len(errors) == 1 and "FOO_ROW.Kp = 12.0f outside [0, 10]" in errors[0]


def test_missing_legend_line(tmp_path, monkeypatch):
    errors, _ = _check(tmp_path, (TABLE % "1").replace("   @UMax U    [0, 1e3]  output limit */", "*/"), monkeypatch)
    assert len(errors) == 1 and "do not match" in errors[0]


# Assignment-form tables (ASSIGN): MRAC_SET has a legend line per row, MRAC_BASIS skips its 2 key arguments.
ASSIGN_TABLE = """
#define K_A 3.0f   /* a constant */
#if HEAVY
#define K_B 1.0f
#else
#define K_B %s
#endif
/* rows
   @alpha  1/s  [0, 5]  a
   @beta   -    [0, 2]  b */
#define MRAC_SET(f, p, r, y, z) \\
    a.f = (p); b.f = (r); c.f = (y); d.f = (z)
/* columns
   @g    1/s  [0, 10]  gain
   @low  -    [-1, 0]  lower bound */
#define MRAC_BASIS(ax, i, g, low) \\
    c_##ax.g[i] = (g); c_##ax.low[i] = (low)
void init(void)
{
    MRAC_SET(alpha, K_A,  1.0f, 0.0f, 5);
    MRAC_SET(beta,  K_B,  0.5f, 0.0f, 0.15f*6);
    for (i = 0; i < 4; i++) { MRAC_BASIS(yaw, i, 0.0f, -0.15f*0.6f); }
}
"""


def test_assignment_tables_in_range(tmp_path, monkeypatch):
    errors, cells = _check(tmp_path, ASSIGN_TABLE % "1.5f", monkeypatch)
    assert errors == []
    beta = [c["value"] for c in cells if c["param"] == "beta"]
    assert beta == pytest.approx([1.0, 1.5, 0.5, 0.0, 0.9])   # both #if definitions of K_B, then r y z
    basis = [(c["param"], c["row"], c["value"]) for c in cells if c["table"] == "MRAC_BASIS"]
    assert basis == [("g", "yaw,i", 0.0), ("low", "yaw,i", -0.15 * 0.6)]


def test_assignment_macro_out_of_range(tmp_path, monkeypatch):
    errors, _ = _check(tmp_path, ASSIGN_TABLE % "2.5f", monkeypatch)
    assert len(errors) == 1 and "MRAC_SET.beta = K_B (2.5) outside [0, 2]" in errors[0]


def test_assignment_missing_row_legend(tmp_path, monkeypatch):
    errors, _ = _check(tmp_path, (ASSIGN_TABLE % "1").replace("   @beta   -    [0, 2]  b */", "*/"), monkeypatch)
    assert len(errors) == 1 and "do not match the rows" in errors[0]
