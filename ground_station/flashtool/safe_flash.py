"""Safety-gated headless build + flash pipeline.

  python -m ground_station.flashtool gate     # read-only: is it safe to flash?
  python -m ground_station.flashtool build    # UV4 -b (no hardware)
  python -m ground_station.flashtool flash    # gate -> UV4 -f -> verify   (asks to confirm)
  python -m ground_station.flashtool verify   # read-only EKF liveness check
  python -m ground_station.flashtool all      # preflight -> build -> gate -> flash -> verify

The pipeline is built so the developer can run ``all`` repeatedly while the
drone sits powered on the bench (props off) and no human is required to flip
a power switch or click Build in uVision. The three guards that make that
work, in order:

* **Pre-flight interlocks** (``preflight.run_all``) — refuse if a uVision
  GUI instance is alive, or if another process is already holding the
  CMSIS-DAP interface.
* **Probe-free build** (``_pMon_neutralised``) — UV4 still drives the
  compile+link, but ``<pMon>`` in ``USER/JX_FLY.uvoptx`` is rewritten to
  the simulator DLL for the duration of the build and restored byte-exact
  afterwards. The CMSIS-DAP driver is never loaded, so a powered-on
  flight controller cannot be halted by the build step.
* **Build-identity guard** (``build_id.next_identity`` /
  ``build_id.check_identity``) — every successful build embeds a
  16-byte identity (magic + monotonic counter + epoch + source
  fingerprint) into the firmware image itself; the safety gate reads it
  back over the probe and refuses unless the local ``.axf`` matches the
  firmware actually flashed. Closes the relink drift hazard that
  previously made the gate's own readings untrustworthy.

The gate reads the RUNNING firmware over the probe (read-only, via livewatch)
and refuses unless ARM_Status==DisArmed and no motor bench test is active.
Reflash halts and resets the core; motors are unpowered during it and boot
disarmed. Over the WIRELESS probe a dropped link mid-write can corrupt
flash, so ``flash`` / ``all`` require an explicit confirmation (``--yes`` to
bypass in trusted automation) and UV4 verifies after.
"""
from __future__ import annotations

import argparse
import contextlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import artifact_custody, build_id, preflight, project_integrity

_ROOT = Path(__file__).resolve().parents[2]
_UV4 = Path(r"C:\Keil_v5\UV4\UV4.exe")
_PROJECT = _ROOT / "USER" / "JX_FLY.uvprojx"
_OPTS = _ROOT / "USER" / "JX_FLY.uvoptx"
_ELF = _ROOT / "OBJ" / "JX_FLY.axf"
_OBJ = _ROOT / "OBJ"

DISARMED = 0

# UV4 build/flash logs are written OUTSIDE the project tree. The project path
# contains a space ("...Six_Degrees_of_Freedom _Adaptive_controller"); UV4's
# arg parser splits the -o value on that space even when quoted and mangles the
# path, so an -o inside the project fails ("incorrect path"). C:\\tmp has no
# space. Exposed as a constant so callers (rebuild_and_flash, tests, slash
# commands) can name the same path without duplicating the literal.
LOG_DIR = Path(r"C:\tmp")


# ---- safety gate (read-only) -------------------------------------------

@dataclass
class GateResult:
    ok: bool
    values: dict
    reasons: list[str]

    def report(self) -> str:
        head = "SAFE to flash" if self.ok else "BLOCKED — refusing to flash"
        lines = [f"[safety gate] {head}"]
        for k, v in self.values.items():
            lines.append(f"    {k:24s} {v}")
        for r in self.reasons:
            lines.append(f"    ! {r}")
        return "\n".join(lines)


class SafetyGate:
    """Reads the running target and decides whether a flash may proceed.

    The check has two halves. The first is the build-identity guard: the
    local ``OBJ/JX_FLY.axf`` must describe the firmware actually flashed on
    the target, or every other field below is reading garbage. The second
    is the runtime-state check: ARM_Status must be DisArmed, no motor bench
    test may be active. Both halves must pass; either alone refuses.
    """

    VARS = ["DroneStatus.ARM_Status", "motor_test_active", "motor_test_id"]

    def __init__(self, elf: str | Path = _ELF):
        self.elf = Path(elf)

    def check(self) -> GateResult:
        # Build-identity guard first — refuse if the local ELF is stale.
        identity = build_id.check_identity(self.elf)
        if not identity.ok:
            return GateResult(
                ok=False,
                values={"build_identity": "MISMATCH"},
                reasons=identity.reasons,
            )
        from ground_station.livewatch.reader import LiveReader
        with LiveReader(self.elf) as lr:
            vals = lr.sample(lr.plan(self.VARS))
        vals["build_identity"] = identity.expected.short_label()
        reasons: list[str] = []
        if vals["DroneStatus.ARM_Status"] != DISARMED:
            reasons.append(
                f"ARM_Status={vals['DroneStatus.ARM_Status']} (expected DisArmed=0)"
            )
        if vals["motor_test_active"] != 0:
            reasons.append(
                f"motor_test_active={vals['motor_test_active']} "
                f"(a motor bench test is running)"
            )
        return GateResult(ok=not reasons, values=vals, reasons=reasons)


# ---- build / flash via UV4 ---------------------------------------------

def _kill_resident_uv4() -> None:
    """Kill any running uVision instance before a CLI build/flash.

    A resident UV4 GUI with this project open holds handles on OBJ/*.o and the
    browse .crf files. The command-line build then can't write those outputs and
    every file fails with "couldn't write file ...: Invalid argument". UV4 must be
    the sole process touching OBJ/, so we terminate it first (harmless — the GUI
    holds no unsaved firmware state; sources are saved by the editor on build).
    """
    subprocess.run(["taskkill", "/F", "/IM", "UV4.exe"],
                   capture_output=True, check=False, timeout=5)


def _run_uv4(flag: str, log_name: str, timeout: float) -> tuple[int, str]:
    """Run UV4 with ``flag`` (e.g. ``-r``, ``-b``, ``-f``) and return ``(rc, log_text)``.

    Returns ``(rc=2, text="<empty uv4 log>")`` when UV4 exits cleanly but never
    writes the log file. A 0-byte log means UV4 opened its GUI, found no probe
    path, and exited silently — i.e. the flash (or build) never actually ran.
    Callers used to treat this as success because the only failure signal they
    checked was the literal substring ``Failed`` in the log text; an empty log
    cleared that check and ``verify`` later reported ``STALE ELF`` with no
    in-pipeline error. Now the empty-log case is collapsed to a synthetic rc=2
    so every caller fails closed.
    """
    if not _UV4.exists():
        raise FileNotFoundError(f"UV4 not found at {_UV4}")
    _kill_resident_uv4()
    # Write the build log OUTSIDE the project tree. The project path contains a
    # space ("...Six_Degrees_of_Freedom _Adaptive_controller"); UV4's arg parser
    # splits the -o value on that space even when quoted and mangles the path
    # ("...\\_Adaptive_controller\\_Adaptive_controller\\..."), so an -o inside the
    # project fails ("incorrect path") and pops the GUI. C:\\tmp has no space.
    # Do NOT pass -j0: parallel armcc jobs race on the .crf files. Exit: 0 ok,
    # 1 warnings, >=2 errors.
    # Write the build log to LOG_DIR (see module docstring for why outside the
    # project tree). -o must point to a path with no spaces in it.
    log = LOG_DIR / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    # Truncate so a stale log from a previous run can't be read back as if it
    # belonged to this invocation (matters for the flash stage where the runner
    # used to silently accept any prior success log).
    try:
        log.write_text("")
    except OSError:
        pass
    proc = subprocess.run([str(_UV4), flag, str(_PROJECT), "-o", str(log)],
                          timeout=timeout)
    text = log.read_text(errors="replace") if log.exists() else ""
    if not text.strip():
        # UV4 produced no log at all. UV4 normally writes a log line per phase
        # (Load/Erase/Programming/Verify), so silence is a failure mode, not a
        # clean run. Surface it as rc=2 (UV4's "errors" exit code) so the
        # caller's rc<2 check rejects it. The rebuild-and-flash wrapper also
        # catches "Failed" in text, but an empty text cleared that check.
        return 2, ""
    return proc.returncode, text


@contextlib.contextmanager
def _browse_info_disabled():
    """Temporarily set <BrowseInformation> to 0 in the project, then restore.

    Headless armcc fails to (re)open the per-file browse .crf files ("cannot open
    source input file ...crf: Invalid argument") for a subset of sources, aborting
    the build. Browse info is a uVision GUI-only convenience (go-to-definition),
    irrelevant to producing the firmware image, so we disable it just for the
    command-line build and restore the user's setting afterward (GUI keeps it on).
    """
    # Operate on raw bytes: text mode would rewrite the project's CRLF line endings
    # to LF and re-encode it, needlessly churning the file the user edits in the GUI.
    original = _PROJECT.read_bytes()
    on, off = b"<BrowseInformation>1</BrowseInformation>", b"<BrowseInformation>0</BrowseInformation>"
    if on not in original:
        yield  # already off / unexpected format; nothing to do
        return
    try:
        _PROJECT.write_bytes(original.replace(on, off, 1))
        yield
    finally:
        _PROJECT.write_bytes(original)


# Probe-free build (Approach 2 in the spec): rewrite <pMon> in uvoptx so UV4
# never instantiates the CMSIS-DAP driver DLL. The <pMon> element selects the
# monitor driver uVision uses to talk to the target; "BIN\\CMSIS_AGDI.dll"
# is the CMSIS-DAP driver that opens the SWD probe during project load.
# Pointing <pMon> at the simulator DLL ("SARMCM3.DLL") keeps UV4 happy
# without ever claiming the probe. Byte-exact restore on exit so the user's
# uVision GUI continues to work normally afterwards.
_PMON_CMSIS_DAP = b"<pMon>BIN\\CMSIS_AGDI.dll</pMon>"
_PMON_SIMULATOR = b"<pMon>SARMCM3.DLL</pMon>"


@contextlib.contextmanager
def _pMon_neutralised():
    """Rewrite ``<pMon>`` in ``uvoptx`` to the simulator DLL for the build.

    Same byte-exact restore pattern as ``_browse_info_disabled``. If the
    element is already absent or already neutralised (e.g. the user's uVision
    project never used a hardware monitor), this is a no-op — we still
    restore the file on exit so a previously-untouched file comes back
    byte-identical.

    Restore is best-effort and NEVER raises: if Windows has the file locked
    (open in uVision, virus scanner, etc.) we leave the on-disk state as it
    is and surface a loud warning, so the user can re-run the script with
    uVision closed. The OSError seen in production (Sep 2026) was the
    restore step failing after a successful build — the build itself was
    fine, but uvoptx was left pointing at SARMCM3.DLL, which made the next
    ``UV4 -f`` flash attempt spawn a GUI that exited silently with rc=2.
    """
    original = _OPTS.read_bytes()
    if _PMON_CMSIS_DAP not in original:
        yield  # nothing to neutralise
        return
    try:
        _OPTS.write_bytes(original.replace(_PMON_CMSIS_DAP, _PMON_SIMULATOR, 1))
        yield
    finally:
        try:
            _OPTS.write_bytes(original)
        except OSError as exc:
            # Surface the failure loudly so the user re-runs with uVision
            # closed. We do NOT re-raise: the build succeeded, aborting now
            # would discard that work and produce a worse error story.
            import warnings
            warnings.warn(
                f"could not restore uvoptx ({type(exc).__name__}: {exc}); "
                f"check USER/JX_FLY.uvoptx still points to BIN\\CMSIS_AGDI.dll "
                f"before any UV4 -f flash. If it stuck on SARMCM3.DLL the next "
                f"headless flash will fail with rc=2 and a 0-byte log.",
                RuntimeWarning,
                stacklevel=2,
            )


# Files armcc failed to write this pass, so we can delete + rebuild only those.
# Two shapes: object write ("couldn't write file '..\obj\x.o'") and browse-info
# read/append ("cannot open source input file "..\obj\x.crf"").
_FAILED_OUT = re.compile(
    r"""couldn't\ write\ file\ '\.\.[\\/]obj[\\/]([^']+)'"""
    r"""|cannot\ open\ source\ input\ file\ "\.\.[\\/]obj[\\/]([^"]+)\"""",
    re.IGNORECASE | re.VERBOSE)


def _delete_failed_outputs(text: str) -> int:
    """Delete the OBJ artifacts armcc couldn't write, forcing their recompile."""
    n = 0
    for m in _FAILED_OUT.finditer(text):
        name = m.group(1) or m.group(2)
        f = _ROOT / "OBJ" / name
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def build(rebuild: bool = False, timeout: float = 600, max_passes: int = 20,
          verbose: bool = True) -> tuple[bool, str]:
    """Headless compile+link, robust to real-time scanner races on OBJ/.

    Defender's real-time scan intermittently grabs freshly-written OBJ artifacts,
    so armcc/armlink fail a shifting subset with "Invalid argument" — and the final
    link (JX_FLY.axf, a fresh executable image RT protection scans hardest) fails
    on nearly every attempt. We disable browse info (removes the .crf failure mode)
    and retry `-b`: each pass deletes the .o files that failed (missing ones just
    recompile) and, if the link couldn't open JX_FLY.axf, deletes the stale image
    so the next link creates it fresh, with a short settle delay for the scanner to
    release the previous output. Written outputs persist, so the set shrinks and the
    build converges. Returns (ok, summary). No hardware needed.

    The build identity is stamped into the firmware image before UV4 runs (via a
    transient ``OBJ/build_id.c`` + temporary ``<Group>`` in ``uvprojx``), and the
    artifact custody snapshot is taken first so an abandoned build leaves the
    working tree holding artifacts that match the firmware on the target.
    """
    import time

    # Snapshot the flashed-matching triple before UV4 touches OBJ/. If this
    # build completes without a subsequent flash, the snapshot is restored
    # by the caller (the ``build`` CLI does it; ``all`` defers until the
    # chain reaches the flash stage).
    custody = artifact_custody.snapshot(_OBJ)

    # Stamp the build identity: allocate the next counter+timestamp+fingerprint
    # (incrementing the on-disk counter so the next build is correctly numbered),
    # generate the C source, splice the file into uvprojx via a context manager
    # that restores the project byte-exact on exit. The C file itself is removed
    # by the transient-file context manager after the build.
    identity = build_id.next_identity(_OBJ, root=_ROOT)

    axf = _ROOT / "OBJ" / "JX_FLY.axf"
    with _browse_info_disabled(), _pMon_neutralised(), \
            build_id.uvprojx_with_build_id(_PROJECT, _OBJ), \
            build_id.transient_build_id_source(_OBJ, identity):
        last = ""
        for i in range(1, max_passes + 1):
            flag = "-r" if (rebuild and i == 1) else "-b"
            rc, text = _run_uv4(flag, "flash_build.log", timeout)
            last = text
            raced = "Invalid argument" in text
            n_obj = len(_FAILED_OUT.findall(text))
            link_failed = "JX_FLY.axf" in text and "Invalid argument" in text
            if verbose:
                print(f"    pass {i:2d}: exit={rc} obj_write_fails={n_obj} "
                      f"link_fail={link_failed}")
            if rc < 2 and not raced:
                return True, f"(pass {i}, UV4 exit {rc}) build OK — target created"
            if not raced:
                break  # a genuine compile/link error, not a scanner race — stop
            _delete_failed_outputs(text)
            if link_failed:
                try:
                    axf.unlink(missing_ok=True)  # force a fresh image next link
                except OSError:
                    pass  # scanner still holds it; next link retries anyway
            time.sleep(1.5)  # let the scanner release the outputs it grabbed
    tail = "\n".join(last.splitlines()[-12:])
    return False, f"(gave up after pass {i}, still racing)\n{tail}"


def flash(timeout: float = 300) -> tuple[bool, str]:
    """Download the built image via UV4 -f (halts/erases/programs/resets/verifies)."""
    rc, text = _run_uv4("-f", "flash_download.log", timeout)
    ok = rc < 2
    tail = "\n".join(text.splitlines()[-12:])
    return ok, f"(UV4 exit {rc})\n{tail}"


# ---- post-flash verification (read-only) -------------------------------

def verify_ekf(elf: str | Path = _ELF) -> str:
    from ground_station.livewatch.reader import LiveReader
    names = ["s_ekf.active", "s_ekf.x[0]", "s_ekf.x[3]", "s_ekf.nis"]
    with LiveReader(elf) as lr:
        row = lr.sample(lr.plan(names))
    active = row["s_ekf.active"]
    verdict = "EKF RUNNING" if active == 1 else f"EKF still gated (active={active})"
    return (f"[verify] {verdict}\n"
            f"    s_ekf.active {active}\n"
            f"    v_body.x     {row['s_ekf.x[0]']:+.5g} m/s\n"
            f"    b_a.x        {row['s_ekf.x[3]']*1000:+.3f} mg\n"
            f"    nis          {row['s_ekf.nis']:+.4g}")


# ---- CLI ---------------------------------------------------------------

def _confirm(auto_yes: bool) -> bool:
    if auto_yes:
        return True
    try:
        return input("Proceed to FLASH the running target? [type 'flash' to confirm] ").strip() == "flash"
    except EOFError:
        return False


def _preflight_or_exit() -> None:
    """Run pre-flight interlocks and exit on failure with a unique code (4)."""
    pf = preflight.run_all()
    print(pf.report())
    if not pf.ok:
        sys.exit(4)


def _gate_or_exit(elf: str | Path) -> GateResult:
    """Run the safety gate (with build-identity check) and exit on failure."""
    gate = SafetyGate(elf).check()
    print(gate.report())
    if not gate.ok:
        sys.exit(2)
    return gate


def main(argv=None):
    p = argparse.ArgumentParser(prog="flashtool", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["gate", "build", "flash", "verify", "all"])
    p.add_argument("--rebuild", action="store_true", help="full rebuild (UV4 -r) instead of incremental")
    p.add_argument("--yes", action="store_true", help="skip the interactive flash confirmation")
    p.add_argument("--skip-preflight", action="store_true",
                   help="skip the UV4.exe / CMSIS-DAP holder checks (NOT recommended)")
    args = p.parse_args(argv)

    # ``gate`` is purely a probe read with no build — no preflight snapshot
    # mutation is possible, but a competing CMSIS-DAP holder would still
    # make the read fail or return garbage, so we run preflight there too.
    if args.cmd == "gate":
        if not args.skip_preflight:
            _preflight_or_exit()
        gate = SafetyGate().check()
        print(gate.report())
        if not gate.ok:
            sys.exit(2)
        return

    if args.cmd == "verify":
        if not args.skip_preflight:
            _preflight_or_exit()
        # Read-only, motors-safe. Use after a manual uVision build+flash to confirm
        # the EKF is live on the running target.
        print(verify_ekf())
        return

    # ``build`` runs UV4, which is the path that previously halted the FC.
    # Refuse immediately if a resident uVision GUI or another livewatch
    # process is alive — building through them is what previously crashed
    # the FC, and we now refuse rather than warn.
    if args.cmd in ("build", "all"):
        if not args.skip_preflight:
            _preflight_or_exit()
        ok, log = build(rebuild=args.rebuild)
        print(f"[build] {'OK' if ok else 'FAILED'} {log}")
        if not ok:
            # Build failed — restore the snapshot so the working tree
            # again describes the firmware actually on the target.
            artifact_custody.restore(_OBJ)
            sys.exit(1)
        if args.cmd == "build":
            # The user invoked ``build`` directly with no intent to flash.
            # Honor the spec's "abandoned build leaves the flashed-matching
            # triple in place" rule by restoring the snapshot.
            artifact_custody.restore(_OBJ)
            return

    # flash / all: gate -> confirm -> flash -> verify
    gate = _gate_or_exit(_ELF)
    if not _confirm(args.yes):
        print("[flash] aborted (not confirmed)")
        # Aborted by the operator — restore the snapshot so the working
        # tree continues to describe the firmware on the target.
        artifact_custody.restore(_OBJ)
        sys.exit(3)
    ok, log = flash()
    print(f"[flash] {'OK' if ok else 'FAILED'} {log}")
    if not ok:
        # Flash failed — do NOT delete the snapshot. The on-disk artifacts
        # no longer describe what's on the target, but neither does the
        # failed flash. Restore the snapshot so livewatch stays trustworthy.
        artifact_custody.restore(_OBJ)
        sys.exit(1)
    # Successful flash — the on-disk artifacts now match what's on the
    # target. Commit the custody cache (delete it) and run the post-flash
    # verification.
    artifact_custody.commit(_OBJ)
    print(verify_ekf())


if __name__ == "__main__":
    main()