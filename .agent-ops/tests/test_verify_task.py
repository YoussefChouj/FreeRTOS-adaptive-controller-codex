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