"""tools/agent_push_guard.py against a throwaway git repo: what a push may carry, and what proves it was checked.

Each test builds origin/main + one branch commit, then calls guard.main(<branch>, <sha>) the way .githooks/pre-push
does. The routing table is the real tools/check_routes.txt.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_push_guard as guard  # noqa: E402
import check_routes  # noqa: E402

BRANCH = 'feat/x'
PAGE = 'docs/agent/streams/feat-x.md'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding='utf-8')


def _commit(repo: Path) -> str:
    _git(repo, 'add', '-A')
    _git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x')
    return _git(repo, 'rev-parse', 'HEAD')


def _record(repo: Path, line: str) -> None:
    _write(repo, '.agent_state/last_check', line + '\n')


@pytest.fixture
def repo(tmp_path_factory, monkeypatch):
    tmp_path = tmp_path_factory.mktemp('repo')
    _git(tmp_path, 'init', '-q')
    _write(tmp_path, '.gitignore', '.agent_state/\n')
    _write(tmp_path, 'README.md', 'base\n')
    _git(tmp_path, 'update-ref', 'refs/remotes/origin/main', _commit(tmp_path))
    tier0 = tmp_path_factory.mktemp('cfg') / 'tier0.txt'
    tier0.write_text('# test list\nAPI/pid.c\n', encoding='utf-8')
    monkeypatch.setattr(guard, 'ROOT', tmp_path)
    monkeypatch.setattr(guard, 'TIER0_LIST', tier0)
    return tmp_path


def _branch(repo: Path, page: str, files: dict[str, str]) -> str:
    _write(repo, PAGE, page)
    for path, text in files.items():
        _write(repo, path, text)
    sha = _commit(repo)
    _record(repo, f'CHECK PASS x scope=full tree={_git(repo, "rev-parse", sha + "^{tree}")}')
    return sha


def test_known_steps_are_the_gate_steps():
    assert guard._KNOWN_STEPS == set(check_routes.STEP_ORDER)


def test_in_scope_checked_push_passes(repo, capsys):
    sha = _branch(repo, 'Allowed-Paths: API/**, docs/**\n', {'API/foo.c': 'int x;\n'})
    assert guard.main([BRANCH, sha]) == 0, capsys.readouterr().out


def test_file_outside_allowed_paths_is_refused(repo, capsys):
    sha = _branch(repo, 'Allowed-Paths: docs/**\n', {'API/foo.c': 'int x;\n'})
    assert guard.main([BRANCH, sha]) == 1
    assert 'outside Allowed-Paths' in capsys.readouterr().out


def test_page_is_read_from_the_pushed_commit_not_the_worktree(repo):
    sha = _branch(repo, 'Allowed-Paths: docs/**\n', {'API/foo.c': 'int x;\n'})
    _write(repo, PAGE, 'Allowed-Paths: **\n')          # uncommitted widening does not count
    assert guard.main([BRANCH, sha]) == 1


def test_unapproved_tier0_file_is_refused(repo, capsys):
    sha = _branch(repo, 'Allowed-Paths: API/**, docs/**\n', {'API/pid.c': 'int kp;\n'})
    assert guard.main([BRANCH, sha]) == 1
    assert 'API/pid.c' in capsys.readouterr().out


def test_committed_operator_approval_unlocks_tier0(repo, capsys):
    page = 'Allowed-Paths: API/**, docs/**\nTier0-Approved: API/pid.c - operator 2026-10-20 "retune kp, go"\n'
    sha = _branch(repo, page, {'API/pid.c': 'int kp;\n'})
    assert guard.main([BRANCH, sha]) == 0, capsys.readouterr().out


def test_malformed_approval_is_ignored_and_shown(repo, capsys):
    page = 'Allowed-Paths: API/**, docs/**\nTier0-Approved: API/pid.c - operator said ok\n'
    sha = _branch(repo, page, {'API/pid.c': 'int kp;\n'})
    assert guard.main([BRANCH, sha]) == 1
    assert 'malformed' in capsys.readouterr().out


def test_pass_recorded_before_the_last_edit_is_refused(repo, capsys):
    sha = _branch(repo, 'Allowed-Paths: API/**, docs/**\n', {'API/foo.c': 'int x;\n'})
    old_tree = _git(repo, 'rev-parse', sha + '^{tree}')
    _write(repo, 'API/foo.c', 'int x = 1;\n')
    sha = _commit(repo)
    _record(repo, f'CHECK PASS x scope=full tree={old_tree}')
    assert guard.main([BRANCH, sha]) == 1
    assert 'differ from what the last run checked' in capsys.readouterr().out


def test_record_without_tree_is_refused(repo, capsys):
    sha = _branch(repo, 'Allowed-Paths: API/**, docs/**\n', {'API/foo.c': 'int x;\n'})
    _record(repo, 'CHECK PASS x scope=full')
    assert guard.main([BRANCH, sha]) == 1
    assert 'no tree=' in capsys.readouterr().out


def test_doc_only_subset_cannot_cover_an_api_change(repo, capsys):
    sha = _branch(repo, 'Allowed-Paths: API/**, docs/**\n', {'API/foo.c': 'int x;\n'})
    _record(repo, f'CHECK SUBSET PASS x scope=doc-paths doc-budget tree={_git(repo, "rev-parse", sha + "^{tree}")}')
    assert guard.main([BRANCH, sha]) == 1
    assert 'missing' in capsys.readouterr().out


def test_routing_failure_requires_the_full_gate(monkeypatch):
    def broken(*_a, **_k):
        raise SystemExit('check_routes.txt has no rows')
    monkeypatch.setattr(check_routes, 'required_for_diff', broken)
    assert guard._required_steps('HEAD') == set(check_routes.STEP_ORDER)


def test_route_reports_unrouted_files_and_always_runs_doc_steps():
    need, unrouted = check_routes.route(['nowhere/x.bin'], check_routes.read_routes())
    assert need == set(check_routes.ALWAYS) and unrouted == ['nowhere/x.bin']
