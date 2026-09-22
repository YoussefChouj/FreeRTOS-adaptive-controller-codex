"""Compile API/subscribe.c on the host and run its harness.

Everything else in this suite tests the Python side against a byte layout we
believe the firmware produces. This test compiles the actual firmware C and
checks that it does -- including Subscribe_StreamTick's round-robin scheduler,
which no amount of host-side testing can substantiate.

Needs a 32-bit-capable gcc (the firmware assumes 32-bit pointers). Skipped
rather than failed when that is unavailable, so the suite still runs on
machines without a cross-capable toolchain.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "API" / "tests" / "test_subscribe_harness.c"
STUBS = ROOT / "API" / "tests" / "stubs"
SOURCE = ROOT / "API" / "subscribe.c"


def _gcc_supports_m32(gcc: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.c"
        probe.write_text("int main(void){return 0;}\n")
        result = subprocess.run(
            [gcc, "-m32", str(probe), "-o", str(Path(tmp) / "probe.exe")],
            capture_output=True,
        )
        return result.returncode == 0


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_firmware_c_passes_its_own_harness():
    gcc = shutil.which("gcc")
    if not _gcc_supports_m32(gcc):
        pytest.skip("gcc cannot target 32-bit; firmware assumes 32-bit pointers")

    # Two builds, because two behaviours are being asserted and neither can be
    # stated in a binary compiled the other way. The default build is what
    # ships: UART5 is not wired, so subscribe.c refuses a UART5 subscribe. The
    # -DSUBSCRIBE_UART5_ENABLED=1 build is the only one in which the link
    # budget -- which is computed from UART5's baud -- can be exercised at all.
    #
    # The harness used to be built once, without the flag, while asserting the
    # ACK that subscribe.c stopped sending on 2026-09-20. It reported 7 of its
    # own 25 checks failing and the suite simply carried the failure.
    for label, extra in (("production (UART5 disabled)", []),
                         ("UART5 enabled", ["-DSUBSCRIBE_UART5_ENABLED=1"])):
        _build_and_run(gcc, label, extra)


def _build_and_run(gcc, label, extra_defines):
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "harness.exe"
        build = subprocess.run(
            [
                gcc, "-m32", "-std=c99", "-Wall", "-Wextra",
                "-Wno-unused-parameter", "-Wno-type-limits",
                "-I", str(STUBS), "-I", str(ROOT / "API"),
                "-I", str(ROOT / "firmware"),
                # Widen the allowlist so the harness's own memory validates and
                # every subscription goes through the REAL parser. The firmware
                # build never defines these -- see the guard comment in
                # subscribe.h.
                "-DSUBSCRIBE_ADDR_SRAM_LO=0x00000000U",
                "-DSUBSCRIBE_ADDR_SRAM_HI=0xFFFFFFFEU",
            ] + extra_defines + [
                str(HARNESS), str(SOURCE), "-o", str(exe),
            ],
            capture_output=True, text=True,
        )
        assert build.returncode == 0, (
            "subscribe.c failed to compile [%s]:\n%s" % (label, build.stderr))

        run = subprocess.run([str(exe)], capture_output=True, text=True)
        assert run.returncode == 0, (
            "firmware harness reported failures [%s]:\n%s"
            % (label, run.stdout + run.stderr))
        assert "0 failure(s)" in run.stdout, "[%s] %s" % (label, run.stdout)


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_firmware_build_does_not_widen_the_address_allowlist():
    """The -D override is a test affordance; it must never reach the firmware."""
    project = (ROOT / "USER" / "JX_FLY.uvprojx").read_text(
        encoding="utf-8", errors="replace")
    for name in ("SUBSCRIBE_ADDR_SRAM_LO", "SUBSCRIBE_ADDR_SRAM_HI"):
        assert name not in project, (
            f"{name} is defined in the uVision project -- that silently widens "
            "what a host can ask the drone to read")
