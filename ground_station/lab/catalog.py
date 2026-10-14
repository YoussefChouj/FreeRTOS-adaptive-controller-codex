"""The lab catalog, docs/lab/catalog.yaml (docs/lab/pipeline-design.md, decision 12).

    lab catalog [cards|modules|images]

Every firmware module, firmware image and card, with its status: idea -> sim-passed -> flown -> confirmed. The
queue runner refuses a card that is not in it; `lab end` moves the day's cards forward (flown, or confirmed when
the confirm passes) and adds the day's count of flights that count. The tools only move a status forward; set one
back by hand. The file's header (its comment lines before the first section) is kept as it is and the sections
are rewritten, so free text goes in an entry's `note:`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from ground_station.lab import cards
from ground_station.service.campaign_launch import ROOT

CATALOG = ROOT / "docs" / "lab" / "catalog.yaml"
STATUSES = ("idea", "sim-passed", "flown", "confirmed")
SECTIONS = ("modules", "images", "cards")


class CatalogError(ValueError):
    pass


def _rel(p):
    try:
        return Path(p).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def load(path=None):
    """{section: {name: entry}}; CatalogError names the file and every problem."""
    path = Path(path or CATALOG)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise CatalogError(f"{_rel(path)}: {e}") from e
    if not isinstance(raw, dict):
        raise CatalogError(f"{_rel(path)}: not a map of sections ({', '.join(SECTIONS)})")
    problems = [f"unknown section {s!r}" for s in raw if s not in SECTIONS]
    cat = {}
    for sec in SECTIONS:
        entries = raw.get(sec) or {}
        if not isinstance(entries, dict):
            problems.append(f"{sec}: not a map of name: {{status: ...}}")
            entries = {}
        cat[sec] = {}
        for name, e in entries.items():
            if not isinstance(e, dict) or e.get("status") not in STATUSES:
                problems.append(f"{sec}.{name}: status must be one of {', '.join(STATUSES)}")
                continue
            days = e.get("flown")
            if days is not None:
                if not isinstance(days, dict) or not all(isinstance(n, int) for n in days.values()):
                    problems.append(f"{sec}.{name}: flown must be {{<day>: <flights>}}")
                    continue
                e["flown"] = {str(d): n for d, n in days.items()}       # an unquoted 2026-10-15 loads as a date
            cat[sec][str(name)] = e
    if problems:
        raise CatalogError(f"{_rel(path)}: " + "; ".join(problems))
    return cat


def save(cat, path=None):
    """Rewrites the sections under the file's own header."""
    path = Path(path or CATALOG)
    head = []
    for line in (path.read_text(encoding="utf-8").splitlines() if path.exists() else []):
        if line[:1].isalpha():
            break
        head.append(line)
    body = yaml.safe_dump({s: cat.get(s) or {} for s in SECTIONS}, sort_keys=False, allow_unicode=True,
                          default_flow_style=None, width=1000)
    path.write_text("\n".join(head + [body.rstrip(), ""]), encoding="utf-8")


def later(a, b):
    """The further of two statuses."""
    return max(a, b, key=STATUSES.index)


def require_card(name, path=None):
    """The queue's gate (decision 12): a card that is not in the catalog does not fly."""
    try:
        known = load(path)["cards"]
    except CatalogError as e:
        raise cards.CardError([str(e)]) from e
    if name not in known:
        raise cards.CardError([f"card {name!r} is not in {_rel(path or CATALOG)}: add it under cards (status idea)"])


def _what(sec, name, e):
    if e.get("what") or sec != "cards":
        return e.get("what") or ""
    try:
        return cards.load_card(name).hypothesis
    except (cards.CardError, OSError):
        return "(no card file)"


def lines(cat, sections=SECTIONS):
    out = []
    for sec in sections:
        rows = cat[sec]
        out.append(f"{sec} ({len(rows)})")
        w = max([len(n) for n in rows] + [4])
        for name, e in rows.items():
            days = e.get("flown") or {}
            flown = f"{sum(days.values())} lab flights, last {max(days)}" if days else "-"
            note = f" [{e['note']}]" if e.get("note") else ""
            out.append(f"  {name:<{w}}  {e['status']:<10}  {flown:<27}  {_what(sec, name, e)}{note}")
        out += ["  none yet"] if not rows else []
        out.append("")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lab catalog", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("section", nargs="?", choices=SECTIONS, help="print one section only")
    a = ap.parse_args(argv)
    try:
        cat = load()
    except CatalogError as e:
        print(e)
        return 1
    print(f"lab catalog: {_rel(CATALOG)}, status {' -> '.join(STATUSES)}\n")
    print("\n".join(lines(cat, [a.section] if a.section else SECTIONS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
