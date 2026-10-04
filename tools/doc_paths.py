"""Every repo path named in an agent-facing doc must exist.

An agent follows `API/foo.c` from AGENTS.md or a skill as an instruction: a stale path costs it a failed read and
a search, every session. Scope: AGENTS.md, CLAUDE.md, docs/*.md, docs/agent/*.md, docs/skills/*.md and the
project skills .claude/skills/*/SKILL.md. Reports, briefs, research notes and plans are dated snapshots and are
not checked.

A path is a backtick span with a '/' and a file extension whose first segment is a top-level repo entry
(`API/pid.c`, `docs/agent/HANDOFF.md:12`); run outputs under logs/ are skipped. A `:line` past the end of the
file fails too (the code moved, the doc did not). Known exceptions (a file that lives on a branch or in another
repo) go in tools/doc_paths_allow.txt; that list shrinks only: an entry whose file now exists fails too.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ALLOW = ROOT / 'tools' / 'doc_paths_allow.txt'
RUN_OUTPUT = ('logs/',)          # written by a run, absent in a fresh checkout
SCOPE = ['AGENTS.md', 'CLAUDE.md', 'docs/*.md', 'docs/agent/*.md', 'docs/skills/*.md', '.claude/skills/*/SKILL.md']
PATH = re.compile(r'`([\w.][\w./-]*/[\w./-]*\.[A-Za-z]{1,8})(?::(\d+)(?:-(\d+))?)?`')


def docs():
    for pattern in SCOPE:
        yield from sorted(ROOT.glob(pattern))


def missing():
    """Yield (doc, path) for every named path that does not exist, or (doc, path:line) past its end."""
    tops = {p.name for p in ROOT.iterdir()}
    lines = {}
    for doc in docs():
        text = doc.read_text(encoding='utf-8', errors='replace')
        name = doc.relative_to(ROOT).as_posix()
        for m in PATH.finditer(text):
            path = m.group(1)
            if path.split('/')[0] not in tops or path.startswith(RUN_OUTPUT):
                continue
            if not (ROOT / path).exists():
                yield name, path
            elif m.group(2):
                if path not in lines:
                    lines[path] = len((ROOT / path).read_bytes().splitlines())
                if int(m.group(3) or m.group(2)) > lines[path]:
                    yield name, '%s:%s' % (path, m.group(3) or m.group(2))


def main():
    allow = set()
    for line in ALLOW.read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            allow.add(tuple(line.split()[:2]))
    found = set(missing())
    bad = 0
    for doc, path in sorted(found - allow):
        print('%s: `%s` %s' % (doc, path, 'is past the end of the file' if ':' in path else 'does not exist'))
        bad += 1
    for doc, path in sorted(allow - found):
        print('%s: `%s` resolves now: remove it from %s' % (doc, path, ALLOW.relative_to(ROOT).as_posix()))
        bad += 1
    print('doc-paths: %s (%d docs, %d allowed)' % ('FAIL: %d' % bad if bad else 'OK', len(list(docs())), len(allow)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
