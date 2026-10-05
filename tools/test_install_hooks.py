"""tools/install-hooks.sh, run on a throwaway git repository (never on this one: hooks are shared by every worktree)."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "install-hooks.sh"
# The PATH bash (Git Bash on Windows). A bare "bash" goes through CreateProcess, which searches System32 first and
# starts WSL bash there: a Linux git, and 5-20 s to boot WSL after it idles (measured 2026-10-05).
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(shutil.which("git") is None or BASH is None, reason="git/bash missing")


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tools").mkdir()
    shutil.copy2(SCRIPT, tmp_path / "tools" / "install-hooks.sh")
    return tmp_path


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "tools/install-hooks.sh", *args], cwd=repo, capture_output=True, text=True)


def test_install_is_idempotent_and_removable(tmp_path):
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert _run(repo).returncode == 0 and _run(repo).returncode == 0
    text = hook.read_text()
    assert "# installed by tools/install-hooks.sh" in text and "tools/check.sh" in text
    assert "\r" not in text
    assert _run(repo, "--remove").returncode == 0 and not hook.exists()


def test_foreign_hook_is_kept(tmp_path):
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    res = _run(repo)
    assert res.returncode == 1 and "not replacing" in res.stderr
    assert _run(repo, "--remove").returncode == 0 and hook.read_text() == "#!/bin/sh\necho mine\n"
