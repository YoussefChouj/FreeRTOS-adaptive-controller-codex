"""Find assignment sites (writers) for each firmware symbol.

Regex over firmware C for LHS identifiers::

    <name> ( [..] | .member )*  [ + - * / | & ]? =

Records ``file:line`` for every assignment whose left-hand base symbol is
<name>. clangd references are a later upgrade; this syntactic pass is the
step-1 contract.
"""
from __future__ import annotations

import re

from .paths import glob_firmware

# base( [..] | .ident )*  then optional compound-ops + '='
_ASGN = re.compile(
    r"\b([A-Za-z_]\w*)(?:(?:\s*\[[^\]]*\])|(?:\.[A-Za-z_]\w*))*\s*[+\-*/|&]?=")

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_SEG = re.compile(r"[A-Za-z_]\w*")


def load_writers() -> dict[str, list[str]]:
    """Return {symbol: ["file:line", ...]}.

    The LHS ``Ctrler.gyroxPID.FB =`` is indexed under its base (``Ctrler``) and
    under every dotted member name (``gyroxPID``, ``FB``) so struct members get
    their writers too.
    """
    out: dict[str, list[str]] = {}
    for path in glob_firmware():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        code = _COMMENT.sub(" ", text)
        rel = _rel(path)
        for m in _ASGN.finditer(code):
            lhs = m.group(0)[: m.end()]  # up to and incl '='
            # identifiers on the LHS (base + dotted members, not part of '='tgt)
            for tm in _SEG.finditer(lhs.split("=", 1)[0]):
                name = tm.group(0)
                line = code.count("\n", 0, m.start()) + 1
                out.setdefault(name, []).append(f"{rel}:{line}")
    return out


def _rel(path):
    from .paths import ROOT
    return path.relative_to(ROOT).as_posix()


def _dedupe(writers: dict[str, list[str]]) -> dict[str, list[str]]:
    for k in writers:
        seen = set()
        ded = []
        for entry in writers[k]:
            if entry not in seen:
                seen.add(entry)
                ded.append(entry)
        writers[k] = ded
    return writers