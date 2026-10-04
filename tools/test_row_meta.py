"""tools/row_meta.py: the firmware tables pass; a value out of range or a missing legend line fails."""
import sys
from pathlib import Path

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
