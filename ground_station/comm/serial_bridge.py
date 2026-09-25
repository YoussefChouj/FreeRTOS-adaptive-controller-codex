from __future__ import annotations

import json
import argparse
import queue
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import serial  # type: ignore
    import serial.tools.list_ports  # noqa: F401  (used by _list_com_ports)
except ImportError:  # pragma: no cover
    serial = None


# Thread-safe command queue: push dicts to send commands to STM32.
# Expected dict shape:
#   {"cmd_id": int, "index": int, "value": float}
#
# Firmware CMD IDs (TASK/send_data.c Process_GroundStation_Command).
# _pack_command_frame always sends: [0xCC][0xDD][cmd_id][index][float32 LE][CRC8] — no special packing per ID.
#
#   0x01 PID gains   0x02 MRAC gamma   0x03 mixer / u_max
#   0x04 flight mode (idx 0=DangerousStop+abort paths, 1=SDK)
#   0x05 MRAC What_limit   0x06 virtual RC sticks -> virtual_rc_sticks[] (SBUS lost + SDK only)
#   0x07 bench mode (idx 0, val>=0.5 on)   0x08 MRAC What_tol
#   0x09 GS safety: idx 0=max_horiz_m/s 1=max_vert_m/s 2=max_pitch_deg 3=max_roll_deg
#
#   0x0A TWC (point target) — FlyMode_SDK only:
#        idx 0=target_x  1=target_y  2=target_z  3=yaw_deg  4=execute (1=start, 0=stop)
#   0x0B sinusoid path — FlyMode_SDK only:
#        idx 0=center_x 1=center_y 2=center_z 3=amplitude_m 4=freq_Hz 5=duration_s
#            6=axis (0=X 1=Y 2=Z)  7=active (1=start path, 0=disable)
#   0x0C circle path — FlyMode_SDK only:
#        idx 0=center_x 1=center_y 2=center_z 3=radius_m 4=omega_rad_s 5=duration_s  6=active (1=start)
#   0x0D abort all paths (GroundStation_AbortAllPaths) — idx 0 (value ignored; use 1.0)
#   0x0E arm/disarm — idx 0: val>=0.5 = arm (GS_KeySDKflag=1, ARM_REQUEST, RCInput authority on),
#                              val<0.5  = disarm (GS_KeySDKflag=0, DISARM_REQUEST, authority off)
#   0x0F MRAC flags — idx: 0=adaptation_on 1=projection_on 2=deadzone_on 3=hard_freeze_on
#                          4=tanh_saturation_on 5=e_modification_on 6=l1_filtering_on
#                          7=axis_enable_pitch 8=axis_enable_roll 9=axis_enable_yaw
#                          10=output_injection_on 11=id_frame_on (0x03 @100Hz)
#                          12=of_frame_on (OF calibration frame 0x05 @200Hz, replaces A/B)
#                          val>=0.5 = ON, val<0.5 = OFF
#   0x10 reset world-frame optical-flow origin — idx 0 (value ignored; use 1.0)
#   0x17 capture optical-flow velocity bias — idx 0 (value ignored; use 1.0).
#        Drone must be level and still; firmware averages of2_dx_fix/dy_fix ~2 s into
#        s_of_bias_x/y (echoed as of.bias_x/y in the 0x05 frame). Kills earth_x/y drift.
#   0x18 force_recal (ADR-0011) — idx 0, value ignored (use 1.0).
#        Refuses unless flight_phase==GROUND_IDLE and DisArmed. On accept: clears
#        g_cal_health (except MANUAL_ORIGIN_RESET=0x80), resets s_cal_trim/s_cal_hot,
#        and forces g_estimator_ready=0 so the cold cal runs again. Use after a hard
#        gyro/accel swap or if cold cal converged badly on the bench.
#   0x11 figure-8 (lemniscate) path — FlyMode_SDK only:
#        idx 0=center_x(cm) 1=center_y(cm) 2=center_z(m) 3=amplitude(cm)
#            4=angular_speed(rad/s) 5=duration(s) 6=type(0=Bernoulli,1=Gerono)
#            7=activate(val>=0.5)
#   0x12 waypoint density — idx 0=spacing in loc-PID units (cm); 0 = continuous.
#        Shared across sinusoid/circle/figure-8 (reference arc-length quantizer).
#   0x14 SysID excitation (ADR-0004) — set params (idx 0-5) then start/abort (idx 6):
#        idx 0=axis (0=pitch 1=roll 2=yaw 3=Z)  1=signal (0=chirp 1=multisine)
#            2=f0_Hz  3=f1_Hz  4=amplitude (deg/s; Z in m/s)  5=duration_s
#            6=start (val>=0.5 -> SysID_Start) / abort (val<0.5 -> SysID_Abort)
#        Dashboard sends 0x10 (OF-origin reset) immediately before idx 6 start.
# Must match GS_PROTO_VERSION in Global_file/global_declare.h.
# Increment both when the telemetry frame layout or CMD semantics change.
# v14: Frame 0x05 grew 39->53 B always-on (acc_bias, gyro_bias, cal_health).
#       With EKF_TELEM_ENABLED=1: 53->73 B (v_body, P_diag, NIS, K_last).
#       Added CMD 0x18 force_recal (ADR-0011).
GS_PROTO_VERSION: int = 15

cmd_queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()


def _parse_simple_yaml(path: Path) -> Dict[str, Any]:
    """
    Minimal YAML parser for simple "key: value" files (no nesting).
    This avoids adding a PyYAML dependency just for config loading.
    """
    result: Dict[str, Any] = {}
    if not path.exists():
        return result

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        # Cast a couple of expected numeric fields.
        if key in {
            "baud_rate",
            "vofa_port",
            "vofa_port_a",
            "vofa_port_b",
            "simulate_udp_port",
            "cmd_udp_port",
            "telemetry_mirror_port",
            "com_probe_timeout_s",
        }:
            try:
                result[key] = int(value)
            except ValueError:
                pass
        # Space-separated COM scan list (e.g. "COM3 COM4 COM5").
        elif key == "com_scan":
            result[key] = [tok.strip().upper() for tok in value.split() if tok.strip()]
        else:
            result[key] = value
    return result


def load_config() -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "serial_port": "AUTO",
        "serial_port_fallback": "COM3",
        "baud_rate": 115200,
        "com_scan": ["COM3", "COM4", "COM5", "COM6", "COM7", "COM8"],
        "com_probe_timeout_s": 1.5,
        "com_match_hints": ["CH340", "CP210x", "FTDI", "USB-SERIAL"],
        "vofa_host": "127.0.0.1",
        "vofa_port": 1347,
        # VOFA+ JustFloat: fixed channel count per connection (Frame A vs Frame B differ).
        "vofa_port_a": 1347,
        "vofa_port_b": 1348,
        "simulate_udp_port": 50007,
        # Dashboard -> serial_bridge control (must differ from vofa_port_b).
        "cmd_udp_port": 1349,
        # One-way JSON telemetry mirror to dashboard (UDP send to this host:port after each decoded frame).
        "telemetry_mirror_host": "127.0.0.1",
        "telemetry_mirror_port": 1350,
        # Dashboard binds here to receive the mirror (same machine as bridge by default).
        "telemetry_mirror_bind": "127.0.0.1",
        # VOFA+ output mode (see _emit_vofa_output):
        #   FireWater text modes often show names in the Rx log but still map to a single channel (I0) on UDP
        #   in VOFA+ 1.3.x — use JustFloat for real multi-channel plots.
        #   firewater_multiline   — legacy: TWO UDP packets (data block + "\\n")
        #   firewater_single_line — one packet: lines "name: val\\n" + final "\\n" (for logging / other tools)
        #   firewater_header_csv  — "!names\\n" then "v0,v1,...\\n"
        #   justfloat (default)   — LE float32 per channel + tail 00 00 80 7F; VOFA shows I0,I1,... (rename in UI)
        #   Channel order = order of (name, value) tuples in _unpack_frame_a / _unpack_frame_b.
        "vofa_format": "justfloat",
    }

    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    overrides = _parse_simple_yaml(config_path)
    defaults.update(overrides)
    # Legacy: if config only has vofa_port, mirror it to vofa_port_a when vofa_port_a omitted.
    if "vofa_port_a" not in overrides and "vofa_port" in defaults:
        try:
            defaults["vofa_port_a"] = int(defaults["vofa_port"])
        except (TypeError, ValueError):
            pass
    return defaults


def _xor_crc8(data: Iterable[int]) -> int:
    """
    CRC8 in this project is an XOR checksum over bytes.
    Matches the C code:
      crc = 0;
      for (i = 2; i < len; i++) { crc ^= Buf[i]; }
    """
    crc = 0
    for b in data:
        crc ^= (b & 0xFF)
    return crc & 0xFF


def _crc16_ccitt(data: Iterable[int]) -> int:
    """
    CRC16-CCITT (XModem), init 0x0000, poly 0x1021, no reflection, no final XOR.
    Frame C (0x06) uses this instead of the XOR-CRC8 the other frames use.
    Matches crc16_xmodem() in TASK/send_data.c; transmitted big-endian (hi, lo).
    """
    crc = 0x0000
    for b in data:
        crc ^= (b & 0xFF) << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def _list_com_ports(hints: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """Return [(device, description)] for every enumerated COM port.

    If `hints` is given (case-insensitive substrings), filter to ports whose
    description matches any hint. Falls back to all ports when nothing matches.
    """
    if serial is None:  # pragma: no cover
        return []
    try:
        all_ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
    except Exception:
        return []
    rows = [(p.device, p.description or "") for p in all_ports]
    if hints:
        keep = [r for r in rows if any(h.lower() in r[1].lower() for h in hints)]
        if keep:
            return keep
    return rows


def _probe_port(port: str, baud: int, timeout_s: float) -> int:
    """Open `port` briefly and count incoming bytes. Returns -1 on open failure."""
    if serial is None:  # pragma: no cover
        return -1
    try:
        with serial.Serial(port=port, baudrate=baud, timeout=0.05) as ser:  # type: ignore[attr-defined]
            t_end = time.monotonic() + float(timeout_s)
            n = 0
            while time.monotonic() < t_end:
                chunk = ser.read(4096)
                if chunk:
                    n += len(chunk)
            return n
    except Exception:
        return -1


def scan_com_ports(
    candidates: Optional[List[str]] = None,
    baud: Optional[int] = None,
    timeout_s: Optional[float] = None,
    hints: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Probe each candidate COM port and return {port: {desc, bytes, error}}.

    Used by --scan-com and the AUTO port resolver. Bytes>0 means telemetry
    is flowing; error is set only on open failure.
    """
    cfg = load_config()
    cand = list(candidates) if candidates is not None else list(cfg.get("com_scan", []))
    br = int(baud) if baud is not None else int(cfg.get("baud_rate", 115200))
    to = float(timeout_s) if timeout_s is not None else float(cfg.get("com_probe_timeout_s", 1.5))
    hint_list = list(hints) if hints is not None else list(cfg.get("com_match_hints", []))
    enums = {dev: desc for dev, desc in _list_com_ports(hint_list)}
    out: Dict[str, Dict[str, Any]] = {}
    for port in cand:
        info: Dict[str, Any] = {"desc": enums.get(port, ""), "bytes": 0, "error": None}
        if port not in enums:
            info["error"] = "not enumerated"
            out[port] = info
            continue
        n = _probe_port(port, br, to)
        if n < 0:
            info["error"] = "open failed"
        else:
            info["bytes"] = int(n)
        out[port] = info
    return out


def resolve_serial_port(
    explicit: Optional[str] = None,
    *,
    candidates: Optional[List[str]] = None,
    baud: Optional[int] = None,
    timeout_s: Optional[float] = None,
) -> str:
    """Pick the COM port the bridge should open.

    - `explicit` is honored if non-empty and not "AUTO" / "auto".
    - Otherwise enumerate `candidates` (or `com_scan` from config), probe each,
      return the first that emits >0 bytes within `timeout_s`. If none stream,
      return `serial_port_fallback` from config so the bridge still starts and
      fails loudly downstream instead of crashing here.
    """
    cfg = load_config()
    if explicit and explicit.strip().upper() != "AUTO":
        return explicit.strip()
    fb = str(cfg.get("serial_port_fallback", "COM3"))
    rows = scan_com_ports(candidates=candidates, baud=baud, timeout_s=timeout_s)
    for port, info in rows.items():
        if info.get("bytes", 0) > 0:
            print(
                f"[AUTO-COM] selected {port} (desc='{info.get('desc','')}', "
                f"{info['bytes']} B in {cfg.get('com_probe_timeout_s', 1.5)}s)",
                flush=True,
            )
            return port
    print(
        f"[AUTO-COM] no telemetry seen on candidates "
        f"{list(rows.keys())}; falling back to {fb}",
        flush=True,
    )
    return fb


def vofa_channel_name(internal_name: str) -> str:
    """FireWater / CSV channel names: dots -> underscores (mrac.pitch.e -> mrac_pitch_e)."""
    return internal_name.replace(".", "_")


class SerialBridge:
    SYNC_0 = 0xAA
    SYNC_1 = 0xBB
    CMD_0 = 0xCC
    CMD_1 = 0xDD

    def __init__(
        self,
        serial_port: Optional[str] = None,
        baud_rate: Optional[int] = None,
        vofa_host: Optional[str] = None,
        vofa_port: Optional[int] = None,
        vofa_port_a: Optional[int] = None,
        vofa_port_b: Optional[int] = None,
        *,
        serial_read_timeout_s: float = 0.2,
        simulate: bool = False,
        simulate_udp_port: Optional[int] = None,
        simulate_bind_host: str = "127.0.0.1",
        cmd_udp_port: Optional[int] = None,
        cmd_bind_host: str = "127.0.0.1",
    ) -> None:
        cfg = load_config()
        self.serial_port = serial_port if serial_port is not None else cfg["serial_port"]
        self.baud_rate = baud_rate if baud_rate is not None else int(cfg["baud_rate"])
        # AUTO port resolution (ADR-0007): probe candidates, pick first that
        # streams telemetry. Falls back to serial_port_fallback.
        if isinstance(self.serial_port, str) and self.serial_port.strip().upper() == "AUTO":
            self.serial_port = resolve_serial_port()
        self.vofa_host = vofa_host if vofa_host is not None else cfg["vofa_host"]
        if vofa_port_a is not None:
            self.vofa_port_a = int(vofa_port_a)
        elif vofa_port is not None:
            self.vofa_port_a = int(vofa_port)
        else:
            self.vofa_port_a = int(cfg.get("vofa_port_a", cfg.get("vofa_port", 1347)))
        self.vofa_port_b = (
            int(vofa_port_b) if vofa_port_b is not None else int(cfg.get("vofa_port_b", 1348))
        )
        self.vofa_port = self.vofa_port_a  # legacy alias (Frame A port)
        self._simulate = bool(simulate)
        self._simulate_udp_port = (
            int(simulate_udp_port)
            if simulate_udp_port is not None
            else int(cfg.get("simulate_udp_port", 50007))
        )
        self._simulate_bind_host = simulate_bind_host

        self._cmd_udp_port = (
            int(cmd_udp_port) if cmd_udp_port is not None else int(cfg.get("cmd_udp_port", 1349))
        )
        self._cmd_bind_host = cmd_bind_host

        self._telemetry_mirror_host = str(cfg.get("telemetry_mirror_host", "127.0.0.1"))
        self._telemetry_mirror_port = int(cfg.get("telemetry_mirror_port", 1350))
        self._mirror_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.serial_read_timeout_s = float(serial_read_timeout_s)

        self._stop_event = threading.Event()
        self._rx_thread: Optional[threading.Thread] = None
        self._cmd_thread: Optional[threading.Thread] = None
        self._cmd_udp_thread: Optional[threading.Thread] = None

        self._serial: Any = None
        self._simulate_sock: Optional[socket.socket] = None
        self._cmd_udp_sock: Optional[socket.socket] = None
        self._write_lock = threading.Lock()

        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Thread-safe "latest known values" for the GUI.
        self._state_lock = threading.Lock()
        self._last_max_num_basis: int = 8
        self._last_arm: Optional[float] = None
        self._last_flymode: Optional[float] = None
        self._last_sbus_lost: Optional[float] = None
        self._last_rc_authority: Optional[float] = None
        self._last_telemetry_a: Dict[str, float] = {}
        self._last_telemetry_b: Dict[str, float] = {}
        self._last_telemetry_id: Dict[str, float] = {}
        self._last_telemetry_bench: Dict[str, float] = {}
        self._last_telemetry_of: Dict[str, float] = {}
        self._last_telemetry_c: Dict[str, float] = {}
        self._telemetry_lock = threading.Lock()

        # Per-frame arrival timestamps for stale-data guards (used by _get_telemetry_snapshot).
        self._last_frame_a_t: float = 0.0
        self._last_frame_b_t: float = 0.0

        # Debug printing throttle (simulate mode can be very chatty).
        self._last_telemetry_print_t = 0.0
        self._telemetry_print_interval_s = 1.0

        self._vofa_format = str(cfg.get("vofa_format", "justfloat")).strip().lower()
        self._vofa_header_sent_a = False
        self._vofa_header_sent_b = False

    def get_last_max_num_basis(self) -> int:
        with self._state_lock:
            return int(self._last_max_num_basis)

    def get_last_arm_status(self) -> Optional[float]:
        with self._state_lock:
            return self._last_arm

    def get_last_flymode(self) -> Optional[float]:
        with self._state_lock:
            return self._last_flymode

    def get_last_sbus_lost(self) -> Optional[float]:
        with self._state_lock:
            return self._last_sbus_lost

    def get_telemetry_snapshot(self) -> Tuple[Dict[str, Optional[float]], Dict[str, Optional[float]]]:
        """Latest decoded Frame A / Frame B variables (thread-safe, for GUI + logging).

        If a frame type has not arrived within its staleness window, its values are
        replaced with None so the dashboard can display '--' instead of stale garbage
        (e.g., the 1e+33 / 1e+29 / 2.1e+9 values seen when Frame B decode silently
        fails due to a payload-length mismatch between v13 firmware and a v14-only bridge).

        The window is PER FRAME because A and B do not run at the same rate. Measured
        on the drone over 20 s, 2026-07-29 (UART5 at 101 % of its 11520 B/s cap):

            Frame A  54.4 Hz   worst gap 0.22 s
            Frame B  11.8 Hz   worst gap 0.42 s

        A single 0.5 s window sat just above Frame B's worst gap, so ordinary jitter on
        a saturated link intermittently nulled every B value -- blanking the PID/MRAC
        panels and the XY position plots while the faster A/C panels kept updating.
        Frame B's window is therefore sized off its own cadence (~18 missed frames),
        which still catches a genuinely dead link within two seconds.
        """
        with self._telemetry_lock:
            now = time.monotonic()
            STALE_A_S = 0.5   # ~27 missed frames at the measured 54 Hz
            STALE_B_S = 1.5   # ~18 missed frames at the measured 12 Hz
            a_stale = (now - self._last_frame_a_t) > STALE_A_S
            b_stale = (now - self._last_frame_b_t) > STALE_B_S
            a = {k: None if a_stale else float(v) for k, v in self._last_telemetry_a.items()}
            b = {k: None if b_stale else float(v) for k, v in self._last_telemetry_b.items()}
            return (a, b)

    def start(self) -> None:
        if self._rx_thread and self._rx_thread.is_alive():
            return
        if not self._simulate and serial is None:  # pragma: no cover
            raise ImportError(
                "pyserial is required for SerialBridge (unless --simulate). Install with: pip install pyserial"
            )

        self._stop_event.clear()

        if self._simulate:
            self._serial = None
            self._simulate_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._simulate_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._simulate_sock.bind((self._simulate_bind_host, self._simulate_udp_port))
            self._simulate_sock.settimeout(0.2)
            rx_target = self._rx_loop_udp
        else:
            self._simulate_sock = None
            self._serial = serial.Serial(  # type: ignore[attr-defined]
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=self.serial_read_timeout_s,
            )
            rx_target = self._rx_loop

        self._rx_thread = threading.Thread(target=rx_target, name="serial_bridge_rx", daemon=True)
        self._cmd_thread = threading.Thread(
            target=self._cmd_loop, name="serial_bridge_cmd", daemon=True
        )

        # Localhost UDP control channel for the dashboard (JSON commands).
        self._cmd_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._cmd_udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._cmd_udp_sock.bind((self._cmd_bind_host, self._cmd_udp_port))
        self._cmd_udp_sock.settimeout(0.2)
        self._cmd_udp_thread = threading.Thread(
            target=self._cmd_udp_loop, name="serial_bridge_cmd_udp", daemon=True
        )

        self._rx_thread.start()
        self._cmd_thread.start()
        self._cmd_udp_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

        # Let threads exit their loops quickly.
        try:
            if self._simulate_sock is not None:
                try:
                    self._simulate_sock.close()
                except Exception:
                    pass
                self._simulate_sock = None
            if self._serial is not None:
                try:
                    self._serial.cancel_read()  # type: ignore[attr-defined]
                except Exception:
                    pass
            if self._cmd_udp_sock is not None:
                try:
                    self._cmd_udp_sock.close()
                except Exception:
                    pass
                self._cmd_udp_sock = None
            if getattr(self, "_mirror_sock", None) is not None:
                try:
                    self._mirror_sock.close()
                except Exception:
                    pass
                self._mirror_sock = None
        finally:
            try:
                if self._serial is not None:
                    self._serial.close()
            except Exception:
                pass

        # Best-effort join (threads are daemon; don't block indefinitely).
        for t in (self._rx_thread, self._cmd_thread, self._cmd_udp_thread):
            if t is not None and t.is_alive():
                t.join(timeout=1.0)

    def _read_exact(self, n: int) -> Optional[bytes]:
        if self._serial is None:
            return None
        buf = bytearray()
        while len(buf) < n and not self._stop_event.is_set():
            chunk = self._serial.read(n - len(buf))
            if not chunk:
                continue
            buf.extend(chunk)
        if len(buf) != n:
            return None
        return bytes(buf)

    def _emit_vofa_output(self, frame_type: int, lines: List[Tuple[str, float]]) -> None:
        """Send one telemetry burst to VOFA+ according to config `vofa_format` and frame type."""
        dest_port = self.vofa_port_a if frame_type == 0x01 else self.vofa_port_b
        fmt = self._vofa_format
        if fmt == "justfloat":
            self._emit_justfloat(lines, dest_port)
        elif fmt == "firewater_single_line":
            self._emit_firewater_single_line(lines, dest_port)
        elif fmt == "firewater_header_csv":
            self._emit_firewater_header_csv(lines, frame_type, dest_port)
        elif fmt == "firewater_multiline":
            self._emit_firewater_multiline(lines, dest_port)
        else:
            self._emit_firewater_single_line(lines, dest_port)

    def _emit_firewater_multiline(self, lines: List[Tuple[str, float]], dest_port: int) -> None:
        # One "name: val\\n" per variable, then a blank line (second UDP) to advance time axis.
        dat = "".join(
            f"{vofa_channel_name(name)}: {val:.6f}\n" for name, val in lines
        ).encode("utf-8")
        dest = (self.vofa_host, dest_port)
        self._udp_sock.sendto(dat, dest)
        self._udp_sock.sendto(b"\n", dest)

    def _emit_firewater_single_line(self, lines: List[Tuple[str, float]], dest_port: int) -> None:
        # FireWater: one line per channel, entire burst in a SINGLE UDP datagram (VOFA+ 1.3.x).
        payload = ""
        for name, val in lines:
            payload += f"{vofa_channel_name(name)}: {val:.6f}\n"
        payload += "\n"  # time advance
        self._udp_sock.sendto(payload.encode("utf-8"), (self.vofa_host, dest_port))

    def _emit_firewater_header_csv(
        self, lines: List[Tuple[str, float]], frame_type: int, dest_port: int
    ) -> None:
        # Attempt 3: declare names once per port/stream, then values-only rows.
        dest = (self.vofa_host, dest_port)
        names = [vofa_channel_name(n) for n, _ in lines]
        header_ok = self._vofa_header_sent_a if frame_type == 0x01 else self._vofa_header_sent_b
        if not header_ok:
            hdr = "!" + ",".join(names) + "\n"
            self._udp_sock.sendto(hdr.encode("utf-8"), dest)
            if frame_type == 0x01:
                self._vofa_header_sent_a = True
            else:
                self._vofa_header_sent_b = True
        body = ",".join(f"{val:.6f}" for _, val in lines) + "\n"
        self._udp_sock.sendto(body.encode("utf-8"), dest)

    def _emit_justfloat(self, lines: List[Tuple[str, float]], dest_port: int) -> None:
        """VOFA+ JustFloat: little-endian float32 payload + frame tail bytes."""
        vals = [float(v) for _, v in lines]
        n = len(vals)
        payload = struct.pack("<" + "f" * n, *vals) + b"\x00\x00\x80\x7f"
        self._udp_sock.sendto(payload, (self.vofa_host, dest_port))

    def _unpack_frame_a(self, max_num_basis: int, payload: bytes) -> List[Tuple[str, float]]:
        # JUSTFLOAT CHANNEL MAP - Frame A (100Hz) -> VOFA UDP vofa_port_a (default 1347)
        # I0  = mrac_pitch_e
        # I1  = mrac_pitch_u_ad
        # I2  = mrac_roll_e
        # I3  = mrac_roll_u_ad
        # I4  = mrac_yaw_e
        # I5  = mrac_yaw_u_ad
        # I6  = mrac_z_e
        # I7  = mrac_z_u_ad
        # I8  = status_arm
        # I9  = status_flymode
        # I10 = status_sbus_lost  (firmware field is `sbus_lost`; 1 = LOST)
        # I11 = status_twc_execute
        # I12 = status_twc_arrived
        #
        # Payload layout (LEN=38):
        #   float pitch.e
        #   float pitch.u_ad
        #   float roll.e
        #   float roll.u_ad
        #   float yaw.e
        #   float yaw.u_ad
        #   float z.e          (uses mrac_state.z_rate.e)
        #   float z.u_ad       (uses mrac_state.z_rate.u_ad)
        #   uint8 status.arm
        #   uint8 status.flymode
        #   uint8 sbus_lost      -> emitted as "status.sbus_lost" (1 = LOST)
        #   uint8 status.twc_execute
        #   uint8 status.twc_arrived
        #   uint8 rc_authority   <- 1=PC authority, 0=RC (added in v2)
        #   uint8 of_hold        <- 1=OF position-hold, 0=angle mode (added in v13; 41-byte payload only)
        #   uint8 estimator_ready<- 1=estimator converged/armable (added in v13; 41-byte payload only)
        #   uint8 proto_version  <- GS_PROTO_VERSION
        #
        # v10 firmware emits a 39-byte payload (no of_hold / estimator_ready); v13
        # emits 41. Accept both so the bridge parses either firmware build.
        of_hold_u8 = 0
        estimator_ready_u8 = 0
        motor_idle_enabled_u8 = 0
        if len(payload) == 39:
            fmt = "<8fBBBBBBB"
            (
                p_e, p_u, r_e, r_u, y_e, y_u, z_e, z_u,
                arm_u8, flymode_u8, sbus_lost_u8, twc_exec_u8, twc_arr_u8,
                rc_authority_u8, proto_ver_u8,
            ) = struct.unpack(fmt, payload)
        elif len(payload) == 41:
            fmt = "<8fBBBBBBBBB"
            (
                p_e, p_u, r_e, r_u, y_e, y_u, z_e, z_u,
                arm_u8, flymode_u8, sbus_lost_u8, twc_exec_u8, twc_arr_u8,
                rc_authority_u8, of_hold_u8, estimator_ready_u8, proto_ver_u8,
            ) = struct.unpack(fmt, payload)
        elif len(payload) == 42:
            fmt = "<8fBBBBBBBBBB"
            (
                p_e, p_u, r_e, r_u, y_e, y_u, z_e, z_u,
                arm_u8, flymode_u8, sbus_lost_u8, twc_exec_u8, twc_arr_u8,
                rc_authority_u8, of_hold_u8, estimator_ready_u8, motor_idle_enabled_u8, proto_ver_u8,
            ) = struct.unpack(fmt, payload)
        else:
            return []
        # Throttle the warning so a sustained mismatch does not flood the log (1 Hz max).
        if proto_ver_u8 != GS_PROTO_VERSION:
            now = time.monotonic()
            last = getattr(self, "_last_proto_warn_t", 0.0)
            if now - last >= 1.0:
                self._last_proto_warn_t = now
                if proto_ver_u8 < GS_PROTO_VERSION:
                    print(
                        f"WARNING: firmware proto_version={proto_ver_u8} < host {GS_PROTO_VERSION}. "
                        f"Decoding fields common to both. Some v{GS_PROTO_VERSION}-only fields will be 0. "
                        f"Reflash firmware to enable full telemetry.",
                        flush=True,
                    )
                else:
                    print(
                        f"WARNING: firmware proto_version={proto_ver_u8} > host {GS_PROTO_VERSION}. "
                        f"Host may be missing newer fields. Update serial_bridge.py.",
                        flush=True,
                    )

        # Update GUI state for ARM / flymode and MRAC basis hiding.
        with self._state_lock:
            self._last_max_num_basis = int(max_num_basis)
            self._last_arm = float(arm_u8)
            self._last_flymode = float(flymode_u8)
            self._last_sbus_lost = float(sbus_lost_u8)
            self._last_rc_authority = float(rc_authority_u8)

        return [
            ("mrac.pitch.e", float(p_e)),
            ("mrac.pitch.u_ad", float(p_u)),
            ("mrac.roll.e", float(r_e)),
            ("mrac.roll.u_ad", float(r_u)),
            ("mrac.yaw.e", float(y_e)),
            ("mrac.yaw.u_ad", float(y_u)),
            ("mrac.z.e", float(z_e)),
            ("mrac.z.u_ad", float(z_u)),
            ("status.arm", float(arm_u8)),
            ("status.flymode", float(flymode_u8)),
            # Same key the WiFi bridge and schema_registry emit; 1 = LOST.
            ("status.sbus_lost", float(sbus_lost_u8)),
            ("status.twc_execute", float(twc_exec_u8)),
            ("status.twc_arrived", float(twc_arr_u8)),
            ("status.rc_authority", float(rc_authority_u8)),
            ("status.of_hold", float(of_hold_u8)),
            ("status.estimator_ready", float(estimator_ready_u8)),
            ("status.motor_idle", float(motor_idle_enabled_u8)),
        ]

    def _unpack_frame_b(self, max_num_basis: int, payload: bytes) -> List[Tuple[str, float]]:
        # JUSTFLOAT CHANNEL MAP - Frame B (20Hz) -> VOFA UDP vofa_port_b (default 1348)
        # Float block layout (mirrors TASK/send_data.c Frame B emit):
        #   MRAC: 3 axes * (max_num_basis + 2) = 3N+6 floats  (pitch/roll/yaw only; z_rate excluded from MRAC)
        #   PID:  12 loops * 3 = 36 floats  (pitch/roll/yaw/gyrox/gyroy/gyroz/z_rate/locx/locy/z_pos/locxs/locys)
        #   total_floats = 3N+42
        # Path tail: 26 B (v3, adds vbat) or 28 B (v13, adds of_hold + estimator_ready).
        # I0  = mrac_pitch_theta_0
        # I1  = mrac_pitch_theta_1
        # I2  = mrac_pitch_theta_2
        # I3  = mrac_pitch_theta_3
        # I4  = mrac_pitch_theta_4
        # I5  = mrac_pitch_theta_5
        # I6  = mrac_pitch_u_nom
        # I7  = mrac_pitch_xm
        # I8  = mrac_roll_theta_0
        # I9  = mrac_roll_theta_1
        # I10 = mrac_roll_theta_2
        # I11 = mrac_roll_theta_3
        # I12 = mrac_roll_theta_4
        # I13 = mrac_roll_theta_5
        # I14 = mrac_roll_u_nom
        # I15 = mrac_roll_xm
        # I16 = mrac_yaw_theta_0
        # I17 = mrac_yaw_theta_1
        # I18 = mrac_yaw_theta_2
        # I19 = mrac_yaw_theta_3
        # I20 = mrac_yaw_theta_4
        # I21 = mrac_yaw_theta_5
        # I22 = mrac_yaw_u_nom
        # I23 = mrac_yaw_xm
        # I24 = mrac_z_theta_0
        # I25 = mrac_z_theta_1
        # I26 = mrac_z_theta_2
        # I27 = mrac_z_theta_3
        # I28 = mrac_z_theta_4
        # I29 = mrac_z_theta_5
        # I30 = mrac_z_u_nom
        # I31 = mrac_z_xm
        # I32 = pid_pitch_FB
        # I33 = pid_pitch_Des
        # I34 = pid_pitch_U
        # I35 = pid_roll_FB
        # I36 = pid_roll_Des
        # I37 = pid_roll_U
        # I38 = pid_yaw_FB
        # I39 = pid_yaw_Des
        # I40 = pid_yaw_U
        # I41 = pid_gyrox_FB
        # I42 = pid_gyrox_Des
        # I43 = pid_gyrox_U
        # I44 = pid_gyroy_FB
        # I45 = pid_gyroy_Des
        # I46 = pid_gyroy_U
        # I47 = pid_gyroz_FB
        # I48 = pid_gyroz_Des
        # I49 = pid_gyroz_U
        # I50 = pid_z_rate_FB
        # I51 = pid_z_rate_Des
        # I52 = pid_z_rate_U
        # I53 = pid_locx_FB
        # I54 = pid_locx_Des
        # I55 = pid_locx_U
        # I56 = pid_locy_FB
        # I57 = pid_locy_Des
        # I58 = pid_locy_U
        # I59..I67 = pid_z_pos (FB,Des,U), pid_locxs (FB,Des,U), pid_locys (FB,Des,U)
        #
        # Payload layout is floats only.
        # MRAC axes: pitch, roll, yaw, z_rate (mapped to axis name 'z')
        #   theta[0..MAX_NUM_BASIS-1], u_nom, xm  => (max_num_basis + 2) floats per axis
        # PID loops (12): + z_pos, locxs, locys (optical flow velocity + altitude)
        #   FB, Des, U => 3 floats per loop
        # Matches TASK/send_data.c: 4*(MAX_NUM_BASIS+2)+36 = 4N+44 floats.
        total_floats = 4 * (max_num_basis + 2) + 36
        main_len = total_floats * 4
        # Path tail: v3 = 26 bytes (adds real_voltage f), v2 = 22 bytes (no vbat).
        # Accept both so plots keep working before the firmware is reflashed to v3.
        # tail_len: 26 (v3, adds vbat) or 30 (v13/v14 unknown extra = firmware-specific).
        tail_len = len(payload) - main_len
        has_vbat = tail_len == 26
        if tail_len not in (26, 30):
            return []

        fmt = "<" + ("f" * total_floats)
        vals = struct.unpack(fmt, payload[:main_len])
        tail = payload[main_len : main_len + tail_len]
        if tail_len == 30:
            # v13/v14 extended tail: v3 (26 B) + of_hold (u8) + estimator_ready (u8) +
            # 2 trailing bytes (status padding / future flags).
            # Format: apm u8 | 5f path states | twc_arr u8 | vbat f32 |
            #         of_hold u8 | est_ready u8 | flag0 u8 | flag1 u8
            (apm_u8, twc_tx, twc_ty, twc_tz, sin_te, circ_th, twc_arr_u8, vbat,
             of_hold_u8, estimator_ready_u8, _flag0, _flag1) = struct.unpack(
                "<BfffffBfBBBB", tail)
            extra0 = float(_flag0)
            extra1 = float(_flag1)
        elif has_vbat:
            (apm_u8, twc_tx, twc_ty, twc_tz, sin_te, circ_th, twc_arr_u8, vbat) = struct.unpack("<BfffffBf", tail)
            extra0 = extra1 = 0.0
            of_hold_u8 = 0
            estimator_ready_u8 = 0
        else:
            (apm_u8, twc_tx, twc_ty, twc_tz, sin_te, circ_th, twc_arr_u8) = struct.unpack("<BfffffB", tail)
            vbat = None
            extra0 = extra1 = 0.0
            of_hold_u8 = 0
            estimator_ready_u8 = 0

        # Update GUI state for MRAC basis hiding.
        with self._state_lock:
            self._last_max_num_basis = int(max_num_basis)

        out: List[Tuple[str, float]] = []
        idx = 0

        axes = ["pitch", "roll", "yaw", "z"]
        for axis in axes:
            for b in range(max_num_basis):
                out.append((f"mrac.{axis}.theta_{b}", float(vals[idx])))
                idx += 1
            out.append((f"mrac.{axis}.u_nom", float(vals[idx])))
            idx += 1
            out.append((f"mrac.{axis}.xm", float(vals[idx])))
            idx += 1

        pid_loops = [
            "pitch",
            "roll",
            "yaw",
            "gyrox",
            "gyroy",
            "gyroz",
            "z_rate",
            "locx",
            "locy",
            "z_pos",
            "locxs",
            "locys",
        ]
        for loop in pid_loops:
            out.append((f"pid.{loop}.FB", float(vals[idx])))
            idx += 1
            out.append((f"pid.{loop}.Des", float(vals[idx])))
            idx += 1
            out.append((f"pid.{loop}.U", float(vals[idx])))
            idx += 1

        out.append(("path.active_path_mode", float(apm_u8)))
        out.append(("path.twc_target_x", float(twc_tx)))
        out.append(("path.twc_target_y", float(twc_ty)))
        out.append(("path.twc_target_z", float(twc_tz)))
        out.append(("path.sinusoid_t_elapsed", float(sin_te)))
        out.append(("path.circle_theta", float(circ_th)))
        out.append(("path.twc_arrived", float(twc_arr_u8)))
        if vbat is not None:
            out.append(("status.vbat", float(vbat)))
        out.append(("status.of_hold", float(of_hold_u8)))
        out.append(("status.estimator_ready", float(estimator_ready_u8)))

        return out

    def _unpack_frame_c(self, payload: bytes) -> List[Tuple[str, float]]:
        # FRAME 0x06 — attitude / body-rate / position, emitted back-to-back with Frame A.
        # Fixed 46-byte layout (TASK/send_data.c Frame C block):
        #   rol, pit, yaw (f32, deg) | gyro_x, gyro_y, gyro_z (f32, rad/s)
        #   | earth_x, earth_y (f32, m) | altitude (f32, m)
        #   | rpm[0..3] (u16 LE) | seq (u16 LE)
        # Unlike A/B this frame carries a CRC16 (see _crc16_ccitt), handled by the RX loop.
        if len(payload) != 46:
            return []
        (rol, pit, yaw, gx, gy, gz, ex, ey, alt) = struct.unpack_from("<9f", payload, 0)
        (r0, r1, r2, r3, seq) = struct.unpack_from("<5H", payload, 36)
        return [
            ("c.rol", float(rol)),
            ("c.pit", float(pit)),
            ("c.yaw", float(yaw)),
            ("c.gyro_x", float(gx)),
            ("c.gyro_y", float(gy)),
            ("c.gyro_z", float(gz)),
            ("c.earth_x", float(ex)),
            ("c.earth_y", float(ey)),
            ("c.altitude", float(alt)),
            ("c.rpm0", float(r0)),
            ("c.rpm1", float(r1)),
            ("c.rpm2", float(r2)),
            ("c.rpm3", float(r3)),
            ("c.seq", float(seq)),
        ]

    def _unpack_frame_id(self, payload: bytes) -> List[Tuple[str, float]]:
        # FRAME 0x03 — high-rate (200 Hz) single-axis system-ID stream. Fixed 36-byte layout:
        #   u32 sample_counter | u8 axis_id | {r, x, u_nom, u_ad, xm} float (EXCITED AXIS ONLY)
        #   | f sysid_dither | f real_voltage | u8 ARM | u8 FlyMode | u8 SysID FSM state
        # Only the excited axis is logged (axis_id: 0 pitch,1 roll,2 yaw,3 z) so the frame is small
        # enough to stream at 200 Hz. sysid_dither = raw excitation (exogenous instrument) for
        # unbiased closed-loop IV ID. Keys are emitted as id.<axisname>.* so downstream analysis
        # (which only uses the excited axis) is unchanged.
        if len(payload) != 36:
            return []
        out: List[Tuple[str, float]] = []
        counter = struct.unpack_from("<I", payload, 0)[0]
        out.append(("id.sample_counter", float(counter)))
        axes = ("pitch", "roll", "yaw", "z")
        fields = ("r", "x", "u_nom", "u_ad", "xm")
        axis_id = payload[4]
        ax = axes[axis_id] if axis_id < len(axes) else f"axis{axis_id}"
        vals = struct.unpack_from("<5f", payload, 5)
        for fld, v in zip(fields, vals):
            out.append((f"id.{ax}.{fld}", float(v)))
        (dither, vbat) = struct.unpack_from("<2f", payload, 25)
        arm = payload[33]
        mode = payload[34]
        sysid_state = payload[35]
        out.append(("id.axis", float(axis_id)))
        out.append(("id.dither", float(dither)))
        out.append(("id.vbat", float(vbat)))
        out.append(("id.arm", float(arm)))
        out.append(("id.mode", float(mode)))
        out.append(("id.sysid_state", float(sysid_state)))
        return out

    def _unpack_frame_bench(self, payload: bytes) -> List[Tuple[str, float]]:
        # FRAME 0x04 — motor bench-test stream (~100 Hz, replaces A/B while active).
        # Dual-length backward compat (mirror of _unpack_frame_b v2/v3 pattern):
        #   v7 = 12 B (no RPM): u32 sample_counter | u8 motor_id | u16 commanded_ccr |
        #                         f real_voltage | u8 active
        #   v8 = 20 B (adds 4x u16 RPM): v7 bytes + u16 rpm0..3 little-endian
        # v7 fills rpm=(0,0,0,0); v8 parses all four channels. Thrust is read off an
        # external scale by hand; this frame carries the operating point so the
        # dashboard logs each manual thrust point against a fresh pack voltage and
        # (v8) measured ω. See docs/bench_characterization.md and ADR-0010.
        if len(payload) not in (12, 20):
            return []
        counter = struct.unpack_from("<I", payload, 0)[0]
        motor_id = payload[4]
        ccr = struct.unpack_from("<H", payload, 5)[0]
        vbat = struct.unpack_from("<f", payload, 7)[0]
        active = payload[11]
        if len(payload) >= 20:
            r0, r1, r2, r3 = struct.unpack_from("<4H", payload, 12)
            rpm_tuple: Tuple[int, int, int, int] = (int(r0), int(r1), int(r2), int(r3))
        else:
            rpm_tuple = (0, 0, 0, 0)
        return [
            ("bench.sample_counter", float(counter)),
            ("bench.motor_id", float(motor_id)),
            ("bench.ccr", float(ccr)),
            ("bench.vbat", float(vbat)),
            ("bench.active", float(active)),
            ("bench.rpm", float(max(rpm_tuple))),
            ("bench.rpm1", float(rpm_tuple[0])),
            ("bench.rpm2", float(rpm_tuple[1])),
            ("bench.rpm3", float(rpm_tuple[2])),
            ("bench.rpm4", float(rpm_tuple[3])),
        ]

    def _unpack_frame_of(self, payload: bytes) -> List[Tuple[str, float]]:
        # FRAME 0x05 — OF calibration/fusion raw stream (200 Hz, replaces A/B while active).
        # v14 layout (GS_PROTO_VERSION=14, TASK/send_data.c):
        #   39 B base: u16 sample_counter, s16 of2_dx_fix/dy_fix/dx/dy, s16 acc_x/y,
        #              s16 lin_acc_x/y, s16 yaw/pit/rol (0.01 deg), s16 bias_x/y (0.01 raw),
        #              u16 alt_cm, f earth_x/y, u8 quality
        #   +16 B always-on (ADR-0011): s16 acc_bias[3] (mg), s16 gyro_bias[3] (1e-4 rad/s),
        #              u16 cal_health, u8 bias_mode (0=FIXED/1=EMA/2=EKF), u8 bias_ema_freeze
        #   +20 B EKF (EKF_TELEM_ENABLED=1): s16 v_body[3] (mm/s), s16 P_diag[3] (1e-3),
        #              s16 NIS (1e-3), s16 K_last[3] (1e-3)
        # Total: 55 B always-on; 75 B with EKF_TELEM_ENABLED=1
        if len(payload) not in (39, 55, 75):
            return []
        (
            counter,
            of2_dx_fix, of2_dy_fix,
            of2_dx, of2_dy,
            acc_x, acc_y,
            lin_acc_x, lin_acc_y,
            yaw_c, pit_c, rol_c,
            bias_x_c, bias_y_c,
            alt_cm,
        ) = struct.unpack_from("<H13hH", payload, 0)
        (earth_x, earth_y) = struct.unpack_from("<2f", payload, 30)
        of_quality = payload[38]
        lines: List[Tuple[str, float]] = [
            ("of.sample_counter", float(counter)),
            ("of.of2_dx_fix", float(of2_dx_fix)),
            ("of.of2_dy_fix", float(of2_dy_fix)),
            ("of.of2_dx", float(of2_dx)),
            ("of.of2_dy", float(of2_dy)),
            ("of.acc_x_mg", float(acc_x)),
            ("of.acc_y_mg", float(acc_y)),
            ("of.lin_acc_x_mg", float(lin_acc_x)),
            ("of.lin_acc_y_mg", float(lin_acc_y)),
            ("of.yaw", float(yaw_c) * 0.01),
            ("of.pit", float(pit_c) * 0.01),
            ("of.rol", float(rol_c) * 0.01),
            ("of.bias_x", float(bias_x_c) * 0.01),
            ("of.bias_y", float(bias_y_c) * 0.01),
            ("of.alt_cm", float(alt_cm)),
            ("of.earth_x", float(earth_x)),
            ("of.earth_y", float(earth_y)),
            ("of.quality", float(of_quality)),
        ]
        # v14 ADR-0011 always-on section (offset 39): acc_bias[3] + gyro_bias[3] + cal_health
        # + bias_mode + bias_ema_freeze = 16 bytes total
        if len(payload) >= 55:
            (ab0, ab1, ab2, gb0, gb1, gb2, cal_h, bias_mode, bias_ema_freeze) = struct.unpack_from(
                "<6hHBB", payload, 39
            )
            lines += [
                ("of.acc_bias_x_mg", float(ab0)),
                ("of.acc_bias_y_mg", float(ab1)),
                ("of.acc_bias_z_mg", float(ab2)),
                ("of.gyro_bias_x_1e4radps", float(gb0)),
                ("of.gyro_bias_y_1e4radps", float(gb1)),
                ("of.gyro_bias_z_1e4radps", float(gb2)),
                ("of.cal_health", float(cal_h)),
                ("of.bias_mode", float(bias_mode)),
                ("of.bias_ema_freeze", float(bias_ema_freeze)),
            ]
        # v14 EKF telemetry section (offset 55): v_body[3] + P_diag[3] + NIS + K_last[3] = 20 bytes
        if len(payload) >= 75:
            (vx, vy, vz, p0, p1, p2, nis, k0, k1, k2) = struct.unpack_from("<10h", payload, 55)
            lines += [
                ("of.ekf_vx_mmps", float(vx)),
                ("of.ekf_vy_mmps", float(vy)),
                ("of.ekf_vz_mmps", float(vz)),
                ("of.ekf_p0_1e3", float(p0)),
                ("of.ekf_p1_1e3", float(p1)),
                ("of.ekf_p2_1e3", float(p2)),
                ("of.ekf_nis_1e3", float(nis)),
                ("of.ekf_k0_1e3", float(k0)),
                ("of.ekf_k1_1e3", float(k1)),
                ("of.ekf_k2_1e3", float(k2)),
            ]
        return lines

    def _handle_frame(self, frame_type: int, max_num_basis: int, payload: bytes) -> None:
        if frame_type == 0x01:
            lines = self._unpack_frame_a(max_num_basis, payload)
        elif frame_type == 0x02:
            lines = self._unpack_frame_b(max_num_basis, payload)
        elif frame_type == 0x03:
            lines = self._unpack_frame_id(payload)
        elif frame_type == 0x04:
            lines = self._unpack_frame_bench(payload)
        elif frame_type == 0x05:
            lines = self._unpack_frame_of(payload)
        elif frame_type == 0x06:
            lines = self._unpack_frame_c(payload)
        else:
            return

        now = time.monotonic()

        if lines:
            mp: Dict[str, float] = {k: float(v) for k, v in lines}
            with self._telemetry_lock:
                if frame_type == 0x01:
                    self._last_telemetry_a = mp
                    self._last_frame_a_t = now
                elif frame_type == 0x03:
                    self._last_telemetry_id = mp
                elif frame_type == 0x04:
                    self._last_telemetry_bench = mp
                elif frame_type == 0x05:
                    self._last_telemetry_of = mp
                elif frame_type == 0x06:
                    self._last_telemetry_c = mp
                else:
                    self._last_telemetry_b = mp
                    self._last_frame_b_t = now
            self._maybe_debug_print_telemetry(frame_type, lines)
            self._emit_vofa_output(frame_type, lines)
            self._mirror_telemetry_udp()

    def _mirror_telemetry_udp(self) -> None:
        """Push latest Frame A/B dicts + max_num_basis to dashboard via UDP (JSON, one-way)."""
        try:
            with self._telemetry_lock:
                a = dict(self._last_telemetry_a)
                b = dict(self._last_telemetry_b)
                idf = dict(self._last_telemetry_id)
                bench = dict(self._last_telemetry_bench)
                of = dict(self._last_telemetry_of)
                c = dict(self._last_telemetry_c)
            with self._state_lock:
                mb = int(self._last_max_num_basis)
            payload = json.dumps(
                {"a": a, "b": b, "c": c, "id": idf, "bench": bench, "of": of, "max_num_basis": mb},
                separators=(",", ":"),
            ).encode("utf-8")
            if len(payload) > 65000:
                return
            self._mirror_sock.sendto(payload, (self._telemetry_mirror_host, self._telemetry_mirror_port))
        except Exception:
            pass

    def _maybe_debug_print_telemetry(self, frame_type: int, lines: List[Tuple[str, float]]) -> None:
        if not self._simulate:
            return
        now = time.monotonic()
        if now - self._last_telemetry_print_t < self._telemetry_print_interval_s:
            return
        self._last_telemetry_print_t = now

        sample = lines[:6]
        sample_str = ", ".join([f"{name}={val:.4g}" for name, val in sample])
        print(
            f"Decoded telemetry frame=0x{frame_type:02X} vars({len(lines)}) sample: {sample_str}",
            flush=True,
        )

    def _format_frame_bytes(self, frame: bytes) -> str:
        return "".join([f"[0x{b:02X}]" for b in frame])

    def _print_tx_frame(self, frame: bytes, cmd: Dict[str, Any]) -> None:
        # Frame layout:
        #   [0xCC][0xDD][CMD_ID][INDEX][VALUE float32 LE][CRC8]
        try:
            cmd_id = frame[2]
            index = frame[3]
            value_f = struct.unpack("<f", frame[4:8])[0]
        except Exception:
            cmd_id = int(cmd.get("cmd_id", 0)) & 0xFF
            index = int(cmd.get("index", 0)) & 0xFF
            value_f = float(cmd.get("value", 0.0))
        print(
            f"TX cmd_frame={self._format_frame_bytes(frame)} cmd_id=0x{cmd_id:02X} index={index} value={value_f:.6g}",
            flush=True,
        )

    def _cmd_udp_loop(self) -> None:
        """
        Dashboard control channel (localhost):
          - send literal `b"ping"` to receive `b"pong"`
          - send JSON: {"cmd_id": int, "index": int, "value": float}
        """
        if self._cmd_udp_sock is None:
            return

        while not self._stop_event.is_set():
            try:
                data, addr = self._cmd_udp_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            if data == b"ping":
                try:
                    self._cmd_udp_sock.sendto(b"pong", addr)
                except Exception:
                    pass
                continue

            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue

            # Normalize a few alternate keys.
            if "cmd_id" not in msg:
                if "CMD_ID" in msg:
                    msg["cmd_id"] = msg["CMD_ID"]
            if "index" not in msg:
                for k in ("idx", "INDEX"):
                    if k in msg:
                        msg["index"] = msg[k]
                        break
            if "value" not in msg:
                for k in ("val", "VALUE"):
                    if k in msg:
                        msg["value"] = msg[k]
                        break

            try:
                cmd_queue.put(msg)
            except Exception:
                continue

    def _parse_and_handle_datagram(self, data: bytes) -> None:
        """
                Parse one complete telemetry datagram from the UDP simulation path.

                Byte layout:
                    [0xAA][0xBB][frame_type][LEN_high][LEN_low][MAX_NUM_BASIS][payload][CRC8]

                Validation order:
                    1) Minimum frame size (7 bytes).
                    2) Sync bytes match self.SYNC_0/self.SYNC_1.
                    3) Frame-type specific payload length is valid.
                    4) Payload length bounds are within (0, 4096].
                    5) Full datagram size matches header + payload + CRC.
                    6) XOR CRC8 over [frame_type, LEN_high, LEN_low, MAX_NUM_BASIS, payload]
                         equals trailing CRC byte.

                If all checks pass, payload is forwarded to
                self._handle_frame(frame_type, max_num_basis, payload).
        """
        if len(data) < 7:
            return
        if data[0] != self.SYNC_0 or data[1] != self.SYNC_1:
            return
        frame_type = data[2]
        len_high = data[3]
        len_low = data[4]
        len_payload = (len_high << 8) | len_low
        max_num_basis = data[5]

        if frame_type == 0x01:
            # v10 = 39 B, v13 = 41 B (adds of_hold + estimator_ready), v15 = 42 B (adds motor_idle_enabled).
            if len_payload not in (39, 41, 42):
                return
        elif frame_type == 0x02:
            # Matches committed TASK/send_data.c (v10/v13): 4*(MAX_NUM_BASIS+2)+36 = 4N+44 floats.
            # Observed on live COM3: N=6 → 298 B = 68*4 + 26.
            total_floats = 4 * (max_num_basis + 2) + 36
            main_len = total_floats * 4
            # Accept v3 tail (26 B = 292 B total) and v13/v14 tail (30 B = 296 B total).
            if len_payload not in (main_len + 26, main_len + 30):
                return
        elif frame_type == 0x03:
            if len_payload != 36:
                return
        elif frame_type == 0x04:
            # v7 = 12 B (no RPM), v8 = 20 B (4x u16 RPM appended). Accept both
            # so the bridge still parses pre-reflash firmware.
            if len_payload not in (12, 20):
                return
        elif frame_type == 0x05:
            # v8-v13 = 39 B; v14 = 53 B (acc_bias, gyro_bias, cal_health); v14+EKF = 73 B.
            if len_payload not in (39, 53, 73):
                return
        elif frame_type == 0x06:
            # Frame C: 46 B live layout. Bounds-only check so a layout bump degrades to
            # "decoder returns []" rather than a hard resync.
            if not (0 < len_payload <= 256):
                return
        else:
            return

        if len_payload <= 0 or len_payload > 4096:
            return

        # Frame C carries a 2-byte CRC16-CCITT; every other frame a 1-byte XOR-CRC8.
        crc_len = 2 if frame_type == 0x06 else 1
        expected_total = 6 + len_payload + crc_len
        if len(data) != expected_total:
            return

        payload = data[6 : 6 + len_payload]
        if frame_type == 0x06:
            recv_crc = int.from_bytes(data[6 + len_payload : 8 + len_payload], "big")
            calc_crc = _crc16_ccitt([frame_type, len_high, len_low, max_num_basis, *payload])
        else:
            recv_crc = data[6 + len_payload]
            calc_crc = _xor_crc8([frame_type, len_high, len_low, max_num_basis, *payload])
        if calc_crc != recv_crc:
            return

        self._handle_frame(frame_type, max_num_basis, payload)

    def _rx_loop_udp(self) -> None:
        """Receive full frames from frame_simulator (UDP) instead of UART."""
        if self._simulate_sock is None:
            return
        while not self._stop_event.is_set():
            try:
                data, _addr = self._simulate_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            self._parse_and_handle_datagram(data)

    def _rx_loop(self) -> None:
        """
        RX loop:
          - sync on [0xAA][0xBB]
          - read frame_type, LEN_high, LEN_low, MAX_NUM_BASIS
          - read payload ((LEN_high<<8)|LEN_low bytes)
          - read CRC8 (1 byte)
          - validate XOR checksum, drop corrupt frames silently
          - forward decoded variables via FireWater over UDP
        """
        if self._serial is None:
            return

        sync_seen = False
        while not self._stop_event.is_set():
            b = self._serial.read(1)
            if not b:
                continue
            byte = b[0]

            if not sync_seen:
                if byte == self.SYNC_0:
                    sync_seen = True
                continue

            # sync_seen means we already saw 0xAA.
            if byte != self.SYNC_1:
                sync_seen = byte == self.SYNC_0
                continue

            header = self._read_exact(4)
            if header is None:
                break

            frame_type = header[0]
            len_high = header[1]
            len_low = header[2]
            max_num_basis = header[3]
            len_payload = (len_high << 8) | len_low

            # Basic header sanity checks to avoid blocking on corrupted LEN values.
            if frame_type == 0x01:
                # v10 = 39 B, v13 = 41 B (adds of_hold + estimator_ready), v15 = 42 B (adds motor_idle_enabled).
                if len_payload not in (39, 41, 42):
                    sync_seen = False
                    continue
            elif frame_type == 0x02:
                # Matches committed TASK/send_data.c (v10/v13): 4*(MAX_NUM_BASIS+2)+36 = 4N+44 floats.
                # Observed on live COM3: N=6 → 298 B = 68*4 + 26.
                total_floats = 4 * (max_num_basis + 2) + 36
                main_len = total_floats * 4
                # Accept v3 tail (26 B = 292 B total) and v13/v14 tail (30 B = 296 B total).
                if len_payload not in (main_len + 26, main_len + 30):
                    sync_seen = False
                    continue
            elif frame_type == 0x03:
                if len_payload != 36:
                    sync_seen = False
                    continue
            elif frame_type == 0x04:
                # v7 = 12 B (no RPM), v8 = 20 B (4x u16 RPM appended). Accept both
                # so the bridge still parses pre-reflash firmware.
                if len_payload not in (12, 20):
                    sync_seen = False
                    continue
            elif frame_type == 0x05:
                if len_payload not in (39, 53, 73):
                    sync_seen = False
                    continue
            elif frame_type == 0x06:
                # Frame C: 46 B live layout. Bounds-only check so a layout bump degrades to
                # "decoder returns []" rather than a hard resync.
                if not (0 < len_payload <= 256):
                    sync_seen = False
                    continue
            else:
                sync_seen = False
                continue

            if len_payload <= 0 or len_payload > 4096:
                sync_seen = False
                continue

            payload = self._read_exact(len_payload)
            if payload is None:
                break

            # Frame C carries a 2-byte CRC16-CCITT; every other frame a 1-byte XOR-CRC8.
            crc_b = self._read_exact(2 if frame_type == 0x06 else 1)
            if crc_b is None:
                break

            # CRC covers bytes after sync: type, LEN_hi, LEN_lo, MAX_NUM_BASIS, payload
            if frame_type == 0x06:
                recv_crc = int.from_bytes(crc_b, "big")
                calc_crc = _crc16_ccitt([frame_type, len_high, len_low, max_num_basis, *payload])
            else:
                recv_crc = crc_b[0]
                calc_crc = _xor_crc8([frame_type, len_high, len_low, max_num_basis, *payload])
            if calc_crc != recv_crc:
                # Drop corrupt frames silently.
                sync_seen = False
                continue

            self._handle_frame(frame_type, max_num_basis, payload)
            sync_seen = False

    def _cmd_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                cmd = cmd_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if self._stop_event.is_set():
                return
            if not isinstance(cmd, dict):
                continue

            try:
                frame = self._pack_command_frame(cmd)
            except Exception:
                # Never let malformed commands kill the background process.
                continue

            try:
                if self._serial is None:
                    if self._simulate:
                        self._print_tx_frame(frame, cmd)
                    continue
                with self._write_lock:
                    self._serial.write(frame)
            except Exception:
                # Best-effort: if serial is gone, exit loop.
                continue

    def _pack_command_frame(self, cmd: Dict[str, Any]) -> bytes:
        # Accept a few key aliases to be forgiving.
        cmd_id = cmd.get("cmd_id", cmd.get("id", cmd.get("CMD_ID")))
        index = cmd.get("index", cmd.get("idx", cmd.get("INDEX")))
        value = cmd.get("value", cmd.get("val", cmd.get("VALUE")))

        if cmd_id is None or index is None or value is None:
            raise ValueError("cmd dict must include cmd_id/index/value")

        cmd_id_u8 = int(cmd_id) & 0xFF
        index_u8 = int(index) & 0xFF
        value_f = float(value)

        value_bytes = struct.pack("<f", value_f)

        # [0xCC] [0xDD] [CMD_ID] [INDEX] [VALUE float32 LE] [CRC8]
        header_and_payload = bytes([self.CMD_0, self.CMD_1, cmd_id_u8, index_u8]) + value_bytes
        # CRC in C is XOR over bytes 2..7 (cmd_id, index, and 4 bytes of value).
        crc = _xor_crc8(header_and_payload[2:])
        return header_and_payload + bytes([crc])


def test_com_ports_listen(
    ports: Optional[List[str]] = None,
    baud_rate: Optional[int] = None,
    wait_s: float = 3.0,
) -> Dict[str, int]:
    """
    Open each serial port briefly, wait for incoming bytes, report counts.
    Used to find which COM port is the wireless UART debugger / vehicle link.
    """
    cfg = load_config()
    br = int(cfg.get("baud_rate", 115200)) if baud_rate is None else int(baud_rate)
    try_list = ports if ports is not None else ["COM6", "COM4", "COM5"]
    out: Dict[str, int] = {}
    if serial is None:  # pragma: no cover
        print("pyserial not installed; cannot test COM ports.")
        return out
    for port in try_list:
        n = 0
        try:
            ser = serial.Serial(port=port, baudrate=br, timeout=0.05)  # type: ignore[attr-defined]
            try:
                t_end = time.monotonic() + float(wait_s)
                while time.monotonic() < t_end:
                    chunk = ser.read(4096)
                    if chunk:
                        n += len(chunk)
                print(f"{port}: received {n} bytes")
            finally:
                ser.close()
        except Exception as e:
            print(f"{port}: open/read failed ({e})")
            n = -1
        out[port] = n
    return out


def start_bridge_in_background(
    *,
    simulate: bool = False,
    simulate_udp_port: Optional[int] = None,
) -> SerialBridge:
    """
    Convenience helper for quick manual runs:
      bridge = start_bridge_in_background()
      ...
      bridge.stop()
    """
    bridge = SerialBridge(simulate=simulate, simulate_udp_port=simulate_udp_port)
    bridge.start()
    return bridge


def _main_cli() -> None:
    p = argparse.ArgumentParser(description="UART4 telemetry bridge → VOFA+ FireWater (UDP).")
    p.add_argument(
        "--simulate",
        action="store_true",
        help="Read synthetic frames from UDP (frame_simulator.py) instead of serial.",
    )
    p.add_argument(
        "--simulate-port",
        type=int,
        default=None,
        metavar="PORT",
        help="UDP port to bind for simulate mode (default: config simulate_udp_port or 50007).",
    )
    p.add_argument(
        "--test-com",
        action="store_true",
        help="Try COM6/COM4/COM5 at config baud for 3s each; print byte counts (no bridge).",
    )
    p.add_argument(
        "--scan-com",
        action="store_true",
        help="Probe each COM port in com_scan and report (desc, bytes, error); exit 0 on first "
        "telemetry port, 1 if none stream, 2 on usage/config error. Honors com_probe_timeout_s.",
    )
    args = p.parse_args()
    if args.test_com:
        test_com_ports_listen()
        return
    if args.scan_com:
        rows = scan_com_ports()
        for port, info in rows.items():
            tag = info["error"] or ("telemetry" if info["bytes"] > 0 else "idle")
            print(f"{port:<6} {info['desc']:<40} {info['bytes']:>7} B  [{tag}]")
        # Exit 0 if at least one candidate is live; non-zero so a launcher can react.
        any_live = any(r.get("bytes", 0) > 0 for r in rows.values())
        return 0 if any_live else 1
    port = args.simulate_port
    b = start_bridge_in_background(simulate=args.simulate, simulate_udp_port=port)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        b.stop()


if __name__ == "__main__":  # pragma: no cover
    _main_cli()

