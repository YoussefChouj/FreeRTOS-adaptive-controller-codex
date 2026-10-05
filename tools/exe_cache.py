"""Content-keyed cache of the host-built test exes (tools/host_tests.py and the pytest suites that compile C).

On this host the antivirus holds the FIRST run of every new .exe (measured: pid_guards 27-50 s first run, 0.47 s
after; a byte-identical copy under a new name is slow again), and its scans also slow the processes started after
them. So a build is kept at a stable path named by a sha256 of gcc, `gcc --version`, the arguments and every file
`gcc -MM` lists (path and bytes): an unchanged build reruns its already-scanned exe. The build's stderr is kept
beside the exe and returned on a hit, so a caller's warning check still sees it.
EXE_CACHE=0 (or HOST_TESTS_CACHE=0) = a fresh build in the caller's directory every time.
"""
from __future__ import annotations

import functools
import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / ".cache" / "exe"
ON = os.environ.get("EXE_CACHE", os.environ.get("HOST_TESTS_CACHE", "1")) != "0"


class Build(NamedTuple):
    ok: bool
    exe: Path
    built: bool       # False = cache hit
    stderr: str


@functools.lru_cache(maxsize=None)
def _ident(gcc: str) -> str:
    return gcc + "\0" + subprocess.run([gcc, "--version"], capture_output=True, text=True).stdout


def _norm(text: str, temp: dict[str, str]) -> str:
    text = text.replace("\\", "/")
    for path, mark in temp.items():
        text = text.replace(path, mark)
    return text


def key(gcc: str, args: list[str], link: list[str], temp: dict[str, str]) -> str:
    """Hash of gcc, the arguments and every file they compile; "" if gcc -MM fails (the build then reports it).
    temp maps a per-run directory (posix) to a placeholder, so a fresh temp copy of the same files hits."""
    dep = subprocess.run([gcc, *args, "-MM"], cwd=REPO, capture_output=True, text=True)
    if dep.returncode != 0:
        return ""
    h = hashlib.sha256((_ident(gcc) + "\0" + _norm("\0".join([*args, *link]), temp)).encode())
    for tok in re.split(r"(?<!\\)\s+", dep.stdout.replace("\\\n", " ")):
        if tok and not tok.endswith(":"):
            path = tok.replace("\\ ", " ")
            h.update(("\0" + _norm(path, temp) + "\0").encode())
            h.update((REPO / path).read_bytes())
    return h.hexdigest()[:16]


def build(gcc: str, name: str, args: list[str], fallback: Path, temp: dict[Path, str] | None = None,
          link: tuple[str, ...] = ("-lm",)) -> Build:
    """Build `gcc args link -o exe`, or reuse the cached exe of an identical build. name must be unique per
    build target across all callers (it names the cache file); fallback is the directory used without a key."""
    marks = {p.as_posix(): m for p, m in (temp or {}).items()}
    k = key(gcc, args, list(link), marks) if ON else ""
    exe = CACHE / f"{name}_{k}.exe" if k else fallback / f"{name}.exe"
    err = exe.with_suffix(".err")
    if k and exe.exists():
        return Build(True, exe, False, err.read_text() if err.exists() else "")
    out = exe.with_name(f"{name}_{os.getpid()}.tmp.exe") if k else exe
    out.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run([gcc, *args, *link, "-o", str(out)], cwd=REPO, capture_output=True, text=True)
    if res.returncode != 0 or not k:
        return Build(res.returncode == 0, exe, True, res.stderr)
    err.write_text(res.stderr)
    try:
        os.replace(out, exe)
    except PermissionError:                         # Windows: another process built and is running the same exe
        out.unlink(missing_ok=True)
    for old in CACHE.glob(f"{name}_*.*"):           # this target's older builds (exact name: mrac_inputs != _rbf)
        if old.stem != exe.stem and re.fullmatch(re.escape(name) + r"_[0-9a-f]{16}\.(exe|err)", old.name):
            try:
                old.unlink()
            except OSError:                         # still running in another process; the next build retries
                pass
    return Build(True, exe, True, res.stderr)
