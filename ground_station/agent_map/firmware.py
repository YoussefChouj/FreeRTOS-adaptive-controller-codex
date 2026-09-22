"""ctags-style scan of firmware C for top-level functions, globals and struct
members. No compiler involved — a lightweight tokenising parser is enough to
keep the agent map honest (definition file:line comes from here or DWARF).

Only firmware-owned files are scanned: the dirs in ``FIRMWARE_DIRS``.
FreeRTOS/ and stm32_lib/ are excluded by not being in that set.
"""
from __future__ import annotations

import re
from pathlib import Path

from .paths import ROOT

_ID_RE = re.compile(r"[A-Za-z_]\w*")
# `}TypeName;` — a typedef/struct/union/enum alias trailing its definition body.
_ALIAS_RE = re.compile(r"}\s*([A-Za-z_]\w*)\s*;")

_TOKEN_RE = re.compile(
    r"(?P<id>[A-Za-z_]\w*)"
    r"|(?P<punct>[{}();,\[\]=*])"
    r"|(?P<ws>\s+)"
    r"|(?P<other>.)",
    re.S,
)

# C tokens that may appear in a type / qualifier run and must not be recorded
# as a declared name.
_TYPE_KEYWORDS = {
    "void", "char", "int", "float", "double", "short", "long", "signed",
    "unsigned", "const", "volatile", "static", "extern", "register", "auto",
    "inline", "struct", "union", "enum", "typedef", "_Bool", "restrict",
    "__attribute__", "__packed", "__SIO_TYPE", "_IOREG",
    "uint8_t", "uint16_t", "uint32_t", "int8_t", "int16_t", "int32_t",
    "size_t", "bool",
}


def _strip_comments(text: str) -> str:
    # Firmware keeps numeric literals in string constants; assume no `//`
    # or `/*` sequence appears inside a string literal. Preserve the number of
    # newlines so reported line numbers stay correct. Preprocessor directives
    # (`#include`, `#define`, `#if`) are masked out (spaces) so they are never
    # mistaken for declarations.
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            out.append("\n" * text.count("\n", i, j))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            if j == -1:
                j = n
            out.append(" " * (j - i))
            i = j
        else:
            out.append(text[i])
            i += 1
    stripped = "".join(out)
    # mask preprocessor directive lines (keep the newline for line numbers)
    lines = []
    for ln in stripped.splitlines(keepends=True):
        body = ln.lstrip(" \t")
        if body.startswith("#"):
            keep_nl = "\n" if ln.endswith("\n") else ""
            lines.append(" " * len(ln.rstrip("\n")) + keep_nl)
        else:
            lines.append(ln)
    return "".join(lines)


def _tokens(code: str):
    return [(m.group(0), m.start()) for m in _TOKEN_RE.finditer(code)
            if m.lastgroup != "ws"]


def _line_of(code: str, start: int) -> int:
    return code.count("\n", 0, start) + 1


def _scan_declarators(seg):
    """Declared name in one declarator segment.

    The declared name is the LAST non-type identifier: ``CtrlerTypeDef Ctrler``
    -> ``Ctrler``, ``unsigned int cnt_h`` -> ``cnt_h``, ``float Throttle_out``
    -> ``Throttle_out``.
    """
    name = None
    for t in seg:
        if t in ("*", "[", "]", "(", ")"):
            continue
        if t in _TYPE_KEYWORDS:
            continue
        if _ID_RE.fullmatch(t):
            name = t
    return [name] if name else []


def _is_compound_type(seg):
    """True if a declaration's type-run references a typedef'd / struct type.

    A declaration like ``PIDTypeDef gyroxPID;`` carries two non-base
    identifiers (the typedef type and the name) whereas ``float e;`` /
    ``uint8_t flag;`` carry only the name. So a member is "compound" iff it
    holds at least two non-base identifiers -> we keep those (struct-typed
    members such as gyroxPID) and drop scalar fields."""
    non_base = 0
    for t in seg:
        if t in {"*", "[", "]", "(", ")"}:
            continue
        if t in _TYPE_KEYWORDS:
            continue
        if _ID_RE.fullmatch(t):
            non_base += 1
    return non_base >= 2


def _parse_declaration(toks, i, code, out, kind):
    """Parse one declaration statement starting at token i.

    ``kind`` is ``"member"`` (inside a struct body) or ``None`` (top level).
    Pushes records onto ``out`` and returns the index of the next unread token.
    """
    n = len(toks)
    stmt_line = _line_of(code, toks[i][1])
    seg: list[str] = []
    segments: list[list[str]] = []   # (first, other) declarator segments
    first_seg = True
    typedef_decl = False
    is_function = False

    def flush():
        nonlocal seg, first_seg
        if is_function:
            return
        if first_seg:
            segments.append(list(seg))
            first_seg = False
        else:
            segments.append(list(seg))
        seg = []

    def emit():
        if is_function:
            return
        if typedef_decl or not segments:
            return
        if kind == "member" and not _is_compound_type(segments[0]):
            return  # scalar struct field: not an agent-map symbol
        for s in segments:
            for nm in _scan_declarators(s):
                out.append({
                    "kind": "member" if kind == "member" else "global",
                    "name": nm,
                    "line": stmt_line,
                })

    while i < n:
        t = toks[i][0]
        if t == "(":
            head = _scan_declarators(seg)
            if head and not typedef_decl:
                # function declarator: name is last head identifier, params follow
                is_function = True
                out.append({"kind": "function", "name": head[-1],
                            "line": stmt_line})
                i = _match_paren(toks, i)
                if i < n and toks[i][0] == "{":
                    i = _skip_block(toks, i)  # definition: skip the body
                else:
                    while i < n and toks[i][0] != ";":
                        i += 1
                return i
            i = _match_paren(toks, i)
            continue
        if t in (",", ";"):
            flush()
            if t == ";":
                emit()
                return i + 1
            i += 1
            continue
        if t == "=":
            flush()  # declared name(s) precede the initialiser
            i += 1
            depth = 0
            while i < n:
                c = toks[i][0]
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                elif c == "," and depth == 0:
                    break
                elif c == ";" and depth == 0:
                    emit()
                    return i + 1
                i += 1
            continue
        if t == "[":
            i += 1
            continue
        if t == "typedef":
            typedef_decl = True
        seg.append(t)
        i += 1
    emit()
    return i


def _match_paren(toks, i):
    depth = 0
    while i < len(toks):
        t = toks[i][0]
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _skip_block(toks, i):
    """toks[i] == '{'; return index after matching '}'."""
    depth = 0
    while i < len(toks):
        c = toks[i][0]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def scan_file(path: Path):
    """Return list of {kind, name, file, line} for a single file."""
    code = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    toks = _tokens(code)
    n = len(toks)
    out: list[dict] = []

    brace_stack: list[bool] = []  # True = that '{' opened a struct body
    struct_flag = False
    after_colon = True

    i = 0
    while i < n:
        t, pos = toks[i][0], toks[i][1]
        if t == "{":
            brace_stack.append(struct_flag)
            struct_flag = False
            after_colon = True
            i += 1
            continue
        if t == "}":
            if brace_stack:
                brace_stack.pop()
            after_colon = True
            i += 1
            continue
        if t == ";":
            after_colon = True
            i += 1
            continue
        if after_colon:
            in_struct = bool(brace_stack and brace_stack[-1])
            if not in_struct and t in ("typedef", "struct", "union", "enum",
                                       "extern"):
                # do not parse struct/union/enum definitions as declarations;
                # let the immediate '{' push the struct body so members parse
                after_colon = False
                struct_flag = True
                i += 1
                continue
            if in_struct:
                # struct body: a member declaration
                i = _parse_declaration(toks, i, code, out, "member")
                after_colon = True
                continue
            if len(brace_stack) == 0:
                # top level: a global or function definition
                i = _parse_declaration(toks, i, code, out, None)
                after_colon = True
                continue
            # inside a function body: skip (locals / control flow are not
            # agent-map symbols). Just advance; `;`/`}` will re-arm the
            # statement boundary.
            i += 1
            continue
        if t in ("struct", "union", "enum"):
            struct_flag = True
        i += 1
    return out


def _collect_aliases() -> set[str]:
    """Type aliases across all firmware files (``}TypeName;``)."""
    aliases: set[str] = set()
    for pkg in ("API", "TASK", "BSP", "USER", "Global_file"):
        base = ROOT / pkg
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() not in (".c", ".h") or not p.is_file():
                continue
            code = _strip_comments(
                p.read_text(encoding="utf-8", errors="replace"))
            aliases.update(m.group(1) for m in _ALIAS_RE.finditer(code))
    return aliases


def scan_firmware() -> dict[str, dict]:
    """Map: name -> {kind, file, line}. First occurrence wins (sorted per dir
    so API/ comes before TASK/ before Global_file/). Type aliases are dropped."""
    aliases = _collect_aliases()
    result: dict[str, dict] = {}
    for pkg in ("API", "TASK", "BSP", "USER", "Global_file"):
        base = ROOT / pkg
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() not in (".c", ".h") or not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            for rec in scan_file(p):
                name = rec["name"]
                if not name or name in aliases or name in result:
                    continue
                result[name] = {
                    "kind": rec["kind"],
                    "file": rel,
                    "line": rec["line"],
                }
    return result