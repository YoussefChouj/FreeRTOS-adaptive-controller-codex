#!/usr/bin/env python3
"""Check the metadata of the firmware tunable tables (*_ROW macros, docs/firmware-table-pattern.md).

Every parameter of a `#define FOO_ROW(a, b, ...)` needs one line in the legend comment above it:

    @a   unit   [min, max]   description

and every literal in every `FOO_ROW(...)` row must lie in [min, max]. Exit 1 on a missing, extra or
misnamed line, or on a value out of range. `--json` prints every cell (file, table, param, unit, min, max,
value, description): the machine-readable parameter list for a ground station.

Assignment-form tables (ASSIGN, statements in an init function) work the same, with leading key arguments that
are not values: MRAC_BASIS(axis, i, g, lim, t, low) has a legend line per value parameter (@g ... @low), and
MRAC_SET(field, p, r, y, z) one per row (@<field>) that bounds all four axis values. A value may be an object-like
#define of the file or of a header it includes; with several definitions (#if branches) each one is checked.

    python tools/row_meta.py [--json] [files...]      default: API/*.c TASK/*.c
"""
from __future__ import annotations

import ast
import json
import operator
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# assignment-form table -> (leading key arguments, legend per row named by the first argument)
ASSIGN = {"MRAC_SET": (1, True), "MRAC_BASIS": (2, False)}
DEFINE = re.compile(r"#define\s+(\w+_ROW|%s)\(([^)]*)\)" % "|".join(ASSIGN))
CONST = re.compile(r"^[ \t]*#define[ \t]+(\w+)[ \t]+([-+.\w*/() \t]+?)[ \t]*(?://.*|/\*.*)?$", re.M)
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
META = re.compile(r"^[ \t*]*@(\w+)\s+(\S+)\s+\[\s*([^,\]]+?)\s*,\s*([^\]]+?)\s*\]\s*(.*?)\s*(?:\*/)?$", re.M)
SUFFIX = re.compile(r"(\d(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)[fFuUlL]+\b")
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.USub: operator.neg, ast.UAdd: operator.pos}


def number(expr: str) -> float:
    """Value of a C constant expression made of literals and + - * / (1.0e6f, 0.15f*0.6f, -2)."""
    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
            return OPS[type(n.op)](ev(n.operand))
        raise ValueError(expr)
    return ev(ast.parse(SUFFIX.sub(r"\1", expr.strip()), mode="eval").body)


def split_args(text: str, start: int) -> tuple[list[str], int]:
    """Arguments of the call whose '(' is at text[start]; returns (args, index after ')')."""
    depth, args, cur = 0, [], ""
    for i in range(start, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
            if depth == 1:
                continue
        elif c == ")":
            depth -= 1
            if depth == 0:
                args.append(cur)
                return [a.strip() for a in args], i + 1
        elif c == "," and depth == 1:
            args.append(cur)
            cur = ""
            continue
        cur += c
    raise ValueError("unbalanced parentheses")


def read(path: Path) -> str:
    return path.read_bytes().decode("latin-1").replace("\r", "")


def constants(path: Path, text: str) -> dict[str, list[str]]:
    """Object-like #defines of the file and of the local headers it includes: name -> every definition."""
    out: dict[str, list[str]] = {}
    headers = [path.parent / h for h in re.findall(r'#include\s+"([^"]+)"', text)]
    for src in [text] + [read(h) for h in headers if h.exists()]:
        for name, expr in CONST.findall(src):
            out.setdefault(name, []).append(expr)
    return out


def check_file(path: Path, cells: list[dict]) -> list[str]:
    raw = read(path)
    text = re.sub(r"\\\n", "  ", raw)                                   # join macro continuation lines
    code = COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)  # same offsets, comments blanked
    rel = path.relative_to(REPO).as_posix()
    consts = constants(path, raw)
    errors = []
    for d in DEFINE.finditer(code):
        name, params = d.group(1), [p.strip() for p in d.group(2).split(",")]
        keys, per_row = ASSIGN.get(name, (0, False))
        meta = {}
        for c in reversed([c for c in COMMENT.finditer(text) if c.end() <= d.start()]):
            found = META.findall(c.group(0))
            if found:
                meta = {m[0]: m for m in found}
                break
        rows = [split_args(code, call.end() - 1)[0] for call in re.finditer(r"\b%s\s*\(" % name, code)
                if call.start() != d.start() + len("#define ")]
        expect = {r[0] for r in rows} if per_row else set(params[keys:])
        if set(meta) != expect:
            what = "the rows" if per_row else "the parameters"
            errors.append(f"{rel}: {name}: legend @lines {sorted(meta)} do not match {what} {sorted(expect)}")
            continue
        for args in rows:
            if len(args) != len(params):
                errors.append(f"{rel}: {name}: row with {len(args)} values, {len(params)} parameters")
                continue
            for p, a in zip(params[keys:], args[keys:]):
                param = args[0] if per_row else p
                _, unit, lo, hi, desc = meta[param]
                try:
                    lo_v, hi_v = number(lo), number(hi)
                    vals = [number(e) for e in consts[a]] if a in consts else [number(a)]
                except (ValueError, SyntaxError):
                    errors.append(f"{rel}: {name}.{param}: not a constant: {a!r}")
                    continue
                for v in vals:
                    cell = {"file": rel, "table": name, "param": param, "unit": unit, "min": lo_v, "max": hi_v,
                            "value": v, "description": desc}
                    if keys:
                        cell["row"] = ",".join(args[:keys]) if not per_row else p
                    cells.append(cell)
                    if not lo_v <= v <= hi_v:
                        shown = f"{a} ({v:g})" if a in consts else a
                        errors.append(f"{rel}: {name}.{param} = {shown} outside [{lo}, {hi}]")
    return errors


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    files = [Path(a).resolve() for a in argv if a != "--json"] or sorted(
        list((REPO / "API").glob("*.c")) + list((REPO / "TASK").glob("*.c")))
    cells: list[dict] = []
    errors = [e for f in files for e in check_file(f, cells)]
    if as_json:
        print(json.dumps(cells, indent=1))
    for e in errors:
        print(f"FAIL {e}", file=sys.stderr if as_json else sys.stdout)
    tables = len({(c["file"], c["table"]) for c in cells})
    print(f"row-meta: {tables} tables, {len(cells)} cells in range, {len(errors)} error(s)",
          file=sys.stderr if as_json else sys.stdout)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
