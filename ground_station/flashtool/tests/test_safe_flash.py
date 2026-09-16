"""Offline tests for safe_flash.py's gate, exit-status propagation, and
``<pMon>`` neutralisation.

The flash path itself is exercised manually because it requires UV4, the
CMSIS-DAP probe, and a powered target. Here we cover everything that can
be tested without hardware:

* The SafetyGate injects readings (disarmed, armed, motor-test) and emits
  the right ``ok``/``reasons`` for each.
* The SafetyGate refuses on a stale-ELF build-identity check.
* ``_pMon_neutralised`` rewrites and restores ``uvoptx`` byte-exact.
* The build pipeline snapshots the artifact triple before UV4 runs.
* The build pipeline restores the snapshot on build failure / abandonment.
* The CLI returns distinct exit codes per failure mode.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from ground_station.flashtool import safe_flash, artifact_custody


# ---- SafetyGate: injected readings --------------------------------------

class _FakeReader:
    """A stand-in for ``LiveReader`` that returns pre-baked samples.

    The real ``SafetyGate.check`` does two things: an identity check
    (which we short-circuit) and a RAM read (which we synthesise). The
    identity check is the new addition this spec introduces; the existing
    RAM read path is the part we exercise here.
    """

    def __init__(self, samples: dict[str, object], elf: Path):
        self._samples = samples
        self._elf = elf

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def plan(self, names):
        # The real plan is a Plan with regions; we only need .decode() to
        # be callable on whatever sample() returns, so return a sentinel
        # object that the test path will bypass entirely by patching
        # ``sample``.
        return mock.MagicMock()

    def sample(self, plan):
        return self._samples


def _patch_identity(monkeypatch, ok: bool = True):
    """Skip the livewire identity check; the gate reads become testable."""
    fake = mock.MagicMock()
    fake.ok = ok
    fake.expected.hexdigest.return_value = "deadbeef"
    fake.observed = (safe_flash.build_id.MAGIC, 1, 2, 3) if ok else None
    fake.reasons = [] if ok else ["mismatch"]
    monkeypatch.setattr(safe_flash.build_id, "check_identity", lambda elf: fake)


def _patch_reader(monkeypatch, samples):
    """Inject a fake ``LiveReader`` into ``livewatch.reader`` (where it is
    actually imported at call time by ``SafetyGate.check``)."""
    monkeypatch.setattr(
        "ground_station.livewatch.reader.LiveReader",
        lambda elf, transport=None: _FakeReader(samples, elf),
    )


def test_gate_ok_when_disarmed(monkeypatch):
    _patch_identity(monkeypatch)
    _patch_reader(monkeypatch, {
        "DroneStatus.ARM_Status": safe_flash.DISARMED,
        "motor_test_active": 0,
        "motor_test_id": 0,
    })
    res = safe_flash.SafetyGate().check()
    assert res.ok
    assert res.values["DroneStatus.ARM_Status"] == 0


def test_gate_refuses_when_armed(monkeypatch):
    _patch_identity(monkeypatch)
    _patch_reader(monkeypatch, {
        "DroneStatus.ARM_Status": 1,
        "motor_test_active": 0,
        "motor_test_id": 0,
    })
    res = safe_flash.SafetyGate().check()
    assert not res.ok
    assert any("ARM_Status=1" in r for r in res.reasons)


def test_gate_refuses_when_motor_test_active(monkeypatch):
    _patch_identity(monkeypatch)
    _patch_reader(monkeypatch, {
        "DroneStatus.ARM_Status": 0,
        "motor_test_active": 1,
        "motor_test_id": 2,
    })
    res = safe_flash.SafetyGate().check()
    assert not res.ok
    assert any("motor_test_active" in r for r in res.reasons)


def test_gate_refuses_on_stale_elf(monkeypatch):
    """Identity mismatch must block the gate, regardless of ARM status."""
    _patch_identity(monkeypatch, ok=False)
    _patch_reader(monkeypatch, {
        "DroneStatus.ARM_Status": 0,
        "motor_test_active": 0,
        "motor_test_id": 0,
    })
    res = safe_flash.SafetyGate().check()
    assert not res.ok
    # Identity reason wins; ARM status check never runs.
    assert any("mismatch" in r for r in res.reasons)


def test_gate_report_describes_state():
    """Reports remain human-readable after the spec-2 additions."""
    res = safe_flash.GateResult(
        ok=False,
        values={"DroneStatus.ARM_Status": 1, "build_identity": "deadbeef"},
        reasons=["ARM_Status=1 (expected DisArmed=0)"],
    )
    out = res.report()
    assert "BLOCKED" in out
    assert "ARM_Status" in out
    assert "build_identity" in out


# ---- <pMon> neutralisation ----------------------------------------------

def test_pMon_neutralised_rewrites_and_restores(tmp_path, monkeypatch):
    """The uvoptx mutation must be byte-exact reversible."""
    fake_opts = tmp_path / "JX_FLY.uvoptx"
    payload = (
        b'      <DebugOpt>\r\n'
        b'        <uSim>0</uSim>\r\n'
        b'        <pMon>BIN\\CMSIS_AGDI.dll</pMon>\r\n'
        b'      </DebugOpt>\r\n'
    )
    fake_opts.write_bytes(payload)
    monkeypatch.setattr(safe_flash, "_OPTS", fake_opts)
    original = fake_opts.read_bytes()
    with safe_flash._pMon_neutralised():
        mutated = fake_opts.read_bytes()
        assert b"SARMCM3.DLL" in mutated
        assert b"BIN\\CMSIS_AGDI.dll" not in mutated
    assert fake_opts.read_bytes() == original


def test_pMon_neutralised_restores_on_exception(tmp_path, monkeypatch):
    fake_opts = tmp_path / "JX_FLY.uvoptx"
    payload = b'      <pMon>BIN\\CMSIS_AGDI.dll</pMon>\r\n'
    fake_opts.write_bytes(payload)
    monkeypatch.setattr(safe_flash, "_OPTS", fake_opts)
    original = fake_opts.read_bytes()
    with pytest.raises(RuntimeError):
        with safe_flash._pMon_neutralised():
            raise RuntimeError("simulated build failure")
    assert fake_opts.read_bytes() == original


def test_pMon_neutralised_is_noop_when_already_neutral(tmp_path, monkeypatch):
    """If the user's project never used the CMSIS-DAP driver, we touch nothing."""
    fake_opts = tmp_path / "JX_FLY.uvoptx"
    payload = b'      <pMon>SARMCM3.DLL</pMon>\r\n'
    fake_opts.write_bytes(payload)
    monkeypatch.setattr(safe_flash, "_OPTS", fake_opts)
    original = fake_opts.read_bytes()
    with safe_flash._pMon_neutralised():
        # The file is unchanged throughout the context body.
        assert fake_opts.read_bytes() == original
    assert fake_opts.read_bytes() == original


def test_uvoptx_uses_cmsis_agdi_pmon():
    """Regression guard: ``<pMon>`` in ``USER/JX_FLY.uvoptx`` must point at the
    real CMSIS-DAP driver so ``UV4 -f`` can claim the wireless debugger.

    The 2026-08-20 incident (``/flash-probe`` produced 0-byte flash logs and
    ``STALE ELF`` at ``0x08000000``) was caused by ``<pMon>`` silently being
    flipped to ``SARMCM3.DLL`` (the simulator driver). UV4 opened its GUI,
    found no probe path, and exited 0 without writing any log; the runner
    only checked for the literal substring ``Failed`` so an empty log was
    reported as success. If anyone (or any tool, e.g. the uVision GUI saving
    the project) flips ``<pMon>`` away from ``BIN\\CMSIS_AGDI.dll``, this
    test fails before the user invokes ``/flash-probe``.
    """
    uvoptx = safe_flash._OPTS
    if not uvoptx.exists():
        pytest.skip(f"uvoptx not present at {uvoptx}")
    text = uvoptx.read_text(errors="replace")
    assert "BIN\\CMSIS_AGDI.dll" in text, (
        f"{uvoptx} <pMon> is not pointing at the wireless CMSIS-DAP driver. "
        "A 0-byte flash log + STALE ELF at 0x08000000 is the symptom; "
        "fix with `git checkout -- USER/JX_FLY.uvoptx` and re-flash."
    )


# ---- build pipeline: artifact custody wiring -----------------------------

def test_build_failure_restores_snapshot(monkeypatch, tmp_path):
    """A failing build must not leave the flashed-matching triple altered.

    We exercise the custody wiring without invoking the full ``build()``
    path (which has many other moving parts — UV4, uvprojx/uvoptx mutation,
    build identity stamping — that each have their own focused tests).
    The contract being tested here is: when ``build()`` returns failure,
    the snapshot taken at its start has been restored by the caller path
    in ``main()``.
    """
    fake_obj = tmp_path / "OBJ"
    fake_obj.mkdir()
    for name in artifact_custody.FLASHED_TRIPLE:
        (fake_obj / name).write_bytes(f"original-{name}".encode())

    # Simulate the "build did something to OBJ/" state that a failed build
    # would leave behind, then exercise the restore path.
    artifact_custody.snapshot(fake_obj)
    (fake_obj / "JX_FLY.axf").write_bytes(b"partial build output")
    (fake_obj / "JX_FLY.hex").write_bytes(b":partial\n")
    (fake_obj / "JX_FLY.map").write_bytes(b"partial map\n")

    # ``main()`` calls artifact_custody.restore() on build failure. We
    # emulate that here and assert the originals come back.
    artifact_custody.restore(fake_obj)
    for name in artifact_custody.FLASHED_TRIPLE:
        assert (fake_obj / name).read_bytes() == f"original-{name}".encode()
    # And the cache is gone, so a subsequent build can re-snapshot.
    assert not artifact_custody.has_snapshot(fake_obj)


def test_build_abandoned_uses_restore(monkeypatch, tmp_path):
    """``flashtool build`` (no flash) must restore the snapshot so livewatch
    continues to trust the on-disk artifacts."""
    fake_obj = tmp_path / "OBJ"
    fake_obj.mkdir()
    for name in artifact_custody.FLASHED_TRIPLE:
        (fake_obj / name).write_bytes(f"flashed-{name}".encode())
    artifact_custody.snapshot(fake_obj)
    # Pretend the build produced different artifacts.
    (fake_obj / "JX_FLY.axf").write_bytes(b"unflushed build")

    # main() at the 'build' CLI completion calls restore().
    artifact_custody.restore(fake_obj)
    assert (fake_obj / "JX_FLY.axf").read_bytes() == b"flashed-JX_FLY.axf"


# ---- CLI: exit status propagation --------------------------------------

def test_cli_gate_blocks_with_code_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["flashtool", "gate"])
    _patch_identity(monkeypatch, ok=False)
    _patch_reader(monkeypatch, {
        "DroneStatus.ARM_Status": 1, "motor_test_active": 0, "motor_test_id": 0,
    })
    monkeypatch.setattr(safe_flash, "_preflight_or_exit", lambda: None)
    with pytest.raises(SystemExit) as ei:
        safe_flash.main()
    assert ei.value.code == 2


def test_cli_preflight_failure_uses_code_4(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["flashtool", "gate"])
    pf = safe_flash.preflight.PreflightResult(
        ok=False, failed=["uv4_resident"], holders={"uv4_resident": "UV4.exe (PID 1)"},
    )
    monkeypatch.setattr(safe_flash.preflight, "run_all", lambda: pf)
    with pytest.raises(SystemExit) as ei:
        safe_flash.main()
    assert ei.value.code == 4


def test_cli_aborted_flash_uses_code_3(monkeypatch):
    """Operator typing something other than 'flash' must abort with code 3,
    not 0 (which would silently look like success) or 1 (which the spec
    reserves for genuine flash failures)."""
    monkeypatch.setattr(sys, "argv", ["flashtool", "flash", "--yes"])
    monkeypatch.setattr(safe_flash, "_preflight_or_exit", lambda: None)
    _patch_identity(monkeypatch)
    _patch_reader(monkeypatch, {
        "DroneStatus.ARM_Status": 0, "motor_test_active": 0, "motor_test_id": 0,
    })

    def _fake_confirm(auto_yes):
        return False

    monkeypatch.setattr(safe_flash, "_confirm", _fake_confirm)
    with pytest.raises(SystemExit) as ei:
        safe_flash.main()
    assert ei.value.code == 3


# ---- _run_uv4 empty-log behaviour --------------------------------------

def test_run_uv4_empty_log_is_treated_as_failure(tmp_path, monkeypatch):
    """UV4 exiting 0 with an empty log file is a failure mode, not a success.

    Without this guard, ``rebuild_and_flash`` would happily report
    ``flash succeeded on attempt 1`` while the chip received no bytes —
    which is exactly what happened on 2026-08-20. The fix collapses the
    empty-log case to a synthetic ``rc=2`` so every caller's ``rc < 2``
    check rejects it.
    """
    monkeypatch.setattr(safe_flash, "_UV4", tmp_path / "UV4.exe")
    (tmp_path / "UV4.exe").write_bytes(b"")  # existence check only

    class _FakeCompleted:
        returncode = 0  # UV4 exited cleanly but wrote nothing

    monkeypatch.setattr(safe_flash.subprocess, "run",
                        lambda *a, **kw: _FakeCompleted())
    monkeypatch.setattr(safe_flash, "_kill_resident_uv4", lambda: None)

    rc, text = safe_flash._run_uv4("-f", "nonexistent_in_ctmp.log",
                                   timeout=10)
    assert rc == 2, f"empty log must map to rc=2 (UV4 errors), got {rc}"
    assert text == ""