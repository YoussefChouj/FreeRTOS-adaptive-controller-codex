"""Pre-flight interlocks that gate every probe-touching operation.

The CMSIS-DAP dongle on this bench is single-owner — exactly one process may
hold the SWD interface at a time. Three documented candidates hold it:

* ``UV4.exe`` (uVision GUI debug session) — historically the most common.
* Any other pyOCD-derived tool (e.g. a livewatch ``watch`` or ``log`` run).
* The flashtool itself, which uses ``SwdCmsisDap`` for the post-flash
  verification step.

If a competing holder is alive when we try to attach, the attach fails with
"no probe found" or ``TransferError`` mid-read. Either error mode wastes
the operator's time and, worse, looks identical to "the probe is broken."

These checks refuse the pipeline before any probe operation, with a name for
who is most likely holding the interface. They do NOT write a competing
lock file — pyOCD's own ``ConnectHelper`` is the single-owner arbiter; we
just observe whether the interface is acquirable.

Both checks are wrapped as injectable callables (``_tasklist``, ``_pyocd``)
so unit tests can drive them with synthetic subprocess output without
actually spawning ``tasklist`` or attaching to hardware.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class PreflightError(RuntimeError):
    """A pre-flight check refused the pipeline; carries a human-readable cause."""

    def __init__(self, message: str, holder: str | None = None):
        super().__init__(message)
        self.holder = holder    # best guess at what was holding the resource


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    failed: list[str]               # names of the checks that failed
    holders: dict[str, str]         # check name -> holder description

    def report(self) -> str:
        if self.ok:
            return "[preflight] OK — no resident UV4, no other holder of CMSIS-DAP"
        head = "[preflight] BLOCKED — refusing to proceed"
        lines = [head]
        for name in self.failed:
            lines.append(f"    ! {name}: {self.holders.get(name, '?')}")
        return "\n".join(lines)


# ---- check 1: resident UV4.exe --------------------------------------------

# `tasklist /FI "IMAGENAME eq UV4.exe"` output that includes "INFO: No tasks
# are running which match the specified criteria." — used by the offline test
# to fake an empty match without spawning tasklist.
_TASKLIST_NO_MATCH = "INFO: No tasks are running which match the specified criteria."


def _default_tasklist() -> str:
    """Run ``tasklist`` filtered to UV4.exe; return its stdout text."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq UV4.exe", "/FO", "LIST"],
        capture_output=True, text=True, check=False,
    )
    return out.stdout or ""


def _parse_tasklist(text: str) -> list[tuple[str, str]]:
    """``tasklist /FO LIST`` -> ``[(pid, image_name), ...]`` of UV4 entries.

    The /FO LIST shape is:
        Image Name:     UV4.exe
        PID:            12345
        ...

    Empty match returns ``INFO: No tasks are running which match ...`` and
    we return ``[]``. Lines are matched case-insensitively.
    """
    if _TASKLIST_NO_MATCH in text:
        return []
    pid = None
    name = None
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("image name:"):
            name = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("pid:"):
            pid = stripped.split(":", 1)[1].strip()
            if name:
                out.append((pid, name))
                pid = name = None
    return out


def uv4_resident(tasklist: Callable[[], str] = _default_tasklist) -> PreflightResult:
    """Is a uVision GUI instance alive? Used by the pipeline; falsifiable."""
    text = tasklist()
    rows = _parse_tasklist(text)
    if rows:
        pid, name = rows[0]
        return PreflightResult(
            ok=False, failed=["uv4_resident"],
            holders={"uv4_resident": f"{name} (PID {pid}) — close uVision (Stop Debug Session)"},
        )
    return PreflightResult(ok=True, failed=[], holders={})


# ---- check 2: CMSIS-DAP already held --------------------------------------

# We acquire a transient attach-mode pyOCD session and immediately drop it.
# If the session can't be opened, another process is the single-owner. If it
# opens cleanly, the dongle was free at the moment of the check; the
# operator must still respect the single-owner rule between this check and
# the actual probe operation (we cannot atomically reserve it for the
# entire pipeline).
def _default_pyocd_attempt() -> tuple[bool, str]:
    """Try to open a transient attach-mode session; return (acquired, message)."""
    if not (shutil.which("pyocd") or Path(".venv/Scripts/pyocd.exe").exists()):
        return False, "pyocd not on PATH (cannot probe CMSIS-DAP holder)"
    try:
        from pyocd.core.helpers import ConnectHelper
        session = ConnectHelper.session_with_chosen_probe(
            options={
                "target_override": "cortex_m",
                "connect_mode": "attach",
                "resume_on_disconnect": False,
            }
        )
    except Exception as exc:
        return False, f"pyocd session creation failed: {exc}"
    if session is None:
        return False, "no CMSIS-DAP probe enumerated (probe unplugged, or held by another process)"
    try:
        session.open()
        return True, "probe acquired"
    except Exception as exc:
        return False, f"pyocd open failed: {exc}"
    finally:
        try:
            session.close()
        except Exception:
            pass


def cmsis_dap_holder(attempt: Callable[[], tuple[bool, str]] = _default_pyocd_attempt
                     ) -> PreflightResult:
    """Is the CMSIS-DAP interface already held by another process?"""
    acquired, msg = attempt()
    if acquired:
        return PreflightResult(ok=True, failed=[], holders={})
    # A common false-positive here is "probe unplugged" — distinguish it
    # from "probe held" so the operator can act on the right cause.
    holder = "another process (close livewatch / uVision debug session)" \
        if "held" in msg or "no probe enumerated" in msg or "another" in msg \
        else "probe unreachable"
    return PreflightResult(
        ok=False, failed=["cmsis_dap_holder"],
        holders={"cmsis_dap_holder": f"{holder} — {msg}"},
    )


# ---- combined entry point -------------------------------------------------

def run_all(tasklist: Callable[[], str] = _default_tasklist,
            attempt: Callable[[], tuple[bool, str]] = _default_pyocd_attempt
            ) -> PreflightResult:
    """Run every pre-flight check; refuse if any failed."""
    results = [uv4_resident(tasklist), cmsis_dap_holder(attempt)]
    failed: list[str] = []
    holders: dict[str, str] = {}
    for r in results:
        if not r.ok:
            failed.extend(r.failed)
            holders.update(r.holders)
    return PreflightResult(ok=not failed, failed=failed, holders=holders)