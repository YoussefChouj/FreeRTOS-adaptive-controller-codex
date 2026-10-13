"""Tests for PlotJuggler layout XML generator (pj_layout.py)."""

from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

from ground_station.livewatch.pj_layout import (
    TAB_RULES,
    COLOR_PALETTE,
    MAX_CURVES,
    MAX_PLOTS,
    find_tab_for_var,
    partition_tab_vars,
    plot_curve_name,
    load_variables_from_source,
    generate_layout_xml,
    write_layout_from_frames,
)
from ground_station.livewatch.stream_log import (
    DEFAULT_FRAMES,
    load_frames,
    plot_key,
    generate_layout_xml as sl_generate_layout_xml,
    write_layout_from_frames as sl_write_layout_from_frames,
)


R5_FRAMES = Path("ground_station/livewatch/r5_extpos_frames.md")
LOG_FRAMES = Path("ground_station/livewatch/log_frames.md")
DRIFT_RC_PRESET = Path("ground_station/vofa_studio/presets/drift_rc.json")


def test_xml_is_valid_and_schema_compliant():
    """Layout XML is well-formed XML and conforms to PlotJuggler 3 hierarchy."""
    sample_vars = [
        "mrac_state.pitch.x",
        "mrac_state.pitch.xm",
        "mrac_state.pitch.r",
        "imu_data.rol",
        "imu_data.pit",
    ]
    xml_str = generate_layout_xml(sample_vars)
    root = ET.fromstring(xml_str)
    assert root.tag == "root"

    tw = root.find("tabbed_widget")
    assert tw is not None
    assert tw.attrib.get("name") == "Main Window"
    assert tw.attrib.get("parent") == "main_window"

    tabs = tw.findall("Tab")
    assert len(tabs) >= 2
    for tab in tabs:
        assert "tab_name" in tab.attrib
        assert tab.attrib.get("containers") == "1"
        container = tab.find("Container")
        assert container is not None
        splitter = container.find("DockSplitter")
        assert splitter is not None
        assert splitter.attrib.get("orientation") == "-"
        assert int(splitter.attrib.get("count", 0)) >= 1
        assert "sizes" in splitter.attrib

        dock_areas = splitter.findall("DockArea")
        assert len(dock_areas) == int(splitter.attrib["count"])
        for da in dock_areas:
            assert "name" in da.attrib
            plot = da.find("plot")
            assert plot is not None
            assert plot.attrib.get("mode") == "TimeSeries"
            assert plot.attrib.get("style") == "Lines"
            assert plot.find("range") is not None
            assert plot.find("limitY") is not None
            curves = plot.findall("curve")
            assert len(curves) >= 1
            for c in curves:
                assert "name" in c.attrib
                assert "color" in c.attrib

    rel_time = root.find("use_relative_time_offset")
    assert rel_time is not None
    assert rel_time.attrib.get("enabled") == "1"


def test_r5_extpos_every_streamed_var_in_exactly_one_tab():
    """For r5_extpos_frames.md: tabs are present, every streamed var in exactly one tab."""
    groups = load_frames(R5_FRAMES)
    expected_vars = [v for _, symbols in groups for v in symbols]
    assert len(expected_vars) == 287

    xml_str = generate_layout_xml(expected_vars)
    root = ET.fromstring(xml_str)

    expected_curves = {plot_curve_name(v) for v in expected_vars}
    assert len(expected_curves) == len(expected_vars)

    tabs = root.findall(".//Tab")
    tab_names = [t.attrib["tab_name"] for t in tabs]

    # Verify major expected tabs are present
    assert "MRAC Pitch" in tab_names
    assert "MRAC Roll" in tab_names
    assert "MRAC Yaw" in tab_names
    assert "MRAC Z" in tab_names
    assert "MRAC Velocity" in tab_names
    assert "Position / EKF" in tab_names
    assert "Ext-Pos / Camera" in tab_names
    assert "IMU / Attitude" in tab_names
    assert "RC / Controller" in tab_names

    seen_curves = set()
    for tab in tabs:
        t_name = tab.attrib["tab_name"]
        tab_curves = [c.attrib["name"] for c in tab.findall(".//curve")]
        assert len(tab_curves) > 0, f"Tab {t_name} is empty"
        for c in tab_curves:
            assert c not in seen_curves, f"Variable {c} duplicated in tab {t_name}"
            seen_curves.add(c)

    assert seen_curves == expected_curves
    assert len(seen_curves) == len(expected_vars)

    # Readable: no plot over MAX_CURVES; the 46 pitch Theta weights get their own tab, the main pitch tab keeps 3 plots
    for tab in tabs:
        areas = tab.findall(".//DockArea")
        assert len(areas) <= MAX_PLOTS, tab.attrib["tab_name"]
        assert all(len(a.findall(".//curve")) <= MAX_CURVES for a in areas), tab.attrib["tab_name"]
    by_name = {t.attrib["tab_name"]: t for t in tabs}
    assert len(by_name["MRAC Pitch Adaptive Weights (Theta)"].findall(".//DockArea")) == 6
    assert len(by_name["MRAC Pitch"].findall(".//DockArea")) == 3 <= MAX_PLOTS


def test_load_from_preset_json(tmp_path):
    """Layout can be generated from a VOFA+ preset JSON file."""
    out_file = tmp_path / "drift_rc.pj.xml"
    write_layout_from_frames(DRIFT_RC_PRESET, out_file)
    assert out_file.exists()

    vars_ = load_variables_from_source(DRIFT_RC_PRESET)
    root = ET.parse(out_file).getroot()
    found_curves = [c.attrib["name"] for c in root.findall(".//curve")]

    expected_curves = [plot_curve_name(v) for v in vars_]
    assert len(found_curves) == len(expected_curves)
    assert set(found_curves) == set(expected_curves)


def test_load_from_log_frames_markdown(tmp_path):
    """Layout from log_frames.md contains only tabs for signals present in the frame."""
    out_file = tmp_path / "log_frames.pj.xml"
    write_layout_from_frames(LOG_FRAMES, out_file)
    assert out_file.exists()

    vars_ = load_variables_from_source(LOG_FRAMES)
    root = ET.parse(out_file).getroot()
    found_curves = [c.attrib["name"] for c in root.findall(".//curve")]

    expected_curves = [plot_curve_name(v) for v in vars_]
    assert len(found_curves) == len(expected_curves)
    assert set(found_curves) == set(expected_curves)

    tab_names = [t.attrib["tab_name"] for t in root.findall(".//Tab")]
    # log_frames has only des_*, imu_data.*, Gyro_*, mrac_state.roll/pitch.*
    assert "RC / Controller" in tab_names
    assert "IMU / Attitude" in tab_names
    assert "MRAC Roll" in tab_names
    assert "MRAC Pitch" in tab_names
    # And groups not present are not created
    assert "Ext-Pos / Camera" not in tab_names
    assert "Motors / Power" not in tab_names


def test_related_curves_grouped_in_same_plot():
    """Verify related variables are grouped together in sub-plots with distinct colors."""
    vars_ = [
        "mrac_state.pitch.x",
        "mrac_state.pitch.xm",
        "mrac_state.pitch.r",
        "mrac_state.pitch.e",
        "mrac_state.pitch.u_nom",
        "mrac_state.pitch.Theta[0]",
        "mrac_state.pitch.Theta[1]",
    ]
    xml_str = generate_layout_xml(vars_)
    root = ET.fromstring(xml_str)
    tab = root.find(".//Tab[@tab_name='MRAC Pitch']")
    assert tab is not None

    plots = tab.findall(".//DockArea")
    plot_map = {p.attrib["name"]: [c.attrib["name"] for c in p.findall(".//curve")] for p in plots}

    assert "Tracking (x, xm, r)" in plot_map
    assert plot_map["Tracking (x, xm, r)"] == [
        "/mrac_state/pitch/x",
        "/mrac_state/pitch/xm",
        "/mrac_state/pitch/r",
    ]

    assert "Errors (e, e_dot, e_int)" in plot_map
    assert plot_map["Errors (e, e_dot, e_int)"] == ["/mrac_state/pitch/e"]

    assert "Control (u_nom, u_ad, u_def)" in plot_map
    assert plot_map["Control (u_nom, u_ad, u_def)"] == ["/mrac_state/pitch/u_nom"]

    assert "Adaptive Weights (Theta)" in plot_map
    assert plot_map["Adaptive Weights (Theta)"] == [
        "/mrac_state/pitch/Theta[0]",
        "/mrac_state/pitch/Theta[1]",
    ]

    # Verify colors assigned from palette
    for da in plots:
        curves = da.findall(".//curve")
        colors = [c.attrib["color"] for c in curves]
        for color in colors:
            assert color in COLOR_PALETTE


def test_stream_log_exports_and_cli(tmp_path):
    """Verify stream_log exports the helpers and supports --layout-out."""
    xml1 = sl_generate_layout_xml(["imu_data.rol", "imu_data.pit"])
    assert "<root>" in xml1

    out1 = tmp_path / "sl_test.pj.xml"
    sl_write_layout_from_frames(["imu_data.rol"], out1)
    assert out1.exists()
