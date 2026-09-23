"""CLI: ``python -m ground_station.research {import,list,show,analyze,query}``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .run import Run, Event, Note
from .store import Store, UAV_RUNS_DIR
from .analysis import analyse_run, generate_report, list_plugins, load_csv_columns


def cmd_import(args: argparse.Namespace) -> int:
    store = Store()
    path = Path(args.path)
    if not path.exists():
        print(f"NOT FOUND: {path}", file=sys.stderr)
        store.close()
        return 1
    run = store.import_capture(str(path), kind=args.kind)
    print(f"imported {path} -> {run.id}")
    store.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = Store()
    where = ""
    params: tuple = ()
    if args.kind:
        where = "kind = ?"
        params = (args.kind,)
    if args.phase:
        if where:
            where += " AND phase = ?"
        else:
            where = "phase = ?"
        params += (args.phase,)
    runs = list(store.query(where, params))
    if not runs:
        print("no runs found")
    for run in runs:
        print(f"{run.id}  {run.kind:12s}  {run.phase:10s}  {run.outcome or '-':6s}")
    store.close()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = Store()
    run = store.get(args.id)
    if run is None:
        print(f"NOT FOUND: {args.id}", file=sys.stderr)
        store.close()
        return 1
    print(run.to_json())
    store.close()
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    store = Store()
    run = store.get(args.id)
    if run is None:
        print(f"NOT FOUND: {args.id}", file=sys.stderr)
        store.close()
        return 1

    # Load data from captures
    run_dir = Path(store.runs_dir / args.id)
    data: dict = {}
    cap_dir = run_dir / "captures"
    if cap_dir.exists():
        for cap in cap_dir.iterdir():
            if cap.suffix in (".csv", ".parquet"):
                col = load_csv_columns(str(cap))
                data.update(col)

    metrics = analyse_run(run, data, axis=args.axis)
    run.metrics.update(metrics)
    store.update(run)

    report_path = generate_report(run, metrics, run_dir)
    print(f"metrics: {json.dumps({k: v for k, v in metrics.items() if isinstance(v, (int, float, str))}, indent=2)}")
    print(f"report:  {report_path}")
    store.close()
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    store = Store()
    if not args.sql:
        print("Usage: ground_station.research query 'WHERE kind = ?' 'experiment'",
              file=sys.stderr)
        store.close()
        return 1
    params = tuple(args.params) if args.params else ()
    for run in store.query(args.sql, params):
        print(json.dumps({
            "id": run.id,
            "kind": run.kind,
            "phase": run.phase,
            "outcome": run.outcome,
        }))
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ground_station.research",
        description="Research platform: run records, index, deterministic analysis",
    )
    sub = parser.add_subparsers(dest="command")

    p_import = sub.add_parser("import", help="import a capture file as a Run")
    p_import.add_argument("path", help="path to capture file")
    p_import.add_argument("--kind", default="debug", help="run kind")

    p_list = sub.add_parser("list", help="list runs")
    p_list.add_argument("--kind", help="filter by kind")
    p_list.add_argument("--phase", help="filter by phase")

    p_show = sub.add_parser("show", help="show a single run")
    p_show.add_argument("id", help="run id")

    p_analyze = sub.add_parser("analyze", help="analyze a run")
    p_analyze.add_argument("id", help="run id")
    p_analyze.add_argument("--axis", default="default", help="axis prefix")

    p_query = sub.add_parser("query", help="run a SQL query on the index")
    p_query.add_argument("sql", help="WHERE clause (without keyword)")
    p_query.add_argument("params", nargs="*", help="bound values")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    cmds = {
        "import": cmd_import,
        "list": cmd_list,
        "show": cmd_show,
        "analyze": cmd_analyze,
        "query": cmd_query,
    }
    fn = cmds[args.command]
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
