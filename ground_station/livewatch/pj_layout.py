"""PlotJuggler layout XML generator.

Generates a multi-tab PlotJuggler layout XML from a stream_log frames file
(Markdown table or VOFA+ preset JSON). Every streamed variable is placed into
exactly one tab according to variable prefix rules, with related curves grouped
into cohesive sub-plots.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, Sequence
import xml.etree.ElementTree as ET

from ground_station.livewatch.stream_log import load_frames, parse_frames_markdown, plot_key


# Standard distinct color palette for curves within each plot
MAX_CURVES = 8   # curves per plot before it is split
MAX_PLOTS = 6    # plots stacked in one tab before the rest spill to "<tab> (2)"

COLOR_PALETTE = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#7f7f7f",  # gray
    "#bcbd22",  # olive
    "#17becf",  # cyan
]

# Rule table: (Tab Name, tuple of variable name prefixes)
# Evaluated in order; first matching rule claims the variable.
TAB_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("MRAC Pitch", ("mrac_state.pitch.",)),
    ("MRAC Roll", ("mrac_state.roll.",)),
    ("MRAC Yaw", ("mrac_state.yaw.",)),
    ("MRAC Z", ("mrac_state.z_rate.",)),
    ("MRAC Velocity", ("mrac_state.x_vel.", "mrac_state.y_vel.")),
    ("MRAC Adaptive / VP", (
        "mrac_vp12", "mrac_bus", "mrac_flags", "mrac_var_id",
        "mrac_ref_", "mrac_u_ff", "vp_", "kp_", "lab_stage_active"
    )),
    ("Position / EKF", (
        "s_ekf_of", "s_ekf.", "g_ekf_gate.", "ano_of",
        "s_of_bias_", "g_of_", "g_ekf_of", "Lin_Acc_"
    )),
    ("Ext-Pos / Camera", ("g_ext_pos", "traj_")),
    ("Motors / Power", (
        "mymotor", "rpm_dbg", "Throttle_", "real_voltage",
        "g_thrust_est", "dbg_motor_"
    )),
    ("IMU / Attitude", ("Acc_", "Gyro_", "imu_data.", "Gravity_Body_")),
    ("RC / Controller", (
        "Remoter.", "Ctrler.", "DroneStatus.", "flight_phase",
        "g_ctrl_", "u_gyro", "des_", "gs_max_", "gs_throttle_",
        "g_land_gate_", "lim_active", "s_state", "g_estimator_ready",
        "g_yaw_mix_dir", "TWC."
    )),
    ("System / Health", (
        "g_wfb_status", "g_rtos_budget", "xTickCount",
        "system_monitor.", "g_loop_", "g_heap_", "g_task_",
        "UA3", "g_reset_csr", "s_cal_", "g_cal_health", "g_gyro_z_bias_"
    )),
]


def find_tab_for_var(var: str) -> str:
    """Return the tab name for a variable based on TAB_RULES prefix matching."""
    for tab_name, prefixes in TAB_RULES:
        for p in prefixes:
            if var.startswith(p):
                return tab_name
    return "Other"


def partition_tab_vars(tab_name: str, vars_: Sequence[str]) -> dict[str, list[str]]:
    """Partition variables within a tab into related sub-plots."""
    plots: dict[str, list[str]] = {}

    if tab_name in ("MRAC Pitch", "MRAC Roll", "MRAC Yaw", "MRAC Z"):
        for v in vars_:
            field = v.rsplit(".", 1)[-1].split("[")[0]
            if field in {"x", "xm", "r"}:
                title = "Tracking (x, xm, r)"
            elif field in {"e", "e_dot", "e_int", "pe"}:
                title = "Errors (e, e_dot, e_int)"
            elif field in {"u_nom", "u_ad", "u_def", "p_gain", "nan_hold"}:
                title = "Control (u_nom, u_ad, u_def)"
            elif field == "Theta":
                title = "Adaptive Weights (Theta)"
            else:
                title = "Other Signals"
            plots.setdefault(title, []).append(v)

    elif tab_name == "MRAC Velocity":
        for v in vars_:
            field = v.rsplit(".", 1)[-1].split("[")[0]
            if field == "Theta":
                title = "Velocity Weights (Theta)"
            elif "x_vel." in v or "mrac_bus[4]" in v:
                title = "X Velocity"
            elif "y_vel." in v or "mrac_bus[5]" in v:
                title = "Y Velocity"
            else:
                title = "Velocity Other"
            plots.setdefault(title, []).append(v)

    elif tab_name == "Position / EKF":
        for v in vars_:
            if ".x[" in v or "Lin_Acc_" in v or "s_of_bias_" in v:
                title = "EKF States & Lin Acc"
            elif any(k in v for k in ("innov_", "rej_", "nis", ".P[", "g_ekf_gate")):
                title = "Innovations & Rejection"
            elif any(k in v for k in ("ano_of.of0_", "ano_of.of1_", "ano_of.of2_", "ano_of.earth_", "ano_of.intergral_", "ano_of.of_alt_cm", "g_of_", "g_ekf_of")):
                title = "Optical Flow & Altitude"
            elif any(k in v for k in ("of_quality", "of_update_cnt", "ano_of.gyr_", "ano_of.acc_")):
                title = "OF Sensor Raw & Quality"
            else:
                title = "Position / Other"
            plots.setdefault(title, []).append(v)

    elif tab_name == "Ext-Pos / Camera":
        for v in vars_:
            if any(k in v for k in ("g_ext_pos.a_", "g_ext_pos.c_", "g_ext_pos.e_")):
                title = "Camera Position & Error"
            else:
                title = "Camera Status & Gating"
            plots.setdefault(title, []).append(v)

    elif tab_name == "IMU / Attitude":
        for v in vars_:
            if "imu_data." in v or "Gravity_Body_" in v:
                title = "Euler Angles (Attitude)"
            elif "Gyro_" in v:
                title = "Angular Rates (Gyro)"
            elif "Acc_" in v:
                title = "Accelerations (Acc)"
            else:
                title = "IMU Other"
            plots.setdefault(title, []).append(v)

    elif tab_name == "Motors / Power":
        for v in vars_:
            if any(k in v for k in ("mymotor", "rpm_dbg", "dbg_motor_")):
                title = "Motor Outputs & RPM"
            else:
                title = "Throttle & Voltage"
            plots.setdefault(title, []).append(v)

    elif tab_name == "RC / Controller":
        for v in vars_:
            if "Remoter." in v:
                title = "RC Inputs (Remoter)"
            elif any(k in v for k in ("des_", "u_gyro")):
                title = "Setpoints & Demands"
            else:
                title = "PID Feedback & Status"
            plots.setdefault(title, []).append(v)

    elif tab_name == "MRAC Adaptive / VP":
        for v in vars_:
            if any(k in v for k in ("vp_", "kp_", "lab_stage_active", "mrac_flags", "v_arm")):
                title = "Adaptation Status & Flags"
            else:
                title = "Filter & Actuators"
            plots.setdefault(title, []).append(v)

    elif tab_name == "System / Health":
        for v in vars_:
            if any(k in v for k in ("g_loop_", "g_rtos_budget", "stabilizerTask", "xTickCount")):
                title = "Timing & Budget"
            else:
                title = "System & Health"
            plots.setdefault(title, []).append(v)

    else:
        plots["Signals"] = list(vars_)

    return plots


def plot_curve_name(var: str) -> str:
    """Return PlotJuggler curve name for a variable (matching JSON parser series path)."""
    return "/" + plot_key(var)


def load_variables_from_source(source: str | Path | Sequence[str]) -> list[str]:
    """Extract ordered unique variable names from a markdown file, JSON preset, or list."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            if p.suffix.lower() == ".json":
                data = json.loads(p.read_text(encoding="utf-8"))
                vars_: list[str] = []
                for s in data.get("slots", []):
                    vars_.extend(s.get("vars", []))
                return list(dict.fromkeys(vars_))
            elif p.suffix.lower() == ".md":
                groups = load_frames(p)
                vars_ = [v for _, symbols in groups for v in symbols]
                return list(dict.fromkeys(vars_))
            else:
                # Try JSON first, then markdown
                text = p.read_text(encoding="utf-8")
                try:
                    data = json.loads(text)
                    vars_ = []
                    for s in data.get("slots", []):
                        vars_.extend(s.get("vars", []))
                    return list(dict.fromkeys(vars_))
                except Exception:
                    groups = parse_frames_markdown(text)
                    vars_ = [v for _, symbols in groups for v in symbols]
                    return list(dict.fromkeys(vars_))
        else:
            # String might be raw markdown or json
            s_str = str(source).strip()
            if s_str.startswith("{"):
                data = json.loads(s_str)
                vars_ = []
                for s in data.get("slots", []):
                    vars_.extend(s.get("vars", []))
                return list(dict.fromkeys(vars_))
            elif "|" in s_str:
                groups = parse_frames_markdown(s_str)
                vars_ = [v for _, symbols in groups for v in symbols]
                return list(dict.fromkeys(vars_))
            raise FileNotFoundError("frames or preset file not found: %s" % source)

    # Sequence of strings
    return list(dict.fromkeys(source))


def generate_layout_xml(variables: Iterable[str]) -> str:
    """Generate PlotJuggler 3 layout XML for given variable names.

    Organizes variables into tabs by prefix, partitions each tab into related
    plots, sets plot styling and curve colors.
    """
    vars_list = list(dict.fromkeys(variables))

    # Group variables into tabs
    tabs: dict[str, list[str]] = {}
    for v in vars_list:
        tab_name = find_tab_for_var(v)
        tabs.setdefault(tab_name, []).append(v)

    # Sort tabs according to TAB_RULES order, then 'Other'
    tab_order = [name for name, _ in TAB_RULES] + ["Other"]
    sorted_tab_names = sorted(tabs.keys(), key=lambda t: tab_order.index(t) if t in tab_order else 999)

    root = ET.Element("root")
    tw = ET.SubElement(root, "tabbed_widget", name="Main Window", parent="main_window")

    pages: list[tuple[str, dict[str, list[str]]]] = []
    for tab_name in sorted_tab_names:
        # Readability: a group over MAX_CURVES splits into plots "<group> (1/2)", ...; a group needing more than two
        # plots (pitch/roll Theta, 46 weights) gets its own tab "<tab> <group>"; overflow past MAX_PLOTS -> "<tab> (2)".
        small, big = [], []
        for title, pv in partition_tab_vars(tab_name, tabs[tab_name]).items():
            parts = [pv[i:i + MAX_CURVES] for i in range(0, len(pv), MAX_CURVES)]
            named = [(title if len(parts) == 1 else f"{title} ({j + 1}/{len(parts)})", part)
                     for j, part in enumerate(parts)]
            if len(parts) > 2:
                big.append((f"{tab_name} {title}", dict(named)))
            else:
                small += named
        for k in range(0, len(small), MAX_PLOTS):
            pages.append((tab_name if k == 0 else f"{tab_name} ({k // MAX_PLOTS + 1})", dict(small[k:k + MAX_PLOTS])))
        pages += big

    for tab_name, plots_dict in pages:
        tab_el = ET.SubElement(tw, "Tab", tab_name=tab_name, containers="1")
        container_el = ET.SubElement(tab_el, "Container")

        num_plots = len(plots_dict)
        sizes_str = ";".join(f"{1.0 / num_plots:.3f}" for _ in range(num_plots)) if num_plots > 1 else "1.0"
        splitter_el = ET.SubElement(
            container_el,
            "DockSplitter",
            count=str(num_plots),
            orientation="-",
            sizes=sizes_str
        )

        for plot_title, p_vars in plots_dict.items():
            dock_area = ET.SubElement(splitter_el, "DockArea", name=plot_title)
            plot_el = ET.SubElement(dock_area, "plot", mode="TimeSeries", style="Lines")
            ET.SubElement(plot_el, "range", bottom="-1.0", top="1.0", left="0.0", right="1.0")
            ET.SubElement(plot_el, "limitY")

            for i, var_name in enumerate(p_vars):
                color = COLOR_PALETTE[i % len(COLOR_PALETTE)]
                curve_name = plot_curve_name(var_name)
                ET.SubElement(plot_el, "curve", color=color, name=curve_name)

    ET.SubElement(root, "use_relative_time_offset", enabled="1")

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def write_layout_from_frames(frames_or_preset: str | Path, out_path: str | Path) -> Path:
    """Read a frames/preset file, generate PlotJuggler XML, and write it to out_path."""
    vars_ = load_variables_from_source(frames_or_preset)
    xml_content = generate_layout_xml(vars_)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml_content, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate PlotJuggler layout XML from frames file")
    parser.add_argument("frames", help="Path to markdown frames file (.md) or VOFA+ preset JSON (.json)")
    parser.add_argument("-o", "--out", required=True, help="Output PlotJuggler XML layout file path")
    args = parser.parse_args(argv)

    out_file = write_layout_from_frames(args.frames, args.out)
    print("Wrote PlotJuggler layout to %s" % out_file)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
