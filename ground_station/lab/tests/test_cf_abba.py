"""Offline ABBA preparation contract; no flight, service or gain writes."""
import json
from pathlib import Path

from ground_station.service.campaign_fly import _gain_meta, resolve_preset_gains

ROOT = Path(__file__).resolve().parents[3]


def test_abba_presets_share_all_measured_pid_gains_and_only_switch_mode():
    variants = []
    for mode, label in enumerate(("legacy", "rate-scaled")):
        path = ROOT / f"docs/lab/gains/2026-10-27-cf-{label}.json"
        preset = json.loads(path.read_text(encoding="utf-8"))
        gains, why, error = resolve_preset_gains(path)
        assert not error
        assert "PROPOSED" in why
        assert {i: gains[i] for i in range(18, 24)} == {
            18: 400, 19: 0.435, 20: 0, 21: 0.7, 22: 0.001, 23: 0.1,
        }
        assert gains[58] == 1  # CF velocity feedback in both variants
        assert gains[59] == mode
        assert _gain_meta(59)[0] == "g_alt_cf_rate_mode"
        assert _gain_meta(59)[2] == 0x2D
        assert len([i for i in gains if 9 <= i <= 17]) == 9
        variants.append(preset["values"])
    assert {key for key in variants[0] if variants[0][key] != variants[1][key]} == {"g_alt_cf_rate_mode"}


def test_abba_order_and_commands_are_saved_in_both_operator_documents():
    labels = ("cf-legacy-hover", "cf-rate-scaled-hover", "cf-rate-scaled-hover-repeat", "cf-legacy-hover-repeat")
    for document in ("docs/lab/COMMANDS.md", "docs/lab/days/2026-10-27.yaml"):
        text = (ROOT / document).read_text(encoding="utf-8")
        positions = [text.index(label) for label in labels]
        assert positions == sorted(positions)
        # Other baseline examples may reuse the preset; verify the four labelled flights.
        for position, mode in zip(positions, ("legacy", "rate-scaled", "rate-scaled", "legacy")):
            command = text[position:].splitlines()[1].lstrip("# ")
            assert command == (r".\fly lab_z_cf_hover --once --gains "
                               f"logs/lab/2026-10-27/2026-10-27-cf-{mode}.json")
