"""live_plot: fast VOFA+ replacement for UAV telemetry live plotting.

Fed by ground_station.livewatch.stream_log (--plot HOST:PORT) or CSV replay.
Core data pipeline (numpy ring buffers, decimation, layout serialization)
runs headlessly without DearPyGui.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import socket
import sys
import threading
import time

import numpy as np

LAYOUTS_DIR = Path(__file__).parent / "plot_layouts"


def parse_datagram(raw: str | bytes) -> dict | None:
    """Parse a JSON datagram from stream_log.

    Expects either:
      {"type":"hdr","slot":k,"names":[...]}
      {"type":"row","slot":k,"seq":seq,"t":t_ms,"v":[...]}
    Returns dict on success, None on error or malformed payload.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    msg_type = data.get("type")
    if msg_type == "hdr":
        if "slot" in data and "names" in data and isinstance(data["names"], list):
            return data
    elif msg_type == "row":
        if ("slot" in data and "seq" in data and "t" in data
                and "v" in data and isinstance(data["v"], list)):
            return data
    return None


def min_max_decimate(t: np.ndarray, y: np.ndarray, target_points: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    """Decimate (t, y) to approximately target_points while strictly preserving
    the minimum and maximum of each bucket (peak/valley detection).
    """
    n = len(t)
    if n <= target_points or target_points < 4:
        return t, y

    num_buckets = max(1, target_points // 2)
    splits = np.array_split(np.arange(n), num_buckets)

    t_out = []
    y_out = []

    for split in splits:
        if len(split) == 0:
            continue
        sub_y = y[split]
        i_min = int(np.argmin(sub_y))
        i_max = int(np.argmax(sub_y))

        idx_min = split[i_min]
        idx_max = split[i_max]

        if idx_min <= idx_max:
            t_out.append(t[idx_min])
            y_out.append(y[idx_min])
            if idx_min != idx_max:
                t_out.append(t[idx_max])
                y_out.append(y[idx_max])
        else:
            t_out.append(t[idx_max])
            y_out.append(y[idx_max])
            t_out.append(t[idx_min])
            y_out.append(y[idx_min])

    return np.array(t_out, dtype=t.dtype), np.array(y_out, dtype=y.dtype)


def build_variable_tree(names: list[str]) -> dict:
    """Build nested dict hierarchy from dot-separated variable names.

    Leaves hold the full variable name string; internal nodes are dicts.
    Example:
      'Ctrler.Z_posPID.FB' -> {'Ctrler': {'Z_posPID': {'FB': 'Ctrler.Z_posPID.FB'}}}
    """
    root: dict = {}
    for full_name in sorted(names):
        parts = full_name.split(".")
        curr = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                curr[part] = full_name
            else:
                if part not in curr or not isinstance(curr[part], dict):
                    curr[part] = {}
                curr = curr[part]
    return root


class SlotBuffer:
    """Per-slot ring buffer holding 300 s of telemetry data in numpy arrays."""

    def __init__(self, slot: int, capacity: int = 30000, rate_hz: float = 100.0):
        self.slot = slot
        self.capacity = max(2, capacity)
        self.rate_hz = rate_hz
        self.names: list[str] = []
        self._name_to_idx: dict[str, int] = {}
        self.has_header = False

        self.t = np.zeros(self.capacity, dtype=np.float64)  # seconds
        self.seq = np.zeros(self.capacity, dtype=np.int64)
        self.data: np.ndarray | None = None  # shape: (capacity, n_vars)

        self.write_pos = 0
        self.count = 0

        self.last_seq: int | None = None
        self.seq_gaps = 0
        self.measured_hz = 0.0
        self._win_t0 = time.monotonic()
        self._win_count = 0

    def set_header(self, names: list[str]):
        """Set channel names from a header datagram. Renames existing channels if buffered."""
        self.has_header = True
        new_names = list(names)
        n_vars = len(new_names)
        if self.data is None:
            self.data = np.zeros((self.capacity, n_vars), dtype=np.float64)
        elif self.data.shape[1] != n_vars:
            old_data = self.data
            old_cols = min(old_data.shape[1], n_vars)
            new_data = np.zeros((self.capacity, n_vars), dtype=np.float64)
            new_data[:, :old_cols] = old_data[:, :old_cols]
            self.data = new_data
        self.names = new_names
        self._name_to_idx = {name: i for i, name in enumerate(new_names)}

    def add_row(self, seq: int, t_ms: float, values: list[float]):
        """Insert a row of values into the ring buffer."""
        n_val = len(values)
        if self.data is None:
            # Row arrived before header: assign slot<k>[i]
            self.names = [f"slot{self.slot}[{i}]" for i in range(n_val)]
            self._name_to_idx = {name: i for i, name in enumerate(self.names)}
            self.data = np.zeros((self.capacity, n_val), dtype=np.float64)
        elif n_val > self.data.shape[1]:
            old_data = self.data
            old_n = old_data.shape[1]
            new_data = np.zeros((self.capacity, n_val), dtype=np.float64)
            new_data[:, :old_n] = old_data
            self.data = new_data
            for i in range(old_n, n_val):
                name = f"slot{self.slot}[{i}]"
                self.names.append(name)
                self._name_to_idx[name] = i

        pos = self.write_pos
        self.t[pos] = t_ms / 1000.0
        self.seq[pos] = seq
        n_copy = min(n_val, self.data.shape[1])
        self.data[pos, :n_copy] = values[:n_copy]

        self.write_pos = (pos + 1) % self.capacity
        self.count += 1

        if self.last_seq is not None:
            gap = (seq - self.last_seq - 1) & 0xFF
            if gap > 0:
                self.seq_gaps += gap
        self.last_seq = seq

        now = time.monotonic()
        self._win_count += 1
        dt = now - self._win_t0
        if dt >= 1.0:
            self.measured_hz = self._win_count / dt
            self._win_t0 = now
            self._win_count = 0

    def get_series(self, var_name: str, window_s: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Extract chronological (t, y) arrays for var_name, optionally limited to last window_s seconds."""
        if self.count == 0 or self.data is None:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
        idx = self._name_to_idx.get(var_name)
        if idx is None:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

        if self.count < self.capacity:
            t_valid = self.t[:self.count]
            y_valid = self.data[:self.count, idx]
        else:
            pos = self.write_pos
            t_valid = np.concatenate([self.t[pos:], self.t[:pos]])
            y_valid = np.concatenate([self.data[pos:, idx], self.data[:pos, idx]])

        if window_s is not None and len(t_valid) > 0:
            t_latest = t_valid[-1]
            cutoff = t_latest - window_s
            start_idx = np.searchsorted(t_valid, cutoff)
            t_valid = t_valid[start_idx:]
            y_valid = y_valid[start_idx:]

        return t_valid, y_valid


class TelemetryStore:
    """Thread-safe store managing per-slot ring buffers."""

    def __init__(self, capacity_per_slot: int = 30000):
        self.capacity_per_slot = capacity_per_slot
        self.slots: dict[int, SlotBuffer] = {}
        self._lock = threading.Lock()

    def get_or_create_slot(self, slot: int) -> SlotBuffer:
        if slot not in self.slots:
            self.slots[slot] = SlotBuffer(slot, capacity=self.capacity_per_slot)
        return self.slots[slot]

    def feed_datagram(self, d: dict):
        with self._lock:
            msg_type = d.get("type")
            slot_id = d.get("slot", 0)
            buf = self.get_or_create_slot(slot_id)
            if msg_type == "hdr":
                buf.set_header(d.get("names", []))
            elif msg_type == "row":
                buf.add_row(d.get("seq", 0), d.get("t", 0.0), d.get("v", []))

    def all_variables(self) -> list[str]:
        with self._lock:
            res = []
            for slot in sorted(self.slots.keys()):
                res.extend(self.slots[slot].names)
            return res

    def get_series(self, var_name: str, window_s: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            for slot in self.slots.values():
                if var_name in slot._name_to_idx:
                    return slot.get_series(var_name, window_s)
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

    def get_slot_stats(self) -> list[dict]:
        with self._lock:
            stats = []
            for slot_id in sorted(self.slots.keys()):
                buf = self.slots[slot_id]
                stats.append({
                    "slot": slot_id,
                    "hz": buf.measured_hz,
                    "rows": buf.count,
                    "gaps": buf.seq_gaps,
                })
            return stats


def save_layout(name: str, layout_data: dict, directory: Path | None = None) -> Path:
    """Save plot layout configuration to JSON."""
    dest_dir = directory or LAYOUTS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = Path(name)
    if not p.suffix:
        p = p.with_suffix(".json")
    if not p.is_absolute():
        p = dest_dir / p.name
    p.write_text(json.dumps(layout_data, indent=2), encoding="utf-8")
    return p


def load_layout(name: str, directory: Path | None = None) -> dict:
    """Load plot layout configuration from JSON."""
    dest_dir = directory or LAYOUTS_DIR
    p = Path(name)
    if not p.suffix:
        p = p.with_suffix(".json")
    if not p.is_absolute():
        cand = dest_dir / p.name
        if cand.exists():
            p = cand
    if not p.exists():
        raise FileNotFoundError(f"Layout not found: {name} (tried {p})")
    return json.loads(p.read_text(encoding="utf-8"))


def load_csv_replay(stem_or_path: str, store: TelemetryStore) -> list[str]:
    """Load every <stem>.slot*.csv file into the TelemetryStore."""
    p = Path(stem_or_path)
    if p.is_dir():
        files = sorted(p.glob("*.slot*.csv"))
    else:
        stem = p.name
        if ".slot" in stem:
            stem = stem.split(".slot")[0]
        elif stem.endswith(".csv"):
            stem = stem[:-4]
        parent = p.parent if str(p.parent) != "" else Path(".")
        files = sorted(parent.glob(f"{stem}.slot*.csv"))
        if not files and p.exists() and p.is_file():
            files = [p]

    if not files:
        raise FileNotFoundError(f"No CSV slot files found matching {stem_or_path}")

    loaded = []
    for fpath in files:
        slot_id = 0
        parts = fpath.stem.split(".slot")
        if len(parts) > 1:
            try:
                slot_id = int(parts[1])
            except ValueError:
                slot_id = 0

        with fpath.open("r", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if not header or len(header) < 3:
                continue
            names = [col.strip() for col in header[3:]]
            slot_buf = store.get_or_create_slot(slot_id)
            slot_buf.set_header(names)
            for row in reader:
                if not row or len(row) < 3:
                    continue
                try:
                    t_ms = float(row[0])
                    seq = int(row[2])
                    vals = [float(x) for x in row[3:]]
                    slot_buf.add_row(seq, t_ms, vals)
                except (ValueError, IndexError):
                    continue
        loaded.append(str(fpath))
    return loaded


def run_demo_sender(host: str, port: int, stop_event: threading.Event):
    """Synthetic sender for 3 slots (sines, steps, ramps) fed to live_plot."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (host, port)

    slot0_names = ["imu_data.rol", "imu_data.pit", "imu_data.yaw", "Acc_X_Real", "Acc_Y_Real", "Acc_Z_Real"]
    slot1_names = ["ano_of.earth_x", "ano_of.earth_y", "g_ext_pos.a_x", "g_ext_pos.a_y",
                   "Ctrler.Z_posPID.FB", "ano_of.of_alt_cm", "mrac_state.z_rate.r"]
    slot2_names = ["real_voltage", "DroneStatus.ARM_Status", "s_of_bias_x", "s_of_bias_y"]

    for s, names in [(0, slot0_names), (1, slot1_names), (2, slot2_names)]:
        hdr = json.dumps({"type": "hdr", "slot": s, "names": names}).encode("utf-8")
        sock.sendto(hdr, addr)

    t0 = time.monotonic()
    last_hdr_t = t0
    seq0 = seq1 = seq2 = 0
    t_next_0 = t_next_1 = t_next_2 = t0

    while not stop_event.is_set():
        now = time.monotonic()
        t_ms = int((now - t0) * 1000)

        if now - last_hdr_t >= 1.0:
            last_hdr_t = now
            for s, names in [(0, slot0_names), (1, slot1_names), (2, slot2_names)]:
                hdr = json.dumps({"type": "hdr", "slot": s, "names": names}).encode("utf-8")
                sock.sendto(hdr, addr)

        t_sec = now - t0
        if now >= t_next_0:
            t_next_0 = now + 0.010  # 100 Hz
            v0 = [
                math.sin(t_sec * 2.0 * math.pi * 0.5) * 10.0,
                math.cos(t_sec * 2.0 * math.pi * 0.5) * 8.0,
                (t_sec * 5.0) % 360.0 - 180.0,
                math.sin(t_sec * 10.0) * 0.5,
                math.cos(t_sec * 10.0) * 0.5,
                9.81 + math.sin(t_sec * 20.0) * 0.1,
            ]
            row0 = json.dumps({"type": "row", "slot": 0, "seq": seq0 & 0xFF, "t": t_ms, "v": v0}).encode("utf-8")
            sock.sendto(row0, addr)
            seq0 += 1

        if now >= t_next_1:
            t_next_1 = now + 0.020  # 50 Hz
            sq = 20.0 if (int(t_sec) % 4 < 2) else -20.0
            v1 = [
                sq + math.sin(t_sec) * 2.0,
                math.sin(t_sec * 0.5) * 30.0,
                sq,
                math.sin(t_sec * 0.5) * 30.0 + 0.5,
                100.0 + math.sin(t_sec * 0.2) * 20.0,
                100.0 + math.sin(t_sec * 0.2) * 20.0 + 1.0,
                100.0,
            ]
            row1 = json.dumps({"type": "row", "slot": 1, "seq": seq1 & 0xFF, "t": t_ms, "v": v1}).encode("utf-8")
            sock.sendto(row1, addr)
            seq1 += 1

        if now >= t_next_2:
            t_next_2 = now + 0.040  # 25 Hz
            v2 = [
                12.4 - (t_sec * 0.005),
                1.0,
                0.02,
                -0.01,
            ]
            row2 = json.dumps({"type": "row", "slot": 2, "seq": seq2 & 0xFF, "t": t_ms, "v": v2}).encode("utf-8")
            sock.sendto(row2, addr)
            seq2 += 1

        time.sleep(0.005)
    sock.close()


def run_gui(store: TelemetryStore, udp_port: int = 47900, layout_name: str | None = None,
            stop_event: threading.Event | None = None):
    """Launch the DearPyGui interface for live plotting."""
    try:
        import dearpygui.dearpygui as dpg
    except ImportError:
        raise RuntimeError("DearPyGui is not installed. Live GUI requires dearpygui (pip install dearpygui).")

    # Load initial layout
    initial_layout = None
    if layout_name:
        try:
            initial_layout = load_layout(layout_name)
        except Exception as e:
            print(f"Warning: could not load layout {layout_name}: {e}", file=sys.stderr)

    if initial_layout is None:
        try:
            initial_layout = load_layout("fusion")
        except Exception:
            initial_layout = {
                "name": "default",
                "time_window": 30.0,
                "autoscale_y": true,
                "panels": [{"title": "Telemetry", "variables": []}],
            }

    state = {
        "paused": False,
        "time_window": float(initial_layout.get("time_window", 30.0)),
        "autoscale_y": bool(initial_layout.get("autoscale_y", True)),
        "panels": initial_layout.get("panels", []),
        "active_panel_idx": 0,
        "filter_query": "",
        "tree_rendered_vars": set(),
    }

    dpg.create_context()
    dpg.create_viewport(title="Live Telemetry Plot", width=1280, height=800)
    dpg.setup_dearpygui()

    series_tags: dict[str, str] = {}  # key: f"{panel_idx}:{var_name}" -> dpg series tag
    plot_tags: dict[int, dict] = {}   # panel_idx -> {"plot": tag, "x_axis": tag, "y_axis": tag}

    def on_pause_toggle(sender, app_data):
        state["paused"] = not state["paused"]
        dpg.set_item_label("pause_btn", "Resume" if state["paused"] else "Pause")

    def on_window_change(sender, app_data):
        state["time_window"] = float(app_data)

    def on_autoscale_toggle(sender, app_data):
        state["autoscale_y"] = bool(app_data)

    def add_var_to_active_panel(var_name: str):
        if not state["panels"]:
            state["panels"].append({"title": f"Panel {len(state['panels']) + 1}", "variables": []})
        idx = max(0, min(state["active_panel_idx"], len(state["panels"]) - 1))
        if var_name not in state["panels"][idx]["variables"]:
            state["panels"][idx]["variables"].append(var_name)
            rebuild_plots()

    def remove_var_from_panel(panel_idx: int, var_name: str):
        if 0 <= panel_idx < len(state["panels"]):
            if var_name in state["panels"][panel_idx]["variables"]:
                state["panels"][panel_idx]["variables"].remove(var_name)
                rebuild_plots()

    def add_new_panel():
        state["panels"].append({"title": f"Panel {len(state['panels']) + 1}", "variables": []})
        state["active_panel_idx"] = len(state["panels"]) - 1
        rebuild_plots()

    def remove_panel(idx: int):
        if 0 <= idx < len(state["panels"]):
            state["panels"].pop(idx)
            state["active_panel_idx"] = max(0, min(state["active_panel_idx"], len(state["panels"]) - 1))
            rebuild_plots()

    def set_active_panel(idx: int):
        state["active_panel_idx"] = idx
        rebuild_plots()

    def save_current_layout(sender, app_data):
        lname = dpg.get_value("layout_name_input") or "layout"
        data = {
            "name": lname,
            "time_window": state["time_window"],
            "autoscale_y": state["autoscale_y"],
            "panels": state["panels"],
        }
        try:
            save_layout(lname, data)
            dpg.set_value("status_bar", f"Saved layout {lname}.json")
        except Exception as exc:
            dpg.set_value("status_bar", f"Save layout error: {exc}")

    def load_user_layout(sender, app_data):
        lname = dpg.get_value("layout_name_input") or "fusion"
        try:
            ld = load_layout(lname)
            state["time_window"] = float(ld.get("time_window", 30.0))
            state["autoscale_y"] = bool(ld.get("autoscale_y", True))
            state["panels"] = ld.get("panels", [])
            state["active_panel_idx"] = 0
            dpg.set_value("window_slider", state["time_window"])
            dpg.set_value("autoscale_check", state["autoscale_y"])
            rebuild_plots()
            dpg.set_value("status_bar", f"Loaded layout {lname}")
        except Exception as exc:
            dpg.set_value("status_bar", f"Load layout error: {exc}")

    def render_tree_node(name: str, node: dict | str, parent_tag: str, query: str):
        if isinstance(node, dict):
            # Internal node
            # Filter check: does this branch contain query?
            if query:
                # check if any descendant has query
                def matches(n):
                    if isinstance(n, str):
                        return query in n.lower()
                    return any(matches(v) for v in n.values())
                if not matches(node):
                    return
            tag = f"tree_{parent_tag}_{name}"
            with dpg.tree_node(label=name, parent=parent_tag, default_open=bool(query)):
                for k, v in sorted(node.items()):
                    render_tree_node(k, v, parent_tag, query)
        else:
            # Leaf node (node is full variable name)
            full_name = node
            if query and query not in full_name.lower():
                return
            leaf_tag = f"leaf_{full_name}"
            dpg.add_selectable(
                label=name,
                parent=parent_tag,
                callback=lambda: add_var_to_active_panel(full_name),
            )
            with dpg.popup(dpg.last_item(), mousebutton=dpg.mvMouseButton_Right):
                dpg.add_menu_item(
                    label=f"Add '{full_name}' to Active Panel",
                    callback=lambda: add_var_to_active_panel(full_name),
                )

    def refresh_variable_tree():
        all_vars = store.all_variables()
        if set(all_vars) == state["tree_rendered_vars"] and not state.get("filter_dirty", False):
            return
        state["tree_rendered_vars"] = set(all_vars)
        state["filter_dirty"] = False

        dpg.delete_item("tree_container", children_only=True)
        tree = build_variable_tree(all_vars)
        q = state["filter_query"].strip().lower()
        for k, v in sorted(tree.items()):
            render_tree_node(k, v, "tree_container", q)

    def on_filter_change(sender, app_data):
        state["filter_query"] = app_data
        state["filter_dirty"] = True
        refresh_variable_tree()

    def rebuild_plots():
        dpg.delete_item("plots_container", children_only=True)
        series_tags.clear()
        plot_tags.clear()

        for p_idx, panel in enumerate(state["panels"]):
            is_active = (p_idx == state["active_panel_idx"])
            p_title = panel.get("title", f"Panel {p_idx + 1}")

            with dpg.group(parent="plots_container"):
                # Header row
                with dpg.group(horizontal=True):
                    dpg.add_text(f"[{'ACTIVE' if is_active else '     '}] {p_title}", color=[100, 200, 255] if is_active else [200, 200, 200])
                    dpg.add_button(label="Select Panel", callback=lambda _, __, u=p_idx: set_active_panel(u))
                    dpg.add_button(label="Remove Panel", callback=lambda _, __, u=p_idx: remove_panel(u))

                # Traces list with removal buttons
                with dpg.group(horizontal=True):
                    for vname in panel.get("variables", []):
                        dpg.add_button(
                            label=f"x {vname}",
                            callback=lambda _, __, u=p_idx, vn=vname: remove_var_from_panel(u, vn),
                        )

                # DPG Plot
                plot_tag = f"plot_{p_idx}"
                x_tag = f"x_axis_{p_idx}"
                y_tag = f"y_axis_{p_idx}"
                with dpg.plot(label=p_title, height=220, width=-1, tag=plot_tag):
                    dpg.add_plot_legend()
                    dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=x_tag)
                    dpg.add_plot_axis(dpg.mvYAxis, label="Value", tag=y_tag)
                    for vname in panel.get("variables", []):
                        stag = f"series_{p_idx}_{vname}"
                        series_tags[f"{p_idx}:{vname}"] = stag
                        dpg.add_line_series([], [], label=vname, parent=y_tag, tag=stag)

                plot_tags[p_idx] = {"plot": plot_tag, "x_axis": x_tag, "y_axis": y_tag}

    with dpg.window(tag="main_window", label="Live Telemetry", no_close=True):
        # Top toolbar
        with dpg.group(horizontal=True):
            dpg.add_button(tag="pause_btn", label="Pause", callback=on_pause_toggle)
            dpg.add_slider_float(tag="window_slider", label="Window (s)", default_value=state["time_window"],
                                 min_value=5.0, max_value=300.0, callback=on_window_change, width=150)
            dpg.add_checkbox(tag="autoscale_check", label="Autoscale Y", default_value=state["autoscale_y"],
                             callback=on_autoscale_toggle)
            dpg.add_button(label="+ Add Panel", callback=add_new_panel)
            dpg.add_text(" | Layout:")
            dpg.add_input_text(tag="layout_name_input", default_value=initial_layout.get("name", "fusion"), width=120)
            dpg.add_button(label="Save", callback=save_current_layout)
            dpg.add_button(label="Load", callback=load_user_layout)

        dpg.add_separator()

        # Main layout: Left (tree) / Right (plots)
        with dpg.group(horizontal=True):
            # Left Child
            with dpg.child_window(width=300, height=-30):
                dpg.add_text("Variables", color=[180, 220, 255])
                dpg.add_input_text(hint="Filter...", callback=on_filter_change, width=-1)
                with dpg.child_window(tag="tree_container", border=False):
                    pass

            # Right Child
            with dpg.child_window(height=-30):
                with dpg.child_window(tag="plots_container", border=False):
                    pass

        dpg.add_text(tag="status_bar", default_value="Waiting for telemetry...")

    dpg.set_primary_window("main_window", True)
    dpg.show_viewport()
    rebuild_plots()

    # 30 fps render loop
    target_dt = 1.0 / 30.0
    last_tree_check = 0.0

    while dpg.is_dearpygui_running():
        loop_start = time.monotonic()
        if stop_event and stop_event.is_set():
            break

        # Periodically refresh tree if new variables arrived
        if loop_start - last_tree_check > 0.5:
            last_tree_check = loop_start
            refresh_variable_tree()

        # Update plots if not paused
        if not state["paused"]:
            max_t = 0.0
            for p_idx, panel in enumerate(state["panels"]):
                for vname in panel.get("variables", []):
                    stag = series_tags.get(f"{p_idx}:{vname}")
                    if not stag:
                        continue
                    t_arr, y_arr = store.get_series(vname, window_s=state["time_window"])
                    if len(t_arr) > 0:
                        max_t = max(max_t, t_arr[-1])
                        t_dec, y_dec = min_max_decimate(t_arr, y_arr, target_points=2000)
                        dpg.set_value(stag, [t_dec.tolist(), y_dec.tolist()])

            # Linked time axis update
            if max_t > 0:
                t_min = max(0.0, max_t - state["time_window"])
                for p_idx in plot_tags:
                    x_tag = plot_tags[p_idx]["x_axis"]
                    y_tag = plot_tags[p_idx]["y_axis"]
                    dpg.set_axis_limits(x_tag, t_min, max_t)
                    if state["autoscale_y"]:
                        dpg.set_axis_limits_auto(y_tag)

        # Update status bar
        stats = store.get_slot_stats()
        if stats:
            s_texts = [f"Slot {s['slot']}: {s['hz']:.1f} Hz ({s['rows']} rows, {s['gaps']} gaps)" for s in stats]
            dpg.set_value("status_bar", " | ".join(s_texts))

        dpg.render_dearpygui_frame()

        elapsed = time.monotonic() - loop_start
        if elapsed < target_dt:
            time.sleep(target_dt - elapsed)

    dpg.destroy_context()


def main(argv=None):
    ap = argparse.ArgumentParser(description="live_plot: fast VOFA+ replacement for WiFi telemetry")
    ap.add_argument("--port", type=int, default=47900, help="UDP listen port (default: 47900)")
    ap.add_argument("--csv", default=None, metavar="STEM", help="replay: load every <stem>.slot*.csv")
    ap.add_argument("--demo", action="store_true", help="send synthetic sines/steps for 3 slots via UDP")
    ap.add_argument("--layout", default="fusion", help="load saved layout (default: fusion)")
    args = ap.parse_args(argv)

    store = TelemetryStore()
    stop_event = threading.Event()

    if args.csv:
        try:
            loaded = load_csv_replay(args.csv, store)
            print(f"Loaded {len(loaded)} slot CSV file(s) for replay: {', '.join(loaded)}")
        except Exception as e:
            print(f"CSV replay error: {e}", file=sys.stderr)

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(("0.0.0.0", args.port))
    udp_sock.settimeout(0.2)

    def udp_listener():
        while not stop_event.is_set():
            try:
                data, _ = udp_sock.recvfrom(65535)
            except (socket.timeout, BlockingIOError, OSError):
                continue
            parsed = parse_datagram(data)
            if parsed:
                store.feed_datagram(parsed)
        udp_sock.close()

    listener_thread = threading.Thread(target=udp_listener, daemon=True)
    listener_thread.start()

    if args.demo:
        demo_thread = threading.Thread(target=run_demo_sender, args=("127.0.0.1", args.port, stop_event), daemon=True)
        demo_thread.start()
        print(f"Demo sender started targeting UDP 127.0.0.1:{args.port}")

    try:
        import dearpygui.dearpygui
        dpg_available = True
    except ImportError:
        dpg_available = False

    if dpg_available:
        try:
            run_gui(store, udp_port=args.port, layout_name=args.layout, stop_event=stop_event)
        finally:
            stop_event.set()
    else:
        print("DearPyGui is not installed; running in headless receiver mode.")
        print(f"Listening on UDP port {args.port}. Press Ctrl+C to stop.")
        try:
            while not stop_event.is_set():
                time.sleep(1.0)
                stats = store.get_slot_stats()
                stat_strs = [f"slot {s['slot']}: {s['hz']:.1f} Hz, {s['rows']} rows, {s['gaps']} gaps" for s in stats]
                if stat_strs:
                    print(" | ".join(stat_strs))
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            stop_event.set()


if __name__ == "__main__":
    main()
