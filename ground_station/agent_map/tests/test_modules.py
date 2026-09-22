from ground_station.agent_map.modules import Modules


def test_symbol_override_beats_file_tier(tmp_path):
    y = tmp_path / "modules.yaml"
    y.write_text(
        "confirmed: true\n"
        "modules:\n"
        "  - {path: TASK/send_data.c, module: cmd, tier: 0}\n"
        "symbols:\n"
        "  - {name: s_ekf, tier: 1}\n", encoding="utf-8")
    m = Modules(y)
    file_tier = m.lookup("TASK/send_data.c")["tier"]
    assert file_tier == 0
    assert m.symbol_tier("s_ekf", file_tier) == 1
    assert m.symbol_tier("other", file_tier) == 0
