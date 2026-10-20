"""Mechanical pre-push guard for agents: `python tools/agent_push_guard.py [<branch> <sha>]`.

.githooks/pre-push calls it once per pushed branch, with the branch name on origin and the commit being pushed;
with no arguments it checks the current branch at HEAD. Everything is read from <sha>, not the working tree, so an
uncommitted edit cannot widen what a push may carry. Three invariants a prose checklist cannot rubber-stamp:

1.  Allowed-Paths: every file in the diff origin/main...<sha> matches a glob on the `Allowed-Paths:` line of the
    branch's stream page (docs/agent/HANDOFF.md for main, docs/agent/streams/<branch, '/' as '-'>.md otherwise).
    Globs are tools/check_routes.py's: '*' stays inside one path segment, '**' spans directories.
2.  Tier-0: a file listed in tools/tier0.txt is in the diff only when the stream page has a line
    `Tier0-Approved: <file>[, <file>...] - operator <YYYY-MM-DD> "<their words>"` naming it. The approval sits in
    the diff, where the reviewer sees it, instead of in an environment variable nobody sees.
3.  Check coverage: .agent_state/last_check records a passing tools/check.sh run whose steps cover what the diff
    needs (tools/check_routes.txt) and whose tree= matches <sha> on every file the branch changes, so a pass from
    before the last edit does not cover the push.

An `Acceptance-Cmd:` line is printed for the operator, never run here (the hardware may not be attached).
Exit 0 on success, 1 on a violation, with the reason on stdout so .githooks/pre-push shows it.
"""
import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import check_routes  # noqa: E402 - the one glob matcher and step router; an import failure exits non-zero (refuses)

TIER0_LIST = ROOT / 'tools' / 'tier0.txt'
MAIN_PAGE = 'docs/agent/HANDOFF.md'
STREAMS = 'docs/agent/streams'

# Tolerant parsers: lines look like "Allowed-Paths: foo/*, bar/*".
KV = re.compile(r'^\s*([A-Za-z][\w-]*)\s*:\s*(.+?)\s*$')
APPROVAL = re.compile(r'^\s*Tier0-Approved\s*:\s*(.+?)\s+-\s+operator\s+\d{4}-\d{2}-\d{2}\s+".+"\s*$')


def git(*args):
    out = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
    return out.returncode, out.stdout


def tier0():
    return [line.strip() for line in TIER0_LIST.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.startswith('#')]


def page_path(branch):
    return MAIN_PAGE if branch == 'main' else f"{STREAMS}/{branch.replace('/', '-')}.md"


def page_text(branch, sha):
    """The stream page as committed in <sha>, or None when <sha> has none."""
    rc, out = git('show', f'{sha}:{page_path(branch)}')
    return out if rc == 0 else None


def parse_kv(text):
    out = {}
    for line in (text or '').splitlines():
        m = KV.match(line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def approved_tier0(text):
    """(files named on well-formed Tier0-Approved lines, malformed lines)."""
    names, bad = set(), []
    for line in (text or '').splitlines():
        if not re.match(r'^\s*Tier0-Approved\s*:', line):
            continue
        m = APPROVAL.match(line)
        if m:
            names |= {f.strip() for f in m.group(1).split(',') if f.strip()}
        else:
            bad.append(line.strip())
    return names, bad


def diff_against_main(sha):
    rc, out = git('diff', '--name-only', f'origin/main...{sha}')
    if rc != 0:
        # No origin/main yet (e.g. a fresh clone that never fetched): refuse on principle.
        sys.stderr.write(f'agent_push_guard: cannot diff origin/main...{sha}\n')
        sys.exit(1)
    return [line for line in out.splitlines() if line]


def last_check_passed(sha, files):
    """Did `tools/check.sh` last complete with a passing run locally, and does that run cover this diff?

    Accepts `CHECK PASS` (the full gate, which trivially covers every step). A subset is accepted only when it
    contains every step this branch's diff requires, per tools/check_routes.txt: an agent that touched API/*.c
    cannot satisfy the guard with a doc-paths-only run. The routing is the same one `check.sh --changed` uses, so
    "run what your change needs" and "the guard accepts what your change needs" are one rule, not two.

    The record also names the tree check.sh checked (tree=). Every file this branch changes must be the same in that
    tree and in <sha>: a pass recorded before the last edit, or in another checkout, does not cover the push.

    Returns (ok, reason). reason is shown to the operator when ok is False.
    """
    f = ROOT / '.agent_state' / 'last_check'
    if not f.exists():
        return False, 'no run recorded in .agent_state/last_check; run `bash tools/check.sh --changed`'
    text = f.read_text(encoding='utf-8', errors='replace')
    if not (text.startswith('CHECK PASS') or text.startswith('CHECK SUBSET PASS')):
        return False, 'the last check.sh run did not pass'
    m = re.search(r'tree=([0-9a-f]{40,64})', text)
    if not m:
        return False, 'the last run recorded no tree=; re-run `bash tools/check.sh --changed`'
    rc, out = git('diff', '--name-only', m.group(1), sha)
    if rc != 0:
        return False, f'the recorded tree {m.group(1)[:12]} is not in this repository; re-run the check'
    stale = sorted(set(out.splitlines()) & set(files))
    if stale:
        return False, (f'{len(stale)} changed file(s) differ from what the last run checked (first: {stale[0]}); '
                       're-run `bash tools/check.sh --changed` on the commit you push')
    if text.startswith('CHECK PASS'):
        return True, ''
    ran = _steps_from_last_check(text)
    if not ran:
        # A legacy/hand-written line: scope= is the only record of what ran, so we cannot prove coverage.
        return False, 'the last subset run recorded no step scope; re-run `bash tools/check.sh --changed`'

    required = _required_steps(sha)
    missing = sorted(required - ran)
    if missing:
        return False, (
            'the last subset run did not cover this diff.\n'
            f'         changed files require: {" ".join(sorted(required))}\n'
            f'         the recorded run had:    {" ".join(sorted(ran))}\n'
            f'         missing:                 {" ".join(missing)}\n'
            '         Run `bash tools/check.sh --changed` (it picks exactly those steps) and commit again.'
        )
    return True, ''


def _steps_from_last_check(text):
    """The steps the recorded run covered, or None when scope= is absent or malformed.

    A scope with no separator (`row-metafw-lint`, what check.sh wrote before the space was kept) would otherwise
    read as one unknown step name; treating it as unreadable makes the guard refuse and ask for a re-run, which is
    the safe direction.
    """
    # scope= runs to tree= or the end of the line (the step names are space-separated).
    m = re.search(r'scope=(\S[^\n]*?)(?:\s+tree=\S*)?\s*$', text, re.M)
    if not m:
        return None
    steps = {s for s in m.group(1).split() if s}
    if steps == {'full'}:
        return set(_KNOWN_STEPS)          # a full run covered everything
    if not steps:
        return None
    if steps <= _KNOWN_STEPS:
        return steps
    # At least one name is not a real step: the line is malformed (or the old unseparated format), so it proves
    # nothing. Refusing is the safe direction; check.sh --changed rewrites it correctly.
    return None


_KNOWN_STEPS = set(check_routes.STEP_ORDER)


def _required_steps(sha):
    """The steps the diff origin/main...<sha> needs, per tools/check_routes.txt. Any routing failure (git, a broken
    table) requires the full gate: the guard fails closed."""
    try:
        return set(check_routes.required_for_diff(ROOT, [f'origin/main...{sha}']))
    except (Exception, SystemExit) as exc:          # noqa: BLE001 - never let a routing bug pass a push
        sys.stderr.write(f'agent_push_guard: routing unavailable ({exc}); requiring the full gate\n')
        return set(_KNOWN_STEPS)


def main(argv):
    if len(argv) == 2:
        branch, sha = argv
    elif not argv:
        rc, out = git('rev-parse', '--abbrev-ref', 'HEAD')
        branch, sha = out.strip(), 'HEAD'
        if rc != 0 or branch == 'HEAD':
            sys.stderr.write('agent_push_guard: cannot determine the current branch; pass <branch> <sha>\n')
            return 1
    else:
        sys.stderr.write('usage: agent_push_guard.py [<branch> <sha>]\n')
        return 2

    text = page_text(branch, sha)
    kv = parse_kv(text)
    allowed_raw = kv.get('allowed-paths', '').strip()
    if not allowed_raw:
        print(f'agent_push_guard: branch {branch!r} has no Allowed-Paths: line in {page_path(branch)} as committed '
              f'in {sha[:12]}.\nCommit one (e.g. "Allowed-Paths: tools/**, docs/context/inbox/**").')
        return 1

    allowed = [g.strip() for g in allowed_raw.split(',') if g.strip()]
    files = diff_against_main(sha)
    bad_paths = [f for f in files if not any(check_routes.match(f, g) for g in allowed)]

    # Tier-0: human-gated whatever Allowed-Paths says.
    approved, malformed = approved_tier0(text)
    tier0_hits = [f for f in files if any(check_routes.match(f, p) for p in tier0())]
    unapproved = [f for f in tier0_hits if f not in approved]
    if unapproved:
        print('agent_push_guard: tier-0 flight-critical files in the diff without operator approval:')
        for f in unapproved:
            print(f'  - {f}')
        print(f'Approval is a committed line in {page_path(branch)}:\n'
              '  Tier0-Approved: <file>, <file> - operator <YYYY-MM-DD> "<the operator\'s words>"')
        for line in malformed:
            print(f'  (malformed, ignored: {line})')
        return 1

    if bad_paths:
        print(f'agent_push_guard: files outside Allowed-Paths for branch {branch!r}:')
        for f in bad_paths:
            print(f'  - {f}')
        print(f'Allowed: {allowed_raw}')
        print("Narrow the diff or widen the stream page's Allowed-Paths line (in a commit); `git push --no-verify` "
              'skips this guard and shows in the command.')
        return 1

    checked_ok, why = last_check_passed(sha, files)
    if not checked_ok:
        print(f'agent_push_guard: check coverage insufficient for this diff.\n         {why}')
        return 1

    if 'acceptance-cmd' in kv:
        print(f'agent_push_guard: ACCEPTANCE-CMD is {kv["acceptance-cmd"]!r}. '
              f'The operator (or CI) runs this before merge; do not run it here.')
    print(f'agent_push_guard: OK ({branch!r}, {len(files)} files within Allowed-Paths)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
