"""Firmware lint ratchet for docs/firmware-coding-standard.md rules 1 and 2.

Checks every .c/.h under the firmware source dirs:
  ascii   - no byte above 0x7F (untranslated GBK comments)
  header  - a .c file carries the `@module` header block near the top
  double  - no double-precision libm call (sin, cos, sqrt, ...) in code: the Cortex-M4 FPU is single precision,
            so armcc runs these in software (the `f` forms run on the FPU). Comments are ignored.

Existing violations are listed in tools/fw_lint_allow.txt. The gate fails on a new violation, and also on an
allow-list entry that no longer fails (so the list can only shrink). Regenerate with --write-allow after fixing
files.
"""
import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIRS = ('API', 'TASK', 'BSP')
ALLOW = ROOT / 'tools' / 'fw_lint_allow.txt'
HEADER_WINDOW = 400   # bytes from the start of the file in which `@module` must appear
COMMENT = re.compile(rb'/\*.*?\*/|//[^\n]*', re.S)
DOUBLE_CALL = re.compile(rb'(?<![A-Za-z0-9_.>])(sin|cos|tan|asin|acos|atan|atan2|sqrt|pow|exp|log|log10|floor|ceil'
                         rb'|fmod|fabs|round)\s*\(')


def violations():
    out = set()
    for d in DIRS:
        for p in sorted((ROOT / d).glob('*.[ch]')):
            raw = p.read_bytes()
            rel = p.relative_to(ROOT).as_posix()
            if any(b > 0x7F for b in raw):
                out.add((rel, 'ascii'))
            if p.suffix == '.c' and b'@module' not in raw[:HEADER_WINDOW]:
                out.add((rel, 'header'))
            if DOUBLE_CALL.search(COMMENT.sub(b' ', raw)):
                out.add((rel, 'double'))
    return out


def load_allow():
    if not ALLOW.exists():
        return set()
    rows = set()
    for line in ALLOW.read_text(encoding='ascii').splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            path, rule = line.split()
            rows.add((path, rule))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--write-allow', action='store_true', help='rewrite the allow-list from the current tree')
    args = ap.parse_args()
    found = violations()
    if args.write_allow:
        body = ''.join('%s %s\n' % v for v in sorted(found))
        ALLOW.write_text('# fw_lint.py allow-list: existing violations. Shrink only.\n' + body, encoding='ascii')
        print('fw-lint: wrote %d entries' % len(found))
        return 0
    allow = load_allow()
    new = sorted(found - allow)
    stale = sorted(allow - found)
    for path, rule in new:
        print('fw-lint NEW   %s %s' % (path, rule))
    for path, rule in stale:
        print('fw-lint FIXED %s %s  (remove it from tools/fw_lint_allow.txt)' % (path, rule))
    print('fw-lint: %d violations, %d allowed, %d new, %d stale' % (len(found), len(allow), len(new), len(stale)))
    return 1 if (new or stale) else 0


if __name__ == '__main__':
    sys.exit(main())
