"""The routing table is only trustworthy if it stays complete and legal, so these are its invariants.

Run with the other tools tests (tools/check.sh c-pytest). No network, no compiler.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import check_routes  # noqa: E402


def test_step_names_are_real():
    """Every step in the table is a step check.sh knows (a typo would silently drop a gate)."""
    rows = check_routes.read_routes()          # raises on an unknown name
    assert rows, "route table is empty"


def test_top_level_dirs_are_routed():
    """A new top-level directory with tracked files must have a row, or its changes run with doc steps only."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    tops = {line.split("/", 1)[0] for line in out.stdout.splitlines() if line.strip()}
    rows = check_routes.read_routes()
    unrouted = sorted(
        t for t in tops
        if "." not in t                      # dotfiles: .github, .gitignore, ...
        and not any(check_routes.match(f"{t}/x", spec) for spec, _ in rows)
    )
    assert not unrouted, f"no route row for top-level dir(s): {unrouted} (add one to tools/check_routes.txt)"


def test_glob_does_not_cross_separator():
    """fnmatch lets * span '/', which would make API/* also match API/tests/deep/x.c. Paths are exact-segment."""
    assert check_routes.match("API/pid.c", "API/*.c")
    assert not check_routes.match("API/tests/x.c", "API/*.c")


def test_double_star_matches_any_depth():
    assert check_routes.match("API/pid.c", "API/**")
    assert check_routes.match("API/sub/deep/pid.c", "API/**")
    assert not check_routes.match("BSP/bsp.c", "API/**")


def test_tracked_bash_scripts_are_lf():
    """CRLF in a .sh that WSL bash executes is the exit-127 class of bug (a temp script once ate a whole
    commit). .gitattributes pins LF on *.sh; this fails the moment a CRLF shell script is committed.

    Only *.sh: Python reads CRLF source correctly, so pinning .py would renormalise hundreds of files for no
    behavioural gain."""
    import glob
    bad = [str(Path(p).relative_to(REPO)).replace("\\", "/") for p in glob.glob(str(REPO / "*.sh"))
           if b"\r\n" in Path(p).read_bytes()]
    assert not bad, f"CRLF line endings in tracked shell scripts: {bad[:5]} (see .gitattributes)"