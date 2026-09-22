"""Tests for verify_task pure functions."""

import pytest
import re
import sys
import os

# Add parent dir to path so we can import verify_task
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verify_task import scan_log, check_paths, pick_tests, verdict, ignore


def test_scan_log_basic():
    """Test scan_log extracts (level, message) pairs from command lines."""
    log = "$ ls -la /tmp\n$ rm -rf /important\n$ echo 'hello'"
    result = scan_log(log)
    assert len(result) == 1  # Only the rm -rf line should FAIL
    assert result[0][0] == "FAIL"
    assert result[0][1] == "rm -rf /important"


def test_scan_log_ansi_stripping():
    """Test ANSI escape sequences are stripped."""
    log = "\x1b[31m$ ls -la\x1b[0m"
    result = scan_log(log)
    assert len(result) == 0  # No fail patterns in ls -la


def test_scan_log_ctrl_c_stripping():
    """Test Ctrl+C (\\x07) is stripped."""
    log = "$ ls -la\x07\n$ rm -rf /tmp"
    result = scan_log(log)
    assert len(result) == 1  # rm -rf should FAIL
    assert result[0][0] == "FAIL"


def test_scan_log_empty():
    """Test empty log returns empty list."""
    result = scan_log("")
    assert result == []


def test_scan_log_denied_warning():
    """Test denied/permission warnings are counted."""
    log = "$ some command failed: permission denied\n$ another error: access denied\n$ normal command"
    result = scan_log(log)
    assert len(result) == 1
    assert result[0][0] == "WARN"
    assert "denied/permission rejections: 2" in result[0][1]


def test_scan_log_loop_warning():
    """Test loop detection warning."""
    log = "$ ls\n$ ls\n$ ls\n$ ls\n$ ls"
    result = scan_log(log)
    assert len(result) == 1
    assert result[0][0] == "WARN"
    assert "loop detected: 5x $ ls" in result[0][1]


def test_check_paths_forbidden_prefix():
    """Test check_paths flags forbidden prefixes."""
    paths = ["API/", "TASK/", "BSP/", "USER/", "FreeRTOS/", "stm32_lib/", "OBJ/", ".git/"]
    findings = check_paths(paths, "")
    fail_count = sum(1 for level, _ in findings if level == "FAIL")
    assert fail_count >= len(paths)


def test_check_paths_modules_yaml():
    """Test check_paths flags modules.yaml."""
    findings = check_paths(["modules.yaml"], "")
    fail_count = sum(1 for level, _ in findings if level == "FAIL")
    assert fail_count >= 1


def test_check_paths_agent_keys():
    """Test check_paths flags agent-keys."""
    findings = check_paths(["agent-keys.txt"], "")
    fail_count = sum(1 for level, _ in findings if level == "FAIL")
    assert fail_count >= 1


def test_check_paths_outside_scope():
    """Test check_paths warns about paths outside task scope."""
    findings = check_paths(["some/random/path"], "task about something else")
    warn_count = sum(1 for level, _ in findings if level == "WARN")
    assert warn_count >= 1


def test_check_paths_in_scope():
    """Test check_paths does not warn when path is in task text."""
    findings = check_paths(["my_file.txt"], "this task is about my_file.txt")
    warn_count = sum(1 for level, _ in findings if level == "WARN")
    assert warn_count == 0


def test_pick_tests_ground_station():
    """Test pick_tests for ground_station package."""
    paths = ["ground_station/some_module.py"]
    result = pick_tests(paths)
    assert len(result) == 1
    assert result[0] == ["python", "-m", "pytest", "-q", "-x", "ground_station/some_module"]


def test_pick_tests_agent_ops():
    """Test pick_tests for .agent-ops Python files."""
    paths = [".agent-ops/verify_task.py"]
    result = pick_tests(paths)
    assert len(result) == 1
    assert result[0] == ["python", "-m", "pytest", "-q", ".agent-ops/tests"]


def test_pick_tests_node():
    """Test pick_tests for node test files."""
    paths = ["docs/dashboard-platform/shell/test.js"]
    result = pick_tests(paths)
    assert len(result) == 1
    assert result[0] == ["node", "ground_station/service/tests/node_harness.js"]


def test_pick_tests_node_service_tests():
    """Test pick_tests for ground_station/service/tests js files."""
    paths = ["ground_station/service/tests/test_helper.js"]
    result = pick_tests(paths)
    assert len(result) == 1
    assert result[0] == ["node", "ground_station/service/tests/node_harness.js"]


def test_pick_tests_dedup():
    """Test pick_tests deduplicates identical argv lists."""
    paths = ["ground_station/a.py", "ground_station/a.py"]
    result = pick_tests(paths)
    assert len(result) == 1
    assert result[0] == ["python", "-m", "pytest", "-q", "-x", "ground_station/a"]


def test_verdict_pass():
    """Test verdict returns PASS for no findings."""
    word, code = verdict([])
    assert word == "PASS"
    assert code == 0


def test_verdict_warn():
    """Test verdict returns WARN for warnings only."""
    word, code = verdict([("WARN", "test")])
    assert word == "WARN"
    assert code == 1


def test_verdict_fail():
    """Test verdict returns FAIL for any failure."""
    word, code = verdict([("FAIL", "test")])
    assert word == "FAIL"
    assert code == 2


def test_ignore_noise():
    """Test ignore() returns True for noise paths."""
    assert ignore(".agent-ops/tasks/something.md")
    assert ignore(".agent-ops/logs/something.out")
    assert ignore(".agent-ops/state.log")
    assert ignore(".agent-ops/inbox/something")
    assert ignore("logs/something")
    assert ignore("__pycache__/something.pyc")
    assert ignore("something.pyc")


def test_ignore_clean():
    """Test ignore() returns False for non-noise paths."""
    assert not ignore("src/main.py")
    assert not ignore("docs/readme.md")
    assert not ignore("README.md")

# ── worktree resolution ──────────────────────────────────────────────
#
# A `-Worktree` spawn runs the worker in `.worktrees/<task_id>`, and
# `.worktrees/` is gitignored. Everything below exists because `verify`
# used to do all of its work in the main checkout, so a worktree worker's
# edits were invisible to the change diff AND to the secrets scan, and the
# task verified clean no matter what it did.

import subprocess
import tempfile
import shutil

from verify_task import work_tree_for, worktree_base_tree, current_tree


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo_with_worktree():
    """A real repo with a real linked worktree at .worktrees/<id>.

    Real git, not a mock: the bug was entirely about what git does with a
    linked worktree (`.git` is a file, `.worktrees/` is ignored), so a mock
    would have reproduced my assumptions instead of git's behaviour.
    """
    root = tempfile.mkdtemp()
    _git(["init", "-q", "-b", "main"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    with open(os.path.join(root, ".gitignore"), "w") as f:
        f.write(".worktrees/\n")
    with open(os.path.join(root, "app.py"), "w") as f:
        f.write("original\n")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "base"], root)
    task_id = "20260923-120000"
    _git(["worktree", "add", "-q", ".worktrees/" + task_id, "-b",
          "agy/" + task_id], root)
    yield root, task_id
    try:
        shutil.rmtree(root)
    except OSError:
        pass


def test_worktree_is_gitignored_so_the_main_checkout_cannot_see_it(
        repo_with_worktree):
    """The premise. If this ever fails, the rest of these tests are moot."""
    root, task_id = repo_with_worktree
    wt = os.path.join(root, ".worktrees", task_id)
    with open(os.path.join(wt, "app.py"), "w") as f:
        f.write("worker edit\n")
    assert _git(["status", "--short"], root) == ""


def test_work_tree_for_finds_the_worktree(repo_with_worktree):
    root, task_id = repo_with_worktree
    workdir, in_wt = work_tree_for(root, task_id)
    assert in_wt is True
    assert os.path.samefile(workdir, os.path.join(root, ".worktrees", task_id))


def test_work_tree_for_falls_back_to_the_repo(repo_with_worktree):
    """A plain spawn has no worktree; nothing about it may change."""
    root, _ = repo_with_worktree
    workdir, in_wt = work_tree_for(root, "20260101-000000")
    assert in_wt is False
    assert workdir == root


def test_a_worktree_edit_shows_up_in_the_diff(repo_with_worktree):
    """The whole point: an edit inside the worktree must be reported.

    Before the fix this diff was computed in the main checkout and came back
    empty, so `verify` printed no changed files for a worker that had
    rewritten the file.
    """
    root, task_id = repo_with_worktree
    workdir, _ = work_tree_for(root, task_id)
    base = worktree_base_tree(workdir)
    assert base, "base tree must resolve"
    with open(os.path.join(workdir, "app.py"), "w") as f:
        f.write("worker edit\n")
    with open(os.path.join(workdir, "brand_new.py"), "w") as f:
        f.write("untracked too\n")
    diff = subprocess.run(
        ["git", "diff", "--name-status", base, current_tree(workdir)],
        cwd=workdir, capture_output=True, text=True).stdout
    assert "app.py" in diff
    assert "brand_new.py" in diff, "untracked files must be included"


def test_the_same_diff_is_empty_from_the_main_checkout(repo_with_worktree):
    """Red half: current_tree(repo_root) is what verify used to compute."""
    root, task_id = repo_with_worktree
    workdir, _ = work_tree_for(root, task_id)
    with open(os.path.join(workdir, "app.py"), "w") as f:
        f.write("worker edit\n")
    head_tree = _git(["rev-parse", "HEAD^{tree}"], root)
    diff = subprocess.run(
        ["git", "diff", "--name-status", head_tree, current_tree(root)],
        cwd=root, capture_output=True, text=True).stdout.strip()
    assert diff == "", "if this is non-empty the old code was not broken"


def test_base_tree_survives_a_worker_that_committed(repo_with_worktree):
    """`HEAD^{tree}` would diff the worker against itself and see nothing."""
    root, task_id = repo_with_worktree
    workdir, _ = work_tree_for(root, task_id)
    with open(os.path.join(workdir, "app.py"), "w") as f:
        f.write("worker edit\n")
    _git(["config", "user.email", "w@w"], workdir)
    _git(["config", "user.name", "w"], workdir)
    _git(["add", "-A"], workdir)
    _git(["commit", "-qm", "worker commit"], workdir)
    diff = subprocess.run(
        ["git", "diff", "--name-status", worktree_base_tree(workdir),
         current_tree(workdir)],
        cwd=workdir, capture_output=True, text=True).stdout
    assert "app.py" in diff


def test_current_tree_does_not_disturb_the_real_index(repo_with_worktree):
    """It runs `git add -A`; that must land in a throwaway index only."""
    root, task_id = repo_with_worktree
    workdir, _ = work_tree_for(root, task_id)
    with open(os.path.join(workdir, "scratch.py"), "w") as f:
        f.write("x\n")
    current_tree(workdir)
    assert "scratch.py" in _git(["status", "--short"], workdir)
    assert _git(["diff", "--cached", "--name-only"], workdir) == "", \
        "add -A leaked into the worker's own index"
