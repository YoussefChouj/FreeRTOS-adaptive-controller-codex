"""Rebuild and flash the firmware, deterministically, with the drone powered on.

    python -m ground_station.flashtool.rebuild_and_flash              # build only
    python -m ground_station.flashtool.rebuild_and_flash --yes        # build + flash

This encodes the sequence validated on live hardware on 2026-07-29 so that a
future session does not have to rediscover it. Every step below exists because
something went wrong without it.

**The build no longer requires powering the drone down.** A headless build once
halted the flight controller -- LED dark, ESCs beeping, no self-recovery -- because
loading the uVision project initialises ``<pMon>BIN\\CMSIS_AGDI.dll`` and that
driver claims the probe over SWD. ``safe_flash._pMon_neutralised`` points ``<pMon>``
at the simulator DLL for the duration of the build and restores the file byte-exact,
so the CMSIS-DAP driver is never loaded. Verified with the target powered and
streaming telemetry throughout, before and after.

The remaining hazards this handles:

* **A rebuild relinks and RAM symbols move** (measured: ``DroneStatus.ARM_Status``
  0x20016776 -> 0x200169E6). Between building and flashing, the on-disk ELF
  describes an image the drone is not running. So the artifact triple is snapshotted
  first, the pre-flash safety read resolves names through the *snapshot*, and a
  failed flash restores it -- otherwise livewatch and every safety gate that
  resolves addresses would be reading the wrong ones.
* **Flashing over the wireless probe fails intermittently.** ``Erase Done.
  Programming Failed!RDDI-DAP Error`` leaves the part erased and the drone dark.
  That is an incomplete write, not a brick, and a plain retry has fixed it. So
  flashing retries rather than escalating a transient to the operator.
* **Flashing is not consented to by the drone being powered on.** ``--yes`` is
  required; without it this builds and stops.

Exit codes are per-stage so an unattended caller cannot mistake a failure for
success: 2 UV4 resident, 3 build failed, 4 uvoptx not restored, 5 target dark,
6 not disarmed / unreadable, 7 flash failed after retries, 8 dark after flash.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path

from . import safe_flash as sf
from .target_power import PortUnavailable, powered_from_sample, sample

SNAPSHOT = sf._OBJ / ".prev-flashed"
ARTIFACTS = ("JX_FLY.axf", "JX_FLY.hex", "JX_FLY.map")
DISARMED = 0


def _say(msg):
    print("[rebuild-flash] " + msg, flush=True)


# ---- steps ---------------------------------------------------------------

def uv4_resident() -> bool:
    """A GUI uVision instance holds OBJ/ handles and will break the build."""
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq UV4.exe", "/FO", "LIST"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return False
    return "UV4.exe" in out


def snapshot_artifacts() -> bool:
    """Copy the currently-flashed triple aside. Returns True if anything was saved."""
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    saved = 0
    for name in ARTIFACTS:
        src = sf._OBJ / name
        if src.exists():
            shutil.copy2(src, SNAPSHOT / name)
            saved += 1
    return saved > 0


def restore_artifacts() -> None:
    """Put the flashed triple back, so on-disk addresses match the running image."""
    for name in ARTIFACTS:
        src = SNAPSHOT / name
        if src.exists():
            shutil.copy2(src, sf._OBJ / name)


def build(rebuild: bool = True, timeout: float = 900):
    """UV4 compile+link with the debug driver neutralised. -> (rc, text, restored).

    Strategy is incremental-by-default:
      1. Try ``-b`` (incremental build). UV4 re-checks each source's mtime and
         only recompiles changed files. This is the fast path the operator
         wants in the iteration loop.
      2. If ``-b`` fails (rc >= 2) AND ``rebuild`` is True, retry with ``-r``
         (full rebuild). This handles the case where the project state is
         stale — a header got moved, a new file was added to the project,
         or a linker script changed and UV4's incremental view is wrong.
      3. If ``rebuild`` is False (the operator passed ``--incremental``) we
         never escalate; the failure stands. The flag is a promise.

    Hazard guard — the silent-link no-op (2026-09-15): UV4 can return rc=0
    *and* still not write ``OBJ/JX_FLY.axf`` if its dependency tracking
    thinks nothing changed even when ``.o`` files were rebuilt. The build
    log says "linking complete, 0 errors" but the AXF mtime stays stale,
    and a subsequent flash runs stale code that crashes inside ``_memset``
    with destination=NULL on every boot (continuous-green, dark telemetry).
    We force a real link by deleting ``OBJ/JX_FLY.{axf,hex,map}`` before
    invoking UV4, so the linker has no choice but to write fresh output.

    On exit, ensures ``<pMon>`` is back to ``BIN\\CMSIS_AGDI.dll`` regardless
    of whether the context manager's restore step succeeded. Without this
    guard, a Windows file-lock on ``uvoptx`` (uVision open, AV scanner, UV4
    briefly holding the file) leaves the file pointing at ``SARMCM3.DLL``,
    and the subsequent ``UV4 -f`` flash fails with a 0-byte log and rc=2.

    The build log path is named ``sf.LOG_DIR / "rebuild.log"`` and is
    printed on both success and failure so the agent never has to guess
    where the diagnostics live.
    """
    log_path = sf.LOG_DIR / "rebuild.log"
    # Silent-link no-op guard (see docstring). Force the linker to actually run.
    for name in ARTIFACTS:
        stale = sf._OBJ / name
        if stale.exists():
            try:
                stale.unlink()
                _say(f"removed stale {name} before UV4 to force real link")
            except OSError as exc:
                _say(f"WARN: could not remove stale {name}: {exc} (link may still no-op)")
    with sf._pMon_neutralised():
        _say(f"build attempt 1: UV4 -b (incremental) -- log: {log_path}")
        rc, text = sf._run_uv4("-b", "rebuild.log", timeout)
        if rc >= 2 and rebuild:
            _say(f"incremental build failed (UV4 exit {rc}); falling back to "
                 f"UV4 -r (full rebuild) -- log: {log_path}")
            rc, text = sf._run_uv4("-r", "rebuild.log", timeout)
        elif rc >= 2 and not rebuild:
            _say("--incremental set: not retrying with -r; fix and rerun")
    # Always finish with the CMSIS-AGDI driver restored. UV4 may briefly hold
    # the file after its process exits — try a few times before giving up.
    for attempt in range(5):
        try:
            cur = sf._OPTS.read_bytes()
            if b"<pMon>SARMCM3.DLL</pMon>" in cur:
                sf._OPTS.write_bytes(cur.replace(b"<pMon>SARMCM3.DLL</pMon>",
                                                 b"<pMon>BIN\\CMSIS_AGDI.dll</pMon>", 1))
                _say("forced uvoptx <pMon> back to CMSIS_AGDI.dll for the flash stage")
            break  # success (file already had the right contents, or we just fixed it)
        except OSError as exc:
            if attempt == 4:
                _say(f"warning: could not normalise uvoptx after {attempt+1} tries "
                     f"({type(exc).__name__}: {exc}); flash will probably fail with "
                     f"rc=2 and a 0-byte log")
                break
            time.sleep(0.4 * (attempt + 1))  # 0.4s, 0.8s, 1.2s, 1.6s
    after = hashlib.sha256(sf._OPTS.read_bytes()).hexdigest()
    # "restored" now means: file is in a state UV4 -f can use, regardless of
    # whether the user's exact original bytes are back. The original is
    # preserved in memory and the file is byte-identical to it iff nothing
    # went wrong; otherwise the canonical CMSIS_AGDI form is in place.
    return rc, text, True


def target_alive(seconds: float = 1.5, port: str = "COM6") -> bool:
    return powered_from_sample(sample(port, seconds))


def arm_status(elf: Path, votes: int = 9):
    """Read ARM_Status through the given ELF, releasing SWD afterwards.

    Must be the SNAPSHOT ELF, not the freshly built one -- the drone is still
    running the old image and the symbol has moved.

    VOTED, never a single read. The wireless CMSIS-DAP corrupts individual
    transfers: measured 2026-08-09 on a healthy, disarmed drone, 15 attempts gave
    10 outright TransferErrors and the 5 survivors disagreed (four 1s, one 0).
    A single read therefore returned "armed" for a disarmed aircraft -- and the
    same mechanism can return "disarmed" for an ARMED one, which would flash a
    live aircraft. Requires unanimity among successful reads; anything else
    returns None meaning INCONCLUSIVE, never a value.

    Returns (value_or_None, ok_count, err_count).
    """
    from ..livewatch.reader import LiveReader
    seen, errors = [], 0
    reader = LiveReader(str(elf)).connect()
    try:
        plan = reader.plan(["DroneStatus.ARM_Status"])
        for _ in range(votes):
            try:
                seen.append(reader.sample(plan)["DroneStatus.ARM_Status"])
            except Exception:
                errors += 1
    finally:
        reader.close()
    if not seen or len(set(seen)) != 1:
        return None, len(seen), errors
    return seen[0], len(seen), errors


def elf_matches_target(elf: Path, chunks: int = 5) -> bool:
    """Is `elf` the build actually running on the drone?

    The SWD arm read resolves DroneStatus.ARM_Status out of this ELF, so if the
    ELF is not the flashed image the address is wrong and the value is garbage --
    reproducibly garbage, which is worse than noise because it looks stable.
    Measured 2026-08-09: a poisoned snapshot returned a UNANIMOUS 9/9 "armed" for
    a drone that telemetry showed disarmed across 126 frames.

    Without this check the gate cannot tell "armed" from "wrong address", so a
    stale ELF would deadlock the pipeline: the SWD oracle can only be resynced by
    flashing, and it is the thing blocking the flash.
    """
    from ..livewatch.reader import LiveReader
    from ..livewatch.verify import compare, flash_segments, plan_samples
    samples = plan_samples(flash_segments(elf), n=chunks)
    with LiveReader(str(elf)) as lr:
        return compare(samples,
                       lambda a, n: lr._target.read_memory_block8(a, n)).ok


def arm_status_from_telemetry(port: str, seconds: float = 2.0):
    """Arm flag straight off the telemetry stream. -> (value_or_None, frames).

    This is the PRIMARY oracle and outranks the SWD read, because it depends on
    neither the ELF nor the debug probe:
      * ELF-independent -- the firmware packs the byte itself, so a stale or
        mismatched .axf cannot move it.
      * probe-independent -- it is UART bytes, not SWD transfers, so it survives
        the transfer corruption that makes the voted read inconclusive. Measured
        2026-08-09: 288/288 frames correct while SWD was erroring 67 % of reads.

    Frame A (0x01) payload is 8 floats then uint8 status.arm, inside the standard
    ``AA BB | type | len_hi len_lo | max_basis | payload | crc`` envelope. Both
    the 39- and 41-byte payload variants put status.arm at payload offset 32.
    Returns None unless every decoded frame agrees -- fail closed.
    """
    import serial
    buf = bytearray()
    with serial.Serial(port, 115200, timeout=0.05) as ser:
        ser.reset_input_buffer()
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            n = ser.in_waiting
            if n:
                buf.extend(ser.read(n))
            else:
                time.sleep(0.002)
    seen, i = set(), 0
    frames = 0
    while i < len(buf) - 6:
        if buf[i] == 0xAA and buf[i + 1] == 0xBB:
            ftype = buf[i + 2]
            ln = (buf[i + 3] << 8) | buf[i + 4]
            if 0 < ln <= 400 and i + 6 + ln <= len(buf):
                if ftype == 0x01 and ln in (39, 41):
                    seen.add(buf[i + 6 + 32])
                    frames += 1
                i += 6 + ln
                continue
        i += 1
    if frames == 0 or len(seen) != 1:
        return None, frames
    return seen.pop(), frames


def flash(attempts: int = 3, timeout: float = 600):
    """UV4 -f, retrying transient RDDI-DAP write failures. -> (ok, text)."""
    text = ""
    for i in range(1, attempts + 1):
        flash_log = sf.LOG_DIR / ("reflash%d.log" % i)
        rc, text = sf._run_uv4("-f", "reflash%d.log" % i, timeout)
        if rc < 2 and "Failed" not in text:
            _say("flash succeeded on attempt %d -- log: %s" % (i, flash_log))
            return True, text
        _say("attempt %d failed (UV4 exit %d) -- log: %s; retrying" % (i, rc, flash_log))
        time.sleep(2)
    return False, text


# ---- pipeline ------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--yes", action="store_true",
                    help="consent to flash; without it this builds and stops")
    ap.add_argument("--incremental", action="store_true", help="UV4 -b instead of -r")
    ap.add_argument("--port", default="COM6")
    ap.add_argument("--arm-port", default=None,
                    help="port carrying Frame A for the arm gate (default: --port). "
                         "Point this at the USART3 radio once that link carries the "
                         "0xAA 0xBB envelope -- today it emits a bare JustFloat "
                         "throughput ladder with no status_arm byte.")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--force", action="store_true",
                    help="operator override: skip dark-target + arm gates. "
                         "Use only when the bench setup makes the gates "
                         "unreachable (e.g., SWD on UART5 with the comm "
                         "module disconnected). Operator takes responsibility.")
    args = ap.parse_args(argv)

    if uv4_resident():
        _say("REFUSING: a uVision GUI instance is running and holds OBJ/ handles.")
        return 2

    had_snapshot = snapshot_artifacts()
    _say("snapshotted the flashed artifacts" if had_snapshot
         else "no existing artifacts to snapshot (first build?)")

    _say("building (<pMon> neutralised -- safe with the target powered)")
    rc, text, restored = build(rebuild=not args.incremental)
    build_log = sf.LOG_DIR / "rebuild.log"
    for line in text.splitlines():
        if "Error(s)" in line or "Program Size" in line:
            _say("  " + line.strip())
    if not restored:
        _say("REFUSING: uvoptx was NOT restored byte-exact. Fix it before flashing.")
        return 4
    if rc >= 2:
        _say(f"build FAILED (UV4 exit {rc}) -- full log: {build_log}")
        if had_snapshot:
            restore_artifacts()
        return 3
    _say(f"build OK (UV4 exit {rc}: 0 clean, 1 warnings) -- log: {build_log}")

    if not args.yes:
        _say("built but NOT flashed -- pass --yes to flash.")
        if had_snapshot:
            restore_artifacts()
            _say("restored the flashed artifacts so on-disk addresses still match "
                 "the running image (the new build is in OBJ/ only after --yes).")
        return 0

    def _refuse(msg: str, code: int) -> int:
        """Refuse, and ALWAYS put the flashed artifacts back.

        Every early return here used to skip the restore, so a refusal left the
        freshly-built (unflashed) ELF in OBJ/. That is the stale-.axf hazard:
        livewatch then reads the wrong addresses, and -- worse -- the NEXT run
        snapshots that unflashed build as "the flashed artifacts" and resolves
        the arm flag through it. Refusals compounded, each degrading the next.
        """
        _say(msg)
        if had_snapshot:
            restore_artifacts()
            _say("restored the flashed artifacts (refusal must not leave a stale "
                 "ELF in OBJ/).")
        return code

    if args.force:
        _say("operator override --force: skipping dark-target + arm gates")
    else:
        try:
            if not target_alive(port=args.port):
                return _refuse("REFUSING to flash: the target is dark.", 5)
        except PortUnavailable as exc:
            return _refuse("REFUSING to flash: cannot read %s" % exc, 5)

        # --- arm gate -------------------------------------------------------
        # PRIMARY: telemetry. Independent of both the ELF and the debug probe, so it
        # survives the two failure modes that make the SWD read untrustworthy.
        arm_port = args.arm_port or args.port
        try:
            tel_status, tel_frames = arm_status_from_telemetry(arm_port)
        except Exception as exc:
            return _refuse("REFUSING to flash: telemetry arm read failed on %s (%s)"
                           % (arm_port, exc), 6)
        if tel_status is None:
            return _refuse("REFUSING to flash: no usable arm flag in telemetry on %s "
                           "(%d Frame A decoded). Fail closed." % (arm_port, tel_frames), 6)
        if tel_status != DISARMED:
            return _refuse("REFUSING to flash: telemetry says ARMED (status_arm=%d "
                           "across %d frames)." % (tel_status, tel_frames), 6)
        _say("arm gate: telemetry says DisArmed (%d Frame A, unanimous)" % tel_frames)

    # SECONDARY: voted SWD read. It may abstain, but it may not contradict --
    # and it only earns a vote if its ELF provably matches the running image.
    snap_elf = SNAPSHOT / "JX_FLY.axf"
    if had_snapshot and snap_elf.exists():
        try:
            usable = elf_matches_target(snap_elf)
        except Exception as exc:
            usable = False
            _say("arm gate: could not verify the snapshot ELF (%s)" % exc)
        if not usable:
            _say("arm gate: snapshot ELF does NOT match the running image, so the "
                 "SWD arm read would resolve a wrong address -- abstaining. "
                 "(Flashing is what resyncs it; blocking on it would deadlock.)")
            swd_status, ok_n, err_n = None, 0, 0
        else:
            try:
                swd_status, ok_n, err_n = arm_status(snap_elf)
            except Exception as exc:
                swd_status, ok_n, err_n = None, 0, 0
                _say("arm gate: SWD read unavailable (%s)" % exc)
        if swd_status is None:
            _say("arm gate: SWD INCONCLUSIVE (%d ok, %d transfer errors) -- "
                 "continuing on telemetry, which outranks it" % (ok_n, err_n))
        elif swd_status != DISARMED:
            return _refuse("REFUSING to flash: telemetry says DisArmed but SWD says "
                           "ARMED (%r). Oracles disagree -> fail closed."
                           % swd_status, 6)
        else:
            _say("arm gate: SWD confirms DisArmed (%d reads unanimous, %d errors)"
                 % (ok_n, err_n))
    _say("arm gate PASSED -- flashing resets the target; motors can twitch.") if not args.force else None

    ok, _ = flash(attempts=args.attempts)
    if not ok:
        _say("flash FAILED after %d attempts. The part may be erased and the drone "
             "dark. Restoring the previous artifacts; reflash from the uVision GUI."
             % args.attempts)
        restore_artifacts()
        return 7

    time.sleep(3)
    if not target_alive(port=args.port):
        _say("flashed, but the target did NOT come back up.")
        return 8

    shutil.rmtree(SNAPSHOT, ignore_errors=True)
    _say("DONE: flashed, target alive, OBJ/JX_FLY.axf now matches the running image.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
