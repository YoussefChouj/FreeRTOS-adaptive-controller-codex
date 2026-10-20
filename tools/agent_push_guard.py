"""Mechanical pre-push guard for agents.

Reads the stream page for the current branch (docs/agent/HANDOFF.md for
main, docs/agent/streams/<name>.md otherwise) and enforces two
hard invariants that prose checklists cannot silently rubber-stamp:

1.  Allowed-Paths: only files matching one of these globs may be in this
    branch's diff against origin/main. Tier-0 flight-critical paths are
    always disallowed unless TIER0_OVERRIDE=1 is exported.
2.  Disallowed-Tier0: same set as AGENTS.md "Authorizations" > Hardware
    notes — by policy these paths are human-gated even when on a
    stream that has them in Allowed-Paths.

Optional:
3.  Acceptance-Cmd: an explicit command printed for the operator to
    run before merge. We cannot execute it (HW may not exist on the
    pushing machine), but we DO refuse to push if the last local
    invocation of `tools/check.sh` (logged to .agent_state/last_check)
    does not end with CHECK PASS.

Exit 0 on success, 1 on a policy violation. Prints the reason on stdout
(so .githooks/pre-push shows it), and writes a one-line verdict.
"""
import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
TIER0 = [
    # Flight-critical paths. Mirrors docs/agent/governance-zones.md Red zone
    # and AGENTS.md Authorizations table. Edit in two places at once.
    'API/pid.c', 'API/mrac.c', 'API/mrac_math.c',
    'API/ekf.c', 'API/ekf_of.c',
    'API/rc_input.c', 'API/land_profile.h',
    'API/land_gate.c', 'API/land_xy_gate.c',
    'API/prearm.c',
    'TASK/StabilizerTask.c', 'TASK/AutoflyTask.c',
]
DEFAULT_MAIN_PAGE = ROOT / 'docs' / 'agent' / 'HANDOFF.md'
STREAMS = ROOT / 'docs' / 'agent' / 'streams'

# Tolerant parsers: lines look like "Allowed-Paths: foo/*, bar/*".
KV = re.compile(r'^\s*([A-Za-z][\w-]*)\s*:\s*(.+?)\s*$')


def fnmatch(path, glob):
    """Gitignore-style glob matching with `**` support.

    `*` matches one path segment (no `/`); `**` matches zero or more
    segments. A trailing `/` is ignored. We deliberately do not use
    Python's `fnmatch` because it does not understand `**`.
    """
    g = glob.rstrip('/')
    g = g.replace('**', '*')  # a first-pass; refine per-segment below
    # Per-segment: split on '/'. For each segment:
    #   - if '*' (from '**'): allow any number of segments (greedy)
    #   - if '*' (from '*'): match one segment
    #   - else: match literal
    g_segs = g.split('/')
    p_segs = path.split('/')
    i = j = 0
    while i < len(p_segs) and j < len(g_segs):
        gp = g_segs[j]
        pp = p_segs[i]
        if gp == '*':
            # Lookahead: if next glob seg is also '*', treat as '**' → eat
            # all remaining path segments.
            if j + 1 < len(g_segs) and g_segs[j + 1] == '*':
                # consume remaining path segments and let trailing *
                # match zero+ segments at end
                return True
            # Plain '*' matches exactly one segment (no slashes in pp)
            if '/' in pp:
                return False
            i += 1; j += 1
            continue
        # Literal (with any embedded '*' from earlier '**' reduction).
        if '*' in gp:
            import fnmatch as _fn
            if not _fn.fnmatchcase(pp, gp):
                return False
            i += 1; j += 1
            continue
        if gp != pp:
            return False
        i += 1; j += 1
    # Leftover: trailing '*' can absorb zero+ segments at end.
    if j < len(g_segs) and all(g_segs[k] == '*' for k in range(j, len(g_segs))):
        return i == len(p_segs)
    return i == len(p_segs) and j == len(g_segs)


def stream_page():
    """Pick the stream page that owns this branch's working tree."""
    try:
        head = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=ROOT, text=True
        ).strip()
    except subprocess.CalledProcessError:
        return None, None
    if head == 'main':
        page = DEFAULT_MAIN_PAGE
    else:
        # Branch names may contain slashes (e.g. claude/feature-x). The
        # stream page filename uses '-' to keep it a single path segment.
        safe = head.replace('/', '-')
        page = STREAMS / f'{safe}.md'
    if not page.exists():
        return head, None
    return head, page


def parse_kv(page):
    if page is None:
        return {}
    out = {}
    for line in page.read_text(encoding='utf-8', errors='replace').splitlines():
        m = KV.match(line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def diff_against_main():
    out = subprocess.run(
        ['git', 'diff', '--name-only', 'origin/main...HEAD'],
        cwd=ROOT, capture_output=True, text=True
    )
    if out.returncode != 0:
        # No origin/main yet (e.g. brand-new branch) — refuse on principle
        sys.stderr.write('agent_push_guard: origin/main not reachable from HEAD\n')
        sys.exit(1)
    return [line for line in out.stdout.splitlines() if line]


def last_check_passed():
    """Did `tools/check.sh` last complete with a passing run locally?

    Accepts both `CHECK PASS` (full) and `CHECK SUBSET PASS` (subset). The
    pre-push guard is about "did the agent run the gate recently", not
    about completeness — AGENTS.md requires the full pass before merge,
    but a subset is fine for a local commit and a developer push to a
    feature branch. The operator reads `scope=` from the file at merge.
    """
    f = ROOT / '.agent_state' / 'last_check'
    if not f.exists():
        return False
    text = f.read_text(encoding='utf-8', errors='replace')
    return 'CHECK PASS' in text or 'CHECK SUBSET PASS' in text


def main():
    head, page = stream_page()
    if head is None:
        sys.stderr.write('agent_push_guard: cannot determine current branch\n')
        return 1

    kv = parse_kv(page)
    allowed_raw = kv.get('allowed-paths', '').strip()
    if not allowed_raw:
        print(f'agent_push_guard: branch {head!r} has no Allowed-Paths: line in '
              f'{page.relative_to(ROOT) if page else "stream page"}.\n'
              f'Add one (e.g. "Allowed-Paths: firmware/*, docs/context/inbox/*") '
              f'or run git push --no-verify.')
        return 1

    allowed = [g.strip() for g in allowed_raw.split(',') if g.strip()]
    files = diff_against_main()
    bad_paths = []
    for f in files:
        if any(fnmatch(f, g) for g in allowed):
            continue
        bad_paths.append(f)

    # Tier-0 always human-gated
    tier0_hits = [f for f in files if any(fnmatch(f, p) for p in TIER0)]
    tier0_override = __import__('os').environ.get('TIER0_OVERRIDE') == '1'

    if tier0_hits and not tier0_override:
        print(f'agent_push_guard: tier-0 flight-critical paths in diff, '
              f'human override required (set TIER0_OVERRIDE=1 after review):')
        for f in tier0_hits:
            print(f'  - {f}')
        return 1

    if bad_paths:
        print(f'agent_push_guard: files outside Allowed-Paths for branch {head!r}:')
        for f in bad_paths:
            print(f'  - {f}')
        print(f'Allowed: {allowed_raw}')
        print('Either narrow the diff, or update the stream page Allowed-Paths '
              'line, or run git push --no-verify (audit-only; AGENTS.md forbids '
              'pushing to main from any agent session).')
        return 1

    if not last_check_passed():
        print('agent_push_guard: no recent CHECK PASS recorded in '
              '.agent_state/last_check. Run `bash tools/check.sh` and try again.')
        return 1

    if 'acceptance-cmd' in kv:
        print(f'agent_push_guard: ACCEPTANCE-CMD is {kv["acceptance-cmd"]!r}. '
              f'The operator (or CI) runs this before merge; do not run it here.')
    print(f'agent_push_guard: OK ({head!r}, {len(files)} files within Allowed-Paths)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
