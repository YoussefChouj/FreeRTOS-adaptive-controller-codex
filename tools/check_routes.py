"""Which check.sh steps a branch's diff requires. Used by `bash tools/check.sh --changed`.

Prints the step names the files changed against the base require, one line of space-separated names, and nothing
else. The table is tools/check_routes.txt (one row: <git pathspec> <space-separated steps>). Rules:

  * Every changed file is tested against every row; the result is the union of the steps, in the order given in
    check.sh, so a file with no matching row costs nothing.
  * doc-paths and doc-budget are always required and added here: a stale path in a doc or a blown line cap fails
    any change, and both are ~2 s.
  * Unrouted files are a gap, not a pass: tools/check_routes.txt says so in a comment, and `tools/test_check_routes.py`
    fails when a top-level directory has no row, so a new tree cannot ship unrouted.

Base is $CHECK_BASE, else origin/main...HEAD; `--staged` routes the staged files instead (git diff --cached, what
.githooks/pre-commit uses). Exits non-zero, printing nothing on stdout, when git or the table fails.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROUTES = REPO / "tools" / "check_routes.txt"

# Step order from check.sh's ALL; the union is emitted in this order, not in table order.
STEP_ORDER = [
    "host-tests", "mrac-equiv", "all-loop-equiv", "c-pytest", "sil-smoke", "clang-tidy", "float", "row-meta", "fw-lint",
    "stack", "doc-paths", "arm-syntax", "doc-budget",
]
ALWAYS = {"doc-paths", "doc-budget"}

VALID_STEPS = set(STEP_ORDER)


def read_routes() -> list[tuple[str, set[str]]]:
    """(glob, steps) per row. Blank lines and '#' comments are skipped."""
    rows = []
    for raw in ROUTES.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        spec, _, names = line.partition(" ")
        steps = {s for s in names.split() if s}
        unknown = steps - VALID_STEPS
        if unknown:
            raise SystemExit(f"check_routes.txt: {spec!r} names unknown step(s): {' '.join(sorted(unknown))}")
        rows.append((spec.strip(), steps))
    if not rows:
        raise SystemExit("check_routes.txt has no rows")
    return rows


def base_ref() -> list[str]:
    """The ref the branch is diffed against."""
    base = os.environ.get("CHECK_BASE")
    if base:
        return base.split()
    try:
        subprocess.run(["git", "rev-parse", "--verify", "origin/main"], cwd=REPO,
                       capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("check_routes: origin/main not reachable; set CHECK_BASE=<ref> or pass one")
    return ["origin/main...HEAD"]


def changed_files(base: list[str]) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", *base], cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"check_routes: git diff failed: {out.stderr.strip()}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def match(path: str, spec: str) -> bool:
    """Git-pathspec-lite glob over '/'-separated segments: '*' stays inside one segment, '**' spans any depth.

    fnmatch would let '*' cross '/', so 'API/*.c' would also match 'API/tests/x.c' and route a test file as if it
    were a top-level source. tools/test_check_routes.py pins both halves.
    """
    return re.fullmatch(_translate(spec), path) is not None


@lru_cache(maxsize=None)
def _translate(spec: str) -> str:
    out = []
    i = 0
    while i < len(spec):
        ch = spec[i]
        if spec.startswith("**/", i):
            out.append("(?:[^/]+/)*")           # any number of leading segments
            i += 3
        elif spec.startswith("/**", i) and len(spec) == i + 3:
            out.append("(?:/.*)?")               # the directory itself and everything under it
            i += 3
        elif ch == "*":
            out.append("[^/]*")                  # one segment, never across a '/'
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def route(files: list[str], rows: list[tuple[str, set[str]]]) -> tuple[set[str], list[str]]:
    """(steps the files require, files no row matched). doc-paths and doc-budget are always in the steps."""
    need = set(ALWAYS)
    unrouted = []
    for path in files:
        hits = [steps for spec, steps in rows if match(path, spec)]
        for steps in hits:
            need |= steps
        if not hits:
            unrouted.append(path)
    return need, unrouted


def required_for_diff(repo: Path = REPO, base: list[str] | None = None) -> set[str]:
    """The steps the diff against `base` requires, as a set. Shared by `check.sh --changed` and the push guard."""
    out = subprocess.run(["git", "diff", "--name-only", *(base or base_ref())],
                         cwd=repo, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git diff failed: {out.stderr.strip()}")
    files = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return route(files, read_routes())[0]


def main(argv: list[str]) -> int:
    rows = read_routes()
    files = changed_files(["--cached"] if "--staged" in argv else base_ref())
    need, unrouted = route(files, rows)
    if unrouted:
        # Not an error: a README or a .gitignore row legitimately has no step. tools/test_check_routes.py is what
        # guarantees every top-level directory is routed, so silence here stays visible in the gate.
        print(f"check_routes: no row for {len(unrouted)} file(s) (first: {unrouted[0]}); doc steps only",
              file=sys.stderr)
    print(" ".join(s for s in STEP_ORDER if s in need), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
