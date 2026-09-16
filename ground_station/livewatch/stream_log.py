"""Subscribe to a set of variables by NAME and log them to CSV.

With no variable argument it logs the **default frame** — the Markdown table in
``log_frames.md`` next to this file. Edit that table to change what gets logged;
nothing here or in the firmware needs to change.

    python -m ground_station.livewatch.stream_log --seconds 30 --out logs/run.csv

Override it per-run with explicit slots, or with one rate for everything:

    ... --group "40:mrac_state.roll.Theta:6" --group "5:imu_data.rol:3"
    ... --symbol mrac_state.roll.Theta:6 --symbol imu_data.rol:3 --rate 20

Names are resolved from ``OBJ/JX_FLY.axf`` (DWARF), so you name variables the way
you think about them and never touch an address. ``:6`` means "6 consecutive
elements" -- one range tuple, whatever its width.

Two ports, on purpose. The subscribe request always goes out the **control**
port (UART5, the CMSIS-DAP VCP) because that is the only port the firmware
accepts commands on. The data comes back on the **data** port, which
``--transport`` selects.

**This is serial, not the debugger.** COM6 is the CMSIS-DAP dongle's *virtual
COM port*, i.e. UART5's wire -- the flight controller actively pushes frames
down it. Nothing here halts the core, reads RAM over SWD, or needs pyOCD. The
firmware copies bytes out of its own memory and never writes back.

``--transport usart3`` routes the data to the MicoAir WiFi module, which
is where it belongs for real flights: measured 258 Hz at ~22% wire utilisation,
uplink ~1100 Hz ceiling, 0% loss. That path is **UDP, not a COM port** -- the
module forwards USART3 bytes as datagrams to UDP 14550, so the data port defaults
to ``udp:14550`` and needs no driver.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from .stream import (
    MAX_SLOTS, MultiStreamDecoder, SEND_TASK_MEASURED_HZ, StreamDecoder,
    StreamRange, TRANSPORT_UART5, TRANSPORT_USART3, build_stream_request,
    decode_schema, stream_bps,
)
from .symbols import SymbolResolver
from .transport import LiveTransportError, UdpDataPort, pop_frame
from .auto_split import auto_split_and_log

_TRANSPORTS = {"usart3": TRANSPORT_USART3, "uart5": TRANSPORT_UART5}
DEFAULT_FRAMES = Path(__file__).with_name("log_frames.md")
DEFAULT_USART3_UDP_PORT = 14550
_STRUCT_FMT = {(4, "float"): "f", (4, "int"): "i", (4, "uint"): "I",
               (2, "int"): "h", (2, "uint"): "H",
               (1, "int"): "b", (1, "uint"): "B"}



def _open_data(data_port: str, control, control_port: str):
    """Open the data path for whichever transport was chosen."""
    if data_port == control_port:
        return control
    if data_port.startswith("udp:"):
        return UdpDataPort(int(data_port.split(":", 1)[1]))
    import serial
    return serial.Serial(data_port, 115200, timeout=0.05)


def resolve_ranges(resolver: SymbolResolver, specs) -> list[StreamRange]:
    """Turn ``path`` or ``path:count`` strings into StreamRanges."""
    ranges = []
    for spec in specs:
        path, _, count_txt = spec.partition(":")
        count = int(count_txt) if count_txt else 1
        # `count > 1` may mean an array (resolve its [0]) or a scalar whose
        # neighbours are contiguous, e.g. imu_data.rol:3 to sweep rol/pit/yaw.
        # Try the array form first and fall back to the plain symbol.
        sym = None
        errors = []
        for probe in ([path + "[0]", path] if count > 1 else [path]):
            try:
                sym = resolver.resolve(probe)
                break
            except Exception as exc:
                errors.append("%s: %s" % (probe, exc))
        if sym is None:
            raise LiveTransportError(
                "stream-log: cannot resolve %r (%s)" % (path, "; ".join(errors)))
        fmt = _STRUCT_FMT.get((sym.size, _kind_of(sym)))
        if fmt is None:
            raise LiveTransportError(
                "stream-log: %s is %d B, not a 1/2/4-byte scalar" % (path, sym.size))
        ranges.append(StreamRange(sym.address, sym.size, count, path, fmt))
    return ranges


def _kind_of(sym) -> str:
    """Best-effort scalar class from the resolver's Symbol."""
    # Use Symbol.fmt (struct format char) to determine kind
    fmt = getattr(sym, "fmt", None)
    if fmt in ("f", "d"):  # float, double
        return "float"
    if fmt in ("B", "H", "I", "Q"):  # unsigned 1/2/4/8-byte
        return "uint"
    if fmt in ("b", "h", "i", "q"):  # signed 1/2/4/8-byte
        return "int"
    # Fallback: try legacy 'kind' attribute if it exists
    kind = getattr(sym, "kind", None) or ""
    text = str(kind).lower()
    if "float" in text or "double" in text:
        return "float"
    if "unsigned" in text or text.startswith("u") or "bool" in text:
        return "uint"
    return "int"


def columns_for(schema) -> list[str]:
    names = []
    for rng in schema.ranges:
        label = rng.name or "0x%08X" % rng.address
        if rng.count == 1:
            names.append(label)
        else:
            names.extend("%s[%d]" % (label, i) for i in range(rng.count))
    return names


def run(control_port, data_port, ranges, divider, transport, seconds, out_path,
        elf="OBJ/JX_FLY.axf", usart3_baud=921600, quiet=False, auto_split=False):
    if auto_split:
        return auto_split_and_log(
            control_port, data_port, ranges, divider, transport,
            seconds, out_path, elf, usart3_baud, quiet)

    import serial

    req = build_stream_request(ranges, divider, transport, usart3_baud)
    stop = build_stream_request([], 0, transport, usart3_baud)

    control = serial.Serial(control_port, 115200, timeout=0.05)
    data = _open_data(data_port, control, control_port)
    try:
        control.reset_input_buffer()
        control.write(req)
        control.flush()

        try:
            schema = _await_schema(control, ranges)
        except LiveTransportError:
            raise

        cols = columns_for(schema)
        if not quiet:
            print("subscribed: %d value(s), %d B/frame, %.1f Hz expected"
                  % (len(cols), schema.frame_bytes, schema.hz))
            print("data on %s -> %s" % (data_port, out_path))

        data.reset_input_buffer()
        decoder = StreamDecoder(schema)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rows = 0
        t0 = time.monotonic()
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["t_src_ms", "t_host_s", "seq"] + cols)
            while time.monotonic() - t0 < seconds:
                waiting = data.in_waiting
                if not waiting:
                    time.sleep(0.002)
                    continue
                for seq, t_ms, values in decoder.feed(data.read(waiting)):
                    flat = []
                    for rng in schema.ranges:
                        got = values[rng.name or "r%d" % len(flat)]
                        flat.extend(got if isinstance(got, list) else [got])
                    writer.writerow(
                        [t_ms, "%.4f" % (time.monotonic() - t0), seq] + flat)
                    rows += 1
        elapsed = time.monotonic() - t0
    finally:
        try:
            control.write(stop)
            control.flush()
        except Exception:
            pass
        control.close()
        if data is not control:
            data.close()

    return {
        "rows": rows,
        "seconds": elapsed,
        "hz": rows / elapsed if elapsed else 0.0,
        "dropped": decoder.dropped,
        "loss_pct": decoder.loss_pct,
        "malformed": decoder.crc_errors,
        "columns": cols,
        "path": str(out_path),
    }


def _await_schema(control, ranges, timeout=1.5):
    rx = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = pop_frame(rx)
        if frame is not None:
            frame_type, byte5, payload = frame
            if frame_type == 0x08:
                return decode_schema(byte5, payload, ranges)
            if frame_type == 0x7F:
                msg = payload.decode("utf-8", "replace").rstrip("\x00")
                raise LiveTransportError(
                    "stream-log: firmware rejected the subscription: %s" % msg)
            continue
        waiting = getattr(control, "in_waiting", 0)
        chunk = control.read(waiting or 1)
        if chunk:
            rx.extend(chunk)
    raise LiveTransportError("stream-log: no 0x08 schema reply from the firmware")


def parse_group(spec: str):
    """``"20:mrac_state.roll.Theta:6,imu_data.rol:3"`` -> ``(20.0, [specs])``.

    Rate first because that is the thing that differs between groups -- it reads
    as "at 20 Hz, log these".
    """
    rate_txt, _, rest = spec.partition(":")
    try:
        rate = float(rate_txt)
    except ValueError:
        raise LiveTransportError(
            "stream-log: --group must start with a rate, e.g. "
            "'20:mrac_state.roll.Theta:6' (got %r)" % spec)
    symbols = [s for s in rest.split(",") if s]
    if not symbols:
        raise LiveTransportError("stream-log: --group %r lists no symbols" % spec)
    if rate <= 0:
        raise LiveTransportError("stream-log: --group %r has a non-positive rate" % spec)
    return rate, symbols


def parse_frames_markdown(text: str):
    """A Markdown table of slots -> ``[(rate, [specs]), ...]`` ordered by slot.

    A frame row is any table row whose first cell is an integer; everything else
    in the document — prose, the budget table, header and separator rows — is
    ignored. That is what lets the file stay readable as documentation instead of
    degenerating into config with comments.
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        try:
            slot, rate = int(cells[0]), float(cells[1])
        except ValueError:
            continue
        if rate <= 0:
            raise LiveTransportError(
                "stream-log: slot %d has a non-positive rate %r" % (slot, cells[1]))
        symbols = [s.strip() for s in cells[2].split(",") if s.strip()]
        if not symbols:
            raise LiveTransportError("stream-log: slot %d lists no variables" % slot)
        rows.append((slot, rate, symbols))

    if not rows:
        raise LiveTransportError(
            "stream-log: no frame rows found — expected a Markdown table whose "
            "columns are | Slot | Rate (Hz) | Variables |")
    slots = sorted(r[0] for r in rows)
    if slots != list(range(len(rows))):
        raise LiveTransportError(
            "stream-log: slots must start at 0 and be contiguous, got %s" % slots)
    rows.sort()
    return [(rate, symbols) for _, rate, symbols in rows]


def load_frames(path=DEFAULT_FRAMES):
    path = Path(path)
    if not path.exists():
        raise LiveTransportError("stream-log: no frame file at %s" % path)
    return parse_frames_markdown(path.read_text(encoding="utf-8"))


def run_groups(control_port, data_port, groups, transport, seconds, out_path,
               elf="OBJ/JX_FLY.axf", usart3_baud=921600, quiet=False):
    """Subscribe several slots at different rates; one CSV per slot.

    Separate files because the slots tick at different rates -- interleaving
    them into one table would mean padding the slow columns, and a 2 Hz signal
    padded to 80 Hz reads as though it were sampled 40x more often than it was.
    """
    import serial

    resolver = SymbolResolver(elf)
    if len(groups) > MAX_SLOTS:
        raise LiveTransportError(
            "stream-log: %d groups but the firmware has %d slots"
            % (len(groups), MAX_SLOTS))

    plans, committed = [], 0
    for slot, (rate, specs) in enumerate(groups):
        ranges = resolve_ranges(resolver, specs)
        divider = max(1, min(255, round(SEND_TASK_MEASURED_HZ / rate)))
        request = build_stream_request(ranges, divider, transport, usart3_baud,
                                       slot, committed)
        total = sum(r.nbytes for r in ranges)
        committed += stream_bps(total, divider)
        plans.append((slot, ranges, divider, request))

    control = serial.Serial(control_port, 115200, timeout=0.05)
    data = _open_data(data_port, control, control_port)
    schemas, writers, handles, rows = [], {}, [], {}
    out_path = Path(out_path)
    try:
        control.reset_input_buffer()
        for slot, ranges, divider, request in plans:
            control.write(request)
            control.flush()
            schema = _await_schema(control, ranges)
            if schema.slot != slot:
                raise LiveTransportError(
                    "stream-log: asked for slot %d, firmware acknowledged %d"
                    % (slot, schema.slot))
            schemas.append(schema)
            if not quiet:
                print("slot %d: %2d value(s), %3d B/frame, %5.1f Hz -> %s"
                      % (slot, len(columns_for(schema)), schema.frame_bytes,
                         schema.hz, _slot_path(out_path, slot).name))

        decoder = MultiStreamDecoder(schemas)
        for schema in schemas:
            path = _slot_path(out_path, schema.slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = path.open("w", newline="", encoding="utf-8")
            handles.append(fh)
            writer = csv.writer(fh)
            writer.writerow(["t_src_ms", "t_host_s", "seq"] + columns_for(schema))
            writers[schema.slot] = (writer, schema)
            rows[schema.slot] = 0

        data.reset_input_buffer()
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            waiting = data.in_waiting
            if not waiting:
                time.sleep(0.002)
                continue
            for slot, seq, t_ms, values in decoder.feed(data.read(waiting)):
                writer, schema = writers[slot]
                flat = []
                for rng in schema.ranges:
                    got = values[rng.name or "r%d" % len(flat)]
                    flat.extend(got if isinstance(got, list) else [got])
                writer.writerow(
                    [t_ms, "%.4f" % (time.monotonic() - t0), seq] + flat)
                rows[slot] += 1
        elapsed = time.monotonic() - t0
    finally:
        for fh in handles:
            fh.close()
        for slot, _, _, _ in plans:
            try:
                control.write(build_stream_request([], 0, transport,
                                                   usart3_baud, slot))
                control.flush()
                # The UART5 side stages only ONE request at a time; back-to-back
                # stops collide and silently never happen (hit for real on
                # 2026-08-09: slots 1+2 kept streaming after a "clean" stop).
                # One Send_Task tick is ~12.5 ms; 250 ms leaves wide margin.
                time.sleep(0.25)
            except Exception:
                pass
        control.close()
        if data is not control:
            data.close()

    return [{
        "slot": schema.slot,
        "rows": rows[schema.slot],
        "hz": rows[schema.slot] / elapsed if elapsed else 0.0,
        "dropped": decoder.decoders[schema.slot].dropped,
        "loss_pct": decoder.decoders[schema.slot].loss_pct,
        "malformed": decoder.decoders[schema.slot].crc_errors,
        "path": str(_slot_path(out_path, schema.slot)),
    } for schema in schemas]


def _slot_path(out_path: Path, slot: int) -> Path:
    return out_path.with_name("%s.slot%d%s"
                              % (out_path.stem, slot, out_path.suffix or ".csv"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbol", action="append", metavar="PATH[:N]",
                    help="variable to log; repeat for more. N = consecutive elements")
    ap.add_argument("--group", action="append", metavar="RATE:SYM[:N][,SYM[:N]]",
                    help="a slot at its own rate, e.g. "
                         "'80:mrac_state.roll.Theta:6' — repeat for up to "
                         "%d slots. Writes one CSV per slot." % MAX_SLOTS)
    ap.add_argument("--rate", type=float, default=20.0,
                    help="desired Hz (rounded to a Send_Task divider at ~80.4 Hz)")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--out", default="logs/stream.csv")
    ap.add_argument("--transport", choices=sorted(_TRANSPORTS), default="usart3")
    ap.add_argument("--control-port", default="COM6", help="UART5 / CMSIS-DAP VCP; ignored for usart3")
    ap.add_argument("--data-port", default=None,
                    help="defaults to udp:%d for usart3 (the MicoAir module's "
                         "downlink port), else the control port. A COMx name "
                         "still selects a serial data path."
                         % DEFAULT_USART3_UDP_PORT)
    ap.add_argument("--usart3-baud", type=int, default=921600,
                    help="USART3 baud used ONLY for the link-budget check; "
                         "nothing on the wire changes. Mirrors USART3_BAUD in "
                         "BSP/usart3.h")
    ap.add_argument("--auto-split", action="store_true",
                    help="Automatically split large manifests across multiple "
                         "slots when link budget is exceeded. Merges streams "
                         "on the host side into one CSV.")
    ap.add_argument("--elf", default="OBJ/JX_FLY.axf")
    ap.add_argument("--frames", default=None, metavar="FILE",
                    help="Markdown frame table to log when neither --symbol nor "
                         "--group is given (default: %s)" % DEFAULT_FRAMES.name)
    args = ap.parse_args(argv)

    transport = _TRANSPORTS[args.transport]
    data_port = args.data_port or (
        "udp:%d" % DEFAULT_USART3_UDP_PORT if transport == TRANSPORT_USART3
        else args.control_port)
    if args.symbol and args.group:
        ap.error("--symbol and --group are alternatives, not both")

    try:
        if args.group or not args.symbol:
            if args.group:
                groups = [parse_group(g) for g in args.group]
            else:
                frames = Path(args.frames) if args.frames else DEFAULT_FRAMES
                groups = load_frames(frames)
                print("frame: %s (%d slot(s))" % (frames, len(groups)))
            stats = run_groups(args.control_port, data_port, groups, transport,
                               args.seconds, args.out, elf=args.elf,
                               usart3_baud=args.usart3_baud)
            print("")
            for row in stats:
                print("slot %d: %5d rows = %5.1f Hz   dropped %d (%.2f%%)   "
                      "malformed %d   %s"
                      % (row["slot"], row["rows"], row["hz"], row["dropped"],
                         row["loss_pct"], row["malformed"], row["path"]))
            return 0

        divider = max(1, min(255, round(SEND_TASK_MEASURED_HZ / max(args.rate, 0.4))))
        resolver = SymbolResolver(args.elf)
        ranges = resolve_ranges(resolver, args.symbol)
        result = run(args.control_port, data_port, ranges, divider, transport,
                     args.seconds, args.out, elf=args.elf,
                     usart3_baud=args.usart3_baud, auto_split=args.auto_split)
    except LiveTransportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Auto-split returns list of slot results, single-slot returns one dict
    if isinstance(result, list):
        print("")
        for one in result:
            print("slot %d: %d rows in %.1f s = %.1f Hz" 
                  % (one["slot"], one["rows"], 
                     one["rows"] / (one.get("seconds") or args.seconds), one["hz"]))
            print("  dropped %d (%.2f%%)   malformed %d   %s"
                  % (one["dropped"], one["loss_pct"], one["malformed"], one["path"]))
        return 0

    print("")
    print("%d rows in %.1f s = %.1f Hz" % (result["rows"], result["seconds"], result["hz"]))
    print("dropped %d (%.2f%%)   malformed %d"
          % (result["dropped"], result["loss_pct"], result["malformed"]))
    print("wrote %s" % result["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
