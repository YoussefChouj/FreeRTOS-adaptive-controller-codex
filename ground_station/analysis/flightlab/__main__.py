"""CLI: python -m ground_station.analysis.flightlab {analyze,compare,ledger} (spec section 9).

Exit codes: 0 ok (skipped plugins included), 2 load failure, 1 internal error.
"""
from __future__ import annotations

import argparse
import sys
import traceback


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ground_station.analysis.flightlab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="analyze one flight")
    a.add_argument("src", help="VOFA stem (flight16) or path to <stem>.meta.json")
    a.add_argument("--out", default=None)
    a.add_argument("--pdf", action="store_true")
    a.add_argument("--no-html", action="store_true")
    a.add_argument("--no-ledger", action="store_true")
    c = sub.add_parser("compare", help="compare two analyzed flights")
    c.add_argument("a")
    c.add_argument("b")
    lg = sub.add_parser("ledger", help="ledger maintenance")
    lg.add_argument("--rebuild", action="store_true", required=True)
    args = ap.parse_args(argv)

    from .loaders import LoadError
    try:
        if args.cmd == "analyze":
            from .pipeline import analyze
            res = analyze(args.src, out_dir=args.out, pdf=args.pdf, html=not args.no_html,
                          ledger=not args.no_ledger)
            sev = [r.severity for r in res.recommendations]
            print(res.out_dir)
            print(f"{res.metrics['flight']['name']}: {len(res.metrics['plugins_run'])} plugins run, "
                  f"{len(res.metrics['plugins_skipped'])} skipped, {len(res.metrics['plugins_failed'])} failed; "
                  f"{sev.count('critical')} critical, {sev.count('warn')} warn, {sev.count('info')} info")
        elif args.cmd == "compare":
            from .compare import compare
            print(compare(args.a, args.b))
        else:
            from .ledger import rebuild
            print(rebuild())
    except LoadError as exc:
        print(f"load failed: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
