"""Offline tests for the pre-flight interlocks.

Both checks accept injectable callables so the unit tests can substitute
synthetic subprocess output and pyOCD behaviour without spawning real
processes or touching hardware.
"""
from __future__ import annotations

import pytest

from ground_station.flashtool import preflight


# ---- tasklist parser ------------------------------------------------------

def test_parse_tasklist_returns_empty_on_no_match():
    """The "no running tasks" sentinel produces an empty list, not a parse error."""
    text = "\n".join([
        "",
        "INFO: No tasks are running which match the specified criteria.",
        "",
    ])
    assert preflight._parse_tasklist(text) == []


def test_parse_tasklist_extracts_pid_and_image():
    text = "\n".join([
        "Image Name:     UV4.exe",
        "PID:            12345",
        "Session Name:   Console",
        "Session#:       1",
        "",
        "Image Name:     notepad.exe",
        "PID:            67890",
        "",
    ])
    rows = preflight._parse_tasklist(text)
    assert ("12345", "UV4.exe") in rows
    assert ("67890", "notepad.exe") in rows


def test_parse_tasklist_handles_mixed_case():
    """`tasklist /FO LIST` keys are case-insensitive on Windows."""
    text = "\n".join([
        "image name:     UV4.EXE",
        "pid:            111",
        "",
    ])
    rows = preflight._parse_tasklist(text)
    assert rows == [("111", "UV4.EXE")]


# ---- UV4.exe resident check ---------------------------------------------

def test_uv4_resident_ok_when_no_match():
    pf = preflight.uv4_resident(tasklist=lambda: "INFO: No tasks are running which match the specified criteria.")
    assert pf.ok


def test_uv4_resident_refuses_with_holder():
    text = "\n".join([
        "Image Name:     UV4.exe",
        "PID:            12345",
    ])
    pf = preflight.uv4_resident(tasklist=lambda: text)
    assert not pf.ok
    assert "uv4_resident" in pf.failed
    assert "UV4.exe" in pf.holders["uv4_resident"]
    assert "12345" in pf.holders["uv4_resident"]


def test_uv4_resident_report_describes_state():
    ok = preflight.uv4_resident(tasklist=lambda: "INFO: No tasks are running which match the specified criteria.")
    blocked = preflight.uv4_resident(tasklist=lambda: "Image Name: UV4.exe\nPID: 999\n")
    assert "OK" in ok.report()
    assert "BLOCKED" in blocked.report()


# ---- CMSIS-DAP holder check --------------------------------------------

def test_cmsis_dap_holder_ok_when_probe_free():
    pf = preflight.cmsis_dap_holder(attempt=lambda: (True, "probe acquired"))
    assert pf.ok


def test_cmsis_dap_holder_refuses_when_probe_held():
    pf = preflight.cmsis_dap_holder(attempt=lambda: (False, "no probe enumerated (held by another process)"))
    assert not pf.ok
    assert "cmsis_dap_holder" in pf.failed
    assert "another process" in pf.holders["cmsis_dap_holder"]


def test_cmsis_dap_holder_distinguishes_unreachable():
    """A 'probe unplugged' failure should not be conflated with 'probe held'."""
    pf = preflight.cmsis_dap_holder(attempt=lambda: (False, "pyocd not on PATH (cannot probe CMSIS-DAP holder)"))
    assert not pf.ok
    assert "probe unreachable" in pf.holders["cmsis_dap_holder"]


# ---- combined entry point ------------------------------------------------

def test_run_all_ok_when_both_pass():
    pf = preflight.run_all(
        tasklist=lambda: "INFO: No tasks are running which match the specified criteria.",
        attempt=lambda: (True, "probe acquired"),
    )
    assert pf.ok
    assert pf.failed == []
    assert "no resident UV4" in pf.report()


def test_run_all_blocks_on_uv4():
    pf = preflight.run_all(
        tasklist=lambda: "Image Name: UV4.exe\nPID: 1\n",
        attempt=lambda: (True, "probe acquired"),
    )
    assert not pf.ok
    assert "uv4_resident" in pf.failed


def test_run_all_blocks_on_probe_held():
    pf = preflight.run_all(
        tasklist=lambda: "INFO: No tasks are running which match the specified criteria.",
        attempt=lambda: (False, "no probe enumerated (held by another process)"),
    )
    assert not pf.ok
    assert "cmsis_dap_holder" in pf.failed


def test_run_all_reports_both_failures():
    pf = preflight.run_all(
        tasklist=lambda: "Image Name: UV4.exe\nPID: 1\n",
        attempt=lambda: (False, "no probe enumerated"),
    )
    assert not pf.ok
    assert set(pf.failed) == {"uv4_resident", "cmsis_dap_holder"}
    assert "BLOCKED" in pf.report()
    assert "uv4_resident" in pf.report()
    assert "cmsis_dap_holder" in pf.report()