import csv
import json

from ground_station.lab import flight_index


def _flight(root, day, name, debrief, meta=None, card=None):
    d = root / day / name
    d.mkdir(parents=True)
    (d / "debrief.txt").write_text(debrief, encoding="utf-8")
    (d / f"{name[3:]}.fly").write_text("", encoding="utf-8")
    if meta is not None:
        (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if card is not None:
        (d / "scorecard.json").write_text(json.dumps(card), encoding="utf-8")
    return d


def test_index_reads_status_rms_metadata_and_verdict(tmp_path):
    _flight(tmp_path, "2026-10-28", "01_hover",
            "complete  air 54.5 s  1 flight(s)  359 live samples\n"
            "HOVER  x/y/z error cm  rms 3/2/2  max 12/8/10  (73 s)\nTRAJ   no samples\n",
            meta={"controller": "lqr", "preset": "lqr-plus-mrac", "git_commit": "4e3aa4c9deadbeef"},
            card={"verdict": "PASS"})
    r = flight_index.rows(tmp_path)[0]
    assert (r["day"], r["folder"], r["fly"], r["status"], r["air_s"]) == ("2026-10-28", "01_hover", "hover",
                                                                         "complete", "54.5")
    assert (r["hover_rms_cm"], r["traj_rms_cm"]) == ("3/2/2", "")
    assert (r["controller"], r["preset"], r["git"], r["verdict"]) == ("lqr", "lqr-plus-mrac", "4e3aa4c9", "PASS")


def test_an_aborted_flight_is_not_listed_as_complete(tmp_path):
    # 10-28: debrief line 1 says "complete" for 85 flights the safety monitor landed; line 2 says ABORTED
    _flight(tmp_path, "2026-10-28", "01_sysid",
            "complete  air 0.0 s  1 flight(s)  204 live samples\n"
            "ABORTED L1 f01_x: takeoff refused at set g_land_xy_gate_cm: no result in 3.0 s "
            "(the safety monitor landed it; the steps after that point never ran)\nHOVER  no samples\n")
    r = flight_index.rows(tmp_path)[0]
    assert r["status"] == "aborted L1"
    assert r["note"] == "takeoff refused at set g_land_xy_gate_cm: no result in 3.0 s"


def test_missing_files_leave_columns_empty_and_main_writes_both_files(tmp_path):
    (tmp_path / "2026-10-20" / "03_empty").mkdir(parents=True)
    _flight(tmp_path, "2026-10-21", "01_a", "error\n")
    assert flight_index.main(["--root", str(tmp_path)]) == 0
    with (tmp_path / "index.csv").open(encoding="utf-8") as fh:
        got = list(csv.DictReader(fh))
    assert [(g["day"], g["folder"], g["status"]) for g in got] == [("2026-10-20", "03_empty", ""),
                                                                  ("2026-10-21", "01_a", "error")]
    md = (tmp_path / "INDEX.md").read_text(encoding="utf-8")
    assert "## 2026-10-20 (1)" in md and "| 01_a | a | error |" in md
