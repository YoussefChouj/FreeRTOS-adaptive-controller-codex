"""Is the flight controller powered right now? Listen, don't guess.

Build and flash want *opposite* power states, and getting it wrong is not a
recoverable mistake:

* **Build** needs the target OFF. Loading the uVision project initialises the
  CMSIS-AGDI debug driver (``<pMon>BIN\\CMSIS_AGDI.dll`` in ``JX_FLY.uvoptx``),
  which claims the probe over SWD. On 2026-07-28 a headless build against a
  powered drone left the core halted -- LED dark, ESCs beeping -- and it never
  self-recovered.
* **Flash** needs the target ON, obviously.

So this is a gate, not a diagnostic. It answers the question by *listening* to
UART5's virtual COM port: the flight controller pushes ``0xAA 0xBB`` telemetry
frames continuously and unprompted, so their presence means a running core and
their absence means a dark one. Nothing here opens SWD, so running the check
cannot itself disturb the target.

    python -m ground_station.flashtool.target_power              # report
    python -m ground_station.flashtool.target_power --require off  # gate a build
    python -m ground_station.flashtool.target_power --require on   # gate a flash

Exit codes: 0 the requirement holds, 1 it does not, 2 the port could not be
opened (dongle unplugged, or another process owns it).
"""

from __future__ import annotations

import argparse
import sys
import time

PREAMBLE = b"\xaa\xbb"
MIN_BYTES = 200


class PortUnavailable(Exception):
    """COM port could not be opened -- neither 'on' nor 'off' can be claimed."""


def sample(port: str = "COM6", seconds: float = 1.5, baud: int = 115200) -> bytes:
    import serial

    try:
        link = serial.Serial(port, baud, timeout=0.2)
    except Exception as exc:  # serial.SerialException and friends
        raise PortUnavailable("%s: %s" % (port, exc))
    try:
        link.reset_input_buffer()
        rx = bytearray()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rx.extend(link.read(4096))
        return bytes(rx)
    finally:
        link.close()


def powered_from_sample(rx: bytes) -> bool:
    """Bytes alone are not proof; a telemetry preamble is.

    A floating line can deliver plenty of bytes, so require both volume and at
    least one real frame header before calling the target alive.
    """
    return len(rx) >= MIN_BYTES and PREAMBLE in rx


def target_powered(port: str = "COM6", seconds: float = 1.5) -> bool:
    return powered_from_sample(sample(port, seconds))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", default="COM6", help="UART5 / CMSIS-DAP VCP")
    ap.add_argument("--seconds", type=float, default=1.5)
    ap.add_argument("--require", choices=("on", "off"), default=None,
                    help="'off' gates a build, 'on' gates a flash")
    args = ap.parse_args(argv)

    try:
        rx = sample(args.port, args.seconds)
    except PortUnavailable as exc:
        print("[target-power] cannot read %s -- refusing to claim a power state"
              % exc, file=sys.stderr)
        return 2

    on = powered_from_sample(rx)
    print("[target-power] %s: %d B in %.1f s -> target is %s"
          % (args.port, len(rx), args.seconds, "POWERED" if on else "OFF"))

    if args.require is None:
        return 0
    if (args.require == "on") == on:
        return 0

    if args.require == "off":
        print("[target-power] REFUSING: a build with the target powered can halt "
              "the core (CMSIS-AGDI claims the probe). Power the drone down first.",
              file=sys.stderr)
    else:
        print("[target-power] REFUSING: nothing to flash -- the target is dark.",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
