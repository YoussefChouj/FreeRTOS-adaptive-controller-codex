"""The push guard's coverage rule, and install-hooks.sh, on throwaway repositories (never on this one).

The rule under test: a subset check.sh run may satisfy the guard only when it covered every step the branch's diff
requires. Without it, `bash tools/check.sh doc-paths` was enough to push a change to a flight-critical C file.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import agent_push_guard as guard  # noqa: E402

SCRIPT = REPO / "tools" / "install-hooks.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git missing")


# --- the coverage rule -------------------------------------------------------------

def test_scope_line_parses_to_the_steps_that_ran():
    ran = guard._steps_from_last_check(
        "CHECK SUBSET PASS 2026-10-20T05:03:07Z scope=row-meta fw-lint doc-paths doc-budget")
    assert ran == {"row-meta", "fw-lint", "doc-paths", "doc-budget"}


def test_full_run_counts_as_every_step():
    assert guard._steps_from_last_check("CHECK PASS 2026-10-20T05:03:07Z scope=full") == guard._KNOWN_STEPS


def test_unreadable_scope_is_refused_not_guessed():
    # The format check.sh wrote before it kept the separator: one run-together name proves nothing.
    assert guard._steps_from_last_check("CHECK SUBSET PASS x scope=row-metafw-lintdoc-paths") is None
    assert guard._steps_from_last_check("CHECK SUBSET PASS x") is None


def test_doc_only_run_cannot_cover_an_api_change():
    """The loophole this rule closes: firmware routed to host-tests/float/clang-tidy must not be pushable on a
    doc-only subset run."""
    api = {"host-tests", "float", "clang-tidy", "mrac-equiv", "row-meta", "fw-lint", "doc-paths", "doc-budget"}
    ran = guard._steps_from_last_check("CHECK SUBSET PASS x scope=doc-paths doc-budget")
    assert ran is not None
    assert not ran >= api, "a doc-only run must not satisfy a firmware diff"


def test_doc_only_run_does_cover_a_doc_change():
    ran = guard._steps_from_last_check("CHECK SUBSET PASS x scope=doc-paths doc-budget")
    assert ran >= {"doc-paths", "doc-budget"}


# --- install-hooks.sh -------------------------------------------------------------

def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / ".githooks").mkdir()
    shutil.copy2(SCRIPT, tmp_path / "tools" / "install-hooks.sh")
    for name in ("pre-commit", "pre-push"):
        shutil.copy2(REPO / ".githooks" / name, tmp_path / ".githooks" / name)
    return tmp_path


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "tools/install-hooks.sh", *args], cwd=repo, capture_output=True, text=True)


@pytest.mark.skipif(BASH is None, reason="bash missing")
def test_default_install_does_not_touch_git_config(tmp_path):
    """Review-then-activate is the default: installing must not wire core.hooksPath behind the operator's back."""
    repo = _repo(tmp_path)
    assert _run(repo).returncode == 0
    assert subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo,
                          capture_output=True).returncode != 0
    assert "core.hooksPath .githooks" in _run(repo).stdout


@pytest.mark.skipif(BASH is None, reason="bash missing")
def test_activate_sets_hooks_path_and_remove_unsets_it(tmp_path):
    repo = _repo(tmp_path)
    assert _run(repo, "--activate").returncode == 0
    got = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo,
                         capture_output=True, text=True).stdout.strip()
    assert got == ".githooks"
    assert subprocess.run(["git", "config", "--get", "tracked-hooks.installed"], cwd=repo,
                          capture_output=True).returncode == 0
    assert _run(repo, "--remove").returncode == 0
    assert subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=repo,
                          capture_output=True).returncode != 0
    assert subprocess.run(["git", "config", "--get", "tracked-hooks.installed"], cwd=repo,
                          capture_output=True).returncode != 0


@pytest.mark.skipif(BASH is None, reason="bash missing")
def test_legacy_mode_writes_into_the_git_hooks_dir(tmp_path):
    repo = _repo(tmp_path)
    assert _run(repo, "--legacy").returncode == 0
    assert (repo / ".git" / "hooks" / "pre-commit").exists()
    assert _run(repo, "--remove").returncode == 0
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


@pytest.mark.skipif(BASH is None, reason="bash missing")
def test_foreign_hook_is_kept(tmp_path):
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    res = _run(repo, "--legacy")
    assert res.returncode == 1 and "not replacing" in res.stderr
    assert hook.read_text() == "#!/bin/sh\necho mine\n"