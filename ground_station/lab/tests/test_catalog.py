"""catalog.py: the lab catalog's file, its status ladder, the queue's gate and `lab catalog`."""
import pytest

from ground_station.lab import cards, catalog, runner
from ground_station.lab import __main__ as lab

HEAD = "# the catalog\n#   kept as it is\n"


def tmp_catalog(monkeypatch, tmp_path, body):
    p = tmp_path / "catalog.yaml"
    p.write_text(HEAD + body, encoding="utf-8")
    monkeypatch.setattr(catalog, "CATALOG", p)
    return p


def test_the_real_catalog_lists_the_days_cards():
    cat = catalog.load()
    for name in ("fusion-hover", "pad-landing", "xy-ctrl-screen", "xy-ctrl-confirm"):
        assert cat["cards"][name]["status"] in catalog.STATUSES


def test_save_keeps_the_header_and_an_unquoted_day_is_a_string(monkeypatch, tmp_path):
    p = tmp_catalog(monkeypatch, tmp_path, "modules:\n  mrac: {status: flown, note: shadow and active}\n"
                                           "cards:\n  pad-landing: {status: flown, flown: {2026-10-15: 2}}\n")
    cat = catalog.load()
    assert cat["cards"]["pad-landing"]["flown"] == {"2026-10-15": 2} and cat["images"] == {}
    cat["cards"]["pad-landing"]["status"] = "confirmed"
    catalog.save(cat)
    assert p.read_text(encoding="utf-8").startswith(HEAD + "modules:")
    again = catalog.load()
    assert again["cards"]["pad-landing"]["status"] == "confirmed" and again["modules"] == cat["modules"]


def test_every_problem_is_named(monkeypatch, tmp_path):
    tmp_catalog(monkeypatch, tmp_path, "rigs: {}\ncards:\n  a: {status: done}\n  b: {status: flown, flown: 3}\n")
    with pytest.raises(catalog.CatalogError) as e:
        catalog.load()
    msg = str(e.value)
    assert "unknown section 'rigs'" in msg and "cards.a: status" in msg and "cards.b: flown" in msg


def test_the_queue_flies_only_listed_cards(monkeypatch, tmp_path):
    tmp_catalog(monkeypatch, tmp_path, "cards:\n  pad-landing: {status: idea}\n")
    with pytest.raises(cards.CardError) as e:
        runner.parse_queue("pad-landing\nfusion-hover\n", "q.txt")
    msg = str(e.value)
    assert "q.txt:2" in msg and "'fusion-hover' is not in" in msg and "pad-landing" not in msg
    assert [c.name for c in runner.parse_queue("pad-landing\n", "q.txt").cards] == ["pad-landing"]


def test_a_broken_catalog_stops_the_queue(monkeypatch, tmp_path):
    tmp_catalog(monkeypatch, tmp_path, "cards: [pad-landing]\n")
    with pytest.raises(cards.CardError, match="cards: not a map"):
        runner.parse_queue("pad-landing\n", "q.txt")


def test_status_only_moves_forward():
    assert catalog.later("flown", "idea") == "flown" and catalog.later("sim-passed", "confirmed") == "confirmed"


def test_lab_catalog_prints_a_section(monkeypatch, tmp_path, capsys):
    body = ("modules:\n  ekf: {status: flown, what: position EKF}\n"
            "cards:\n  pad-landing: {status: flown, flown: {2026-10-14: 3, 2026-10-15: 2}}\n")
    tmp_catalog(monkeypatch, tmp_path, body)
    assert catalog.main([]) == 0
    out = capsys.readouterr().out
    assert "modules (1)" in out and "position EKF" in out and "images (0)" in out and "none yet" in out
    assert "5 lab flights, last 2026-10-15" in out and cards.load_card("pad-landing").hypothesis in out
    assert catalog.main(["cards"]) == 0 and "modules" not in capsys.readouterr().out


def test_lab_catalog_word(monkeypatch):
    seen = []
    monkeypatch.setattr(catalog, "main", lambda argv: seen.append(argv) or 0)
    assert lab.main(["catalog", "cards"]) == 0 and seen == [["cards"]]
