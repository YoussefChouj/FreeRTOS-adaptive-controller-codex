"""Tests for service startup identity (task 20260921-141231):
the git commit read once at startup and the browser-smoke stale-service
WARNING line.
"""
import subprocess

import pytest

from ground_station.service import browser_smoke
from ground_station.service import core


@pytest.fixture(autouse=True)
def _reset_commit_cache():
    core._started_commit.cache_clear()
    yield
    core._started_commit.cache_clear()


def test_started_commit_reads_short_head(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="abcdef1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert core._started_commit() == "abcdef1"
    # Read once: a second call must not invoke git again.
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append(a) or pytest.fail(
                            "git must be read at most once per process"))
    assert core._started_commit() == "abcdef1"
    assert calls == []


def test_started_commit_none_when_git_missing(monkeypatch):
    def raise_oserror(cmd, **kw):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    assert core._started_commit() is None


def test_started_commit_none_on_git_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 128, stdout=""))
    assert core._started_commit() is None


def test_started_commit_none_on_empty_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, stdout="  \n"))
    assert core._started_commit() is None


def test_service_startup_records_commit_and_time():
    from ground_station.service.storage import SessionStore

    svc = core.GroundStationService(store=SessionStore(), source="test")
    assert isinstance(svc.started_at, float)
    assert svc.started_commit is None or isinstance(svc.started_commit, str)


def test_warning_when_commit_differs():
    w = browser_smoke.commit_mismatch_warning("abcdef1", "1234567")
    assert w is not None
    assert w.startswith("WARNING")
    assert "abcdef1" in w and "1234567" in w


def test_no_warning_when_commit_matches():
    assert browser_smoke.commit_mismatch_warning("abcdef1", "abcdef1") is None


def test_warning_when_service_publishes_no_commit():
    w = browser_smoke.commit_mismatch_warning(None, "abcdef1")
    assert w is not None and w.startswith("WARNING")


def test_no_warning_when_local_git_unavailable():
    # No honest local basis for comparison: must not invent a mismatch.
    assert browser_smoke.commit_mismatch_warning("abcdef1", None) is None
