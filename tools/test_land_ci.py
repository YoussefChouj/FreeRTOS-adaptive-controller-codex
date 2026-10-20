"""tools/land_ci.py and the poll in tools/land.sh: GitHub's reply -> land, keep waiting (and say why), or stop.

Payloads are trimmed copies of real api.github.com replies. Why this file exists (2026-10-20): land.sh's inline
parser had a backslash inside an f-string, a SyntaxError before Python 3.12, and `2>/dev/null || state=unknown` hid
it, so a green CI run looked like a hang. The same day an exhausted unauthenticated rate limit looked identical.
The CLI and land.sh tests run the real script under this interpreter, which is what would have caught the first.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import land_ci  # noqa: E402

NOW = 1_800_000_000.0
RATE_MSG = "API rate limit exceeded for 204.0.113.7. (But here's the good news: Authenticated requests get a higher rate limit.)"


def _runs(*runs: tuple[int, str, str, str | None]) -> str:
    return json.dumps({'total_count': len(runs), 'workflow_runs': [
        {'id': i, 'path': p, 'status': s, 'conclusion': c} for i, p, s, c in runs]})


def _classify(code: int, body: str = '', **headers: str) -> tuple[int, str]:
    return land_ci.classify(code, {k.replace('_', '-'): v for k, v in headers.items()}, body, now=NOW)


def test_green_check_run_lands_and_other_workflows_are_ignored():
    body = _runs((7, '.github/workflows/docs.yml', 'completed', 'failure'),
                 (8, '.github/workflows/check.yml', 'completed', 'success'))
    assert _classify(200, body) == (land_ci.OK, 'completed success')


def test_newest_check_run_wins_and_a_running_one_has_no_conclusion():
    body = _runs((9, '.github/workflows/check.yml', 'in_progress', None),
                 (4, '.github/workflows/check.yml', 'completed', 'failure'))
    assert _classify(200, body) == (land_ci.OK, 'in_progress')


def test_no_run_yet_is_none():
    assert _classify(200, _runs()) == (land_ci.OK, 'none')


def test_rate_limit_waits_and_says_until_when_and_how_to_lift_it():
    verdict, text = _classify(403, json.dumps({'message': RATE_MSG}), x_ratelimit_remaining='0',
                              x_ratelimit_reset=str(int(NOW) + 900))
    assert verdict == land_ci.WAIT
    assert 'rate limit' in text and land_ci._clock(NOW + 900) in text and 'GITHUB_TOKEN' in text


def test_rate_limit_with_a_token_drops_the_token_hint():
    verdict, text = land_ci.classify(403, {'x-ratelimit-remaining': '0'}, json.dumps({'message': RATE_MSG}),
                                     authed=True, now=NOW)
    assert verdict == land_ci.WAIT and 'GITHUB_TOKEN' not in text


def test_secondary_rate_limit_uses_retry_after():
    verdict, text = _classify(403, json.dumps({'message': 'You have exceeded a secondary rate limit'}), retry_after='120')
    assert verdict == land_ci.WAIT and land_ci._clock(NOW + 120) in text


@pytest.mark.parametrize('code, message, needle', [
    (401, 'Bad credentials', 'GITHUB_TOKEN refused'),
    (403, 'Resource not accessible by personal access token', 'Actions: read'),
    (404, 'Not Found', 'LAND_REPO'),
    (422, 'Validation Failed', '422'),
])
def test_answers_that_waiting_cannot_change_stop(code, message, needle):
    verdict, text = _classify(code, json.dumps({'message': message}), x_ratelimit_remaining='4999')
    assert verdict == land_ci.STOP and message in text and needle in text


@pytest.mark.parametrize('code, body, needle', [
    (0, '', 'Could not resolve host'),
    (502, '<html>Bad gateway</html>', '502'),
    (200, '<html>captive portal</html>', 'unreadable'),
])
def test_no_answer_yet_waits(code, body, needle):
    verdict, text = land_ci.classify(code, {}, body, curl_error='curl: (6) Could not resolve host: api.github.com\n')
    assert verdict == land_ci.WAIT and needle in text


def test_headers_come_from_the_last_response_after_a_proxy_connect():
    dump = 'HTTP/1.1 200 Connection established\r\n\r\nHTTP/2 403\r\nX-RateLimit-Remaining: 0\r\nRetry-After: 5\r\n\r\n'
    assert land_ci.parse_headers(dump) == {'x-ratelimit-remaining': '0', 'retry-after': '5'}


def _cli(tmp_path: Path, code: str, headers: str, body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / 'h').write_text(headers, encoding='utf-8')
    (tmp_path / 'b').write_text(body, encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k != 'GITHUB_TOKEN'}
    return subprocess.run([sys.executable, str(TOOLS / 'land_ci.py'), code, str(tmp_path / 'h'), str(tmp_path / 'b')],
                          capture_output=True, text=True, env=env)


def test_cli_exit_code_is_the_verdict(tmp_path):
    green = _cli(tmp_path, '200', 'HTTP/2 200\r\n\r\n', _runs((1, '.github/workflows/check.yml', 'completed', 'success')))
    assert (green.returncode, green.stdout.strip()) == (0, 'completed success')
    limited = _cli(tmp_path, '403', 'HTTP/2 403\r\nx-ratelimit-remaining: 0\r\nx-ratelimit-reset: 1800000900\r\n\r\n',
                   json.dumps({'message': RATE_MSG}))
    assert limited.returncode == 2 and 'GITHUB_TOKEN' in limited.stdout


FAKE_CURL = '''#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_REPLY/args"
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift ;; -D) hdr=$2; shift ;; esac; shift; done
cp "$FAKE_REPLY/hdr" "$hdr"; cp "$FAKE_REPLY/body" "$out"; cat "$FAKE_REPLY/code"
'''
NEEDS_BASH = pytest.mark.skipif(shutil.which('bash') is None or shutil.which('git') is None, reason='bash or git missing')
CI_GREEN = _runs((1, '.github/workflows/check.yml', 'completed', 'success'))
RATE_LIMITED = json.dumps({'message': 'API rate limit exceeded'})


def _land_status(tmp_path: Path, code: str, body: str, **env_extra: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run land.sh --status in a throwaway repo against a fake curl; return the run and curl's arguments.

    Hermetic: no GITHUB_TOKEN, and GITHUB_TOKEN_FILE points at a missing file unless env_extra says otherwise."""
    repo, reply, stub = tmp_path / 'repo', tmp_path / 'reply', tmp_path / 'bin'
    for d in (repo / 'tools', reply, stub):
        d.mkdir(parents=True)
    for name in ('land.sh', 'land_ci.py'):
        shutil.copy(TOOLS / name, repo / 'tools' / name)
    (reply / 'hdr').write_text(f'HTTP/2 {code}\r\n\r\n', encoding='utf-8')
    (reply / 'body').write_text(body, encoding='utf-8')
    (reply / 'code').write_text(code, encoding='utf-8')
    (stub / 'curl').write_text(FAKE_CURL, encoding='utf-8', newline='\n')
    (stub / 'curl').chmod(0o755)
    git = ['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=/dev/null']
    subprocess.run(['git', 'init', '-q', '-b', 'feat', str(repo)], check=True)
    subprocess.run([*git, 'add', '-A'], check=True)
    subprocess.run([*git, 'commit', '-qm', 'x'], check=True)
    env = {k: v for k, v in os.environ.items() if k not in ('GITHUB_TOKEN', 'GITHUB_TOKEN_FILE')}
    env.update(FAKE_REPLY=reply.as_posix(), PY=Path(sys.executable).as_posix(),
               PATH=str(stub) + os.pathsep + env.get('PATH', ''), GITHUB_TOKEN_FILE=(tmp_path / 'no-token').as_posix())
    env.update(env_extra)
    res = subprocess.run([shutil.which('bash'), 'tools/land.sh', '--status', 'feat'], cwd=repo,
                         capture_output=True, text=True, env=env)
    return res, (reply / 'args').read_text(encoding='utf-8')


@NEEDS_BASH
@pytest.mark.parametrize('code, body, rc, needle', [
    ('200', CI_GREEN, 0, 'completed success'),
    ('401', json.dumps({'message': 'Bad credentials'}), 3, 'GITHUB_TOKEN refused (401 Bad credentials)'),
])
def test_land_status_reports_the_api_answer(tmp_path, code, body, rc, needle):
    res, args = _land_status(tmp_path, code, body)
    assert res.returncode == rc, res.stderr
    assert needle in res.stdout and 'Authorization' not in args


@NEEDS_BASH
def test_land_reads_the_token_file_and_the_environment_wins(tmp_path):
    token = tmp_path / 'github.token'
    token.write_bytes(b'tok-file\r\n')  # a token saved by a Windows editor
    bare, args = _land_status(tmp_path / 'bare', '403', RATE_LIMITED)
    assert bare.returncode == 2 and 'no token' in bare.stdout and 'Authorization' not in args
    filed, args = _land_status(tmp_path / 'file', '403', RATE_LIMITED, GITHUB_TOKEN_FILE=token.as_posix())
    assert filed.returncode == 2 and 'no token' not in filed.stdout  # land_ci.py sees the token too
    assert 'Authorization: Bearer tok-file\n' in args
    env, args = _land_status(tmp_path / 'env', '200', CI_GREEN, GITHUB_TOKEN_FILE=token.as_posix(), GITHUB_TOKEN='tok-env')
    assert env.returncode == 0 and 'Authorization: Bearer tok-env\n' in args
