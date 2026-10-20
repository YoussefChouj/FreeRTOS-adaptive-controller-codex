"""What tools/land.sh does next, read from GitHub's reply to "Actions runs for this commit".

land.sh fetches with curl (the transport that already works through the VPN) and hands the saved reply here:
    python tools/land_ci.py <http_code> <headers_file> <body_file> [<curl_stderr_file>]
stdout is one line for the operator; the exit code is the verdict:
    0  the reply parsed: stdout is the `check` run state, "completed success", "in_progress", "none", ...
    2  no answer yet (no reply, rate limit, GitHub 5xx): keep waiting, stdout says why and until when
    3  waiting cannot help (token refused, no access, unknown repo): stop, stdout says what to fix
A lost reply is "unknown", never "failed": only an answer that will not change by itself stops the landing.
Any other exit (this script crashing) stops land.sh too, so a broken parser can no longer pass for a slow CI.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

OK, WAIT, STOP = 0, 2, 3
WORKFLOW = '/check.yml'


def parse_headers(text: str) -> dict[str, str]:
    """Headers of the last response in a `curl -D` dump (a proxy's CONNECT reply can come first)."""
    blocks = [b for b in text.replace('\r\n', '\n').split('\n\n') if b.strip().startswith('HTTP/')]
    headers: dict[str, str] = {}
    for line in (blocks[-1].splitlines()[1:] if blocks else []):
        name, sep, value = line.partition(':')
        if sep:
            headers[name.strip().lower()] = value.strip()
    return headers


def _message(body: str) -> str:
    try:
        return str(json.loads(body).get('message', '')).split('. ')[0].strip() or '(no message)'
    except (ValueError, AttributeError):
        return ' '.join(body.split())[:80] or '(empty reply)'


def _clock(epoch: float) -> str:
    return time.strftime('%H:%M', time.localtime(epoch))


def classify(code: int, headers: dict[str, str], body: str, *, curl_error: str = '', authed: bool = False,
             now: float | None = None) -> tuple[int, str]:
    now = time.time() if now is None else now
    if code == 0:
        return WAIT, 'no reply from api.github.com (' + (curl_error.strip().splitlines() or ['network'])[-1] + ')'
    if code == 200:
        try:
            runs = [r for r in json.loads(body).get('workflow_runs', []) if str(r.get('path', '')).endswith(WORKFLOW)]
        except (ValueError, AttributeError):
            return WAIT, 'unreadable reply (HTTP 200, not the runs JSON)'
        if not runs:
            return OK, 'none'
        run = max(runs, key=lambda r: r.get('id') or 0)  # a new run of the same commit gets a higher id
        return OK, ' '.join(str(p) for p in (run.get('status'), run.get('conclusion')) if p)
    msg = _message(body)
    if code == 401:
        return STOP, f'GITHUB_TOKEN refused (401 {msg}): renew it, or unset it to poll unauthenticated'
    if code in (403, 429) and (headers.get('x-ratelimit-remaining') == '0' or 'rate limit' in msg.lower()):
        if headers.get('retry-after', '').isdigit():
            until = now + int(headers['retry-after'])
        elif headers.get('x-ratelimit-reset', '').isdigit():
            until = float(headers['x-ratelimit-reset'])
        else:
            until = now + 60
        hint = '' if authed else '; set GITHUB_TOKEN (5000 requests/h instead of 60 per IP) and re-run'
        return WAIT, f'GitHub API rate limit ({msg}) until {_clock(until)}{hint}'
    if code == 403:
        return STOP, f'GitHub API refused (403 {msg}): the token needs Actions: read on this repo'
    if code == 404:
        return STOP, f'repo not found or not visible (404 {msg}): check LAND_REPO and the token scope'
    if code >= 500:
        return WAIT, f'GitHub API error {code} ({msg})'
    return STOP, f'GitHub API answered {code} ({msg})'


def _read(path: str | None) -> str:
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace') if path else ''
    except OSError:
        return ''


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4) or not argv[0].isdigit():
        print('usage: land_ci.py <http_code> <headers_file> <body_file> [<curl_stderr_file>]', file=sys.stderr)
        return 64
    verdict, text = classify(int(argv[0]), parse_headers(_read(argv[1])), _read(argv[2]),
                             curl_error=_read(argv[3] if len(argv) == 4 else None),
                             authed=bool(os.environ.get('GITHUB_TOKEN')))
    print(text)
    return verdict


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
