"""CLI: ``python -m ground_station.research {import,list,show,analyze,query,workflow,campaign}``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .run import Run, Event, Note
from .store import Store, UAV_RUNS_DIR
from .analysis import analyse_run, generate_report, list_plugins, load_csv_columns
from .workflow import validate_workflow_file
from .executor import run_workflow, SimBackend, DashboardBackend
from .campaign import plan_campaign, next_point, campaign_is_within_envelope
from .finding import main as finding_main
from .catalog import main as catalog_main


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

    # --- workflow subcommand -----------------------------------------------
    p_workflow = sub.add_parser("workflow", help="workflow management")
    wf_sub = p_workflow.add_subparsers(dest="workflow_command")

    wf_validate = wf_sub.add_parser("validate", help="validate a workflow YAML")
    wf_validate.add_argument("workflow_path", help="path to workflow YAML")

    wf_dryrun = wf_sub.add_parser("dryrun", help="dry-run a workflow in sim")
    wf_dryrun.add_argument("workflow_path", help="path to workflow YAML")

    wf_run = wf_sub.add_parser("run", help="execute a workflow in sim")
    wf_run.add_argument("workflow_path", help="path to workflow YAML")

    # --- campaign subcommand -----------------------------------------------
    p_campaign = sub.add_parser("campaign", help="campaign planning")
    camp_sub = p_campaign.add_subparsers(dest="campaign_command")

    camp_plan = camp_sub.add_parser("plan", help="plan a campaign")
    camp_plan.add_argument("envelope_path", help="JSON envelope file")
    camp_plan.add_argument("--budget-steps", type=int, default=20)
    camp_plan.add_argument("--strategy", default="grid",
                           choices=("grid", "successive_halving"))

    # --- finding subcommand ----------------------------------------------
    p_finding = sub.add_parser("finding", help="findings channel")
    find_sub = p_finding.add_subparsers(dest="finding_command")
    find_list = find_sub.add_parser("list", help="list all findings")
    find_new = find_sub.add_parser("new", help="create a new finding")
    find_new.add_argument("finding_id", default="F-TEMP", help="finding ID")
    find_new.add_argument("--date", default="", help="date string")
    find_new.add_argument("--severity", default="medium",
                          choices=("low", "medium", "high", "critical"))
    find_new.add_argument("--status", default="open",
                          choices=("open", "in-progress", "resolved"))
    find_new.add_argument("--runs", default="", help="comma-separated run IDs")
    find_new.add_argument("--summary", default="", help="one-line summary")
    find_new.add_argument("--evidence", default="", help="evidence text")
    find_new.add_argument("--suggested-action", default="", help="suggested action")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    # --- finding subcommand (delegates to finding.py) --------------------
    if args.command == "finding":
        # Re-parse with the finding subcommands
        finding_argv = ["finding"]
        sub = args.finding_command or ""
        if sub == "new":
            finding_argv.extend(["new", args.finding_id or "F-TEMP",
                                 "--severity", args.finding_severity or "medium",
                                 "--status", args.finding_status or "open",
                                 "--summary", args.finding_summary or ""])
            if args.finding_date:
                finding_argv.extend(["--date", args.finding_date])
            if args.finding_runs:
                finding_argv.extend(["--runs", args.finding_runs])
            if args.finding_evidence:
                finding_argv.extend(["--evidence", args.finding_evidence])
            if args.finding_suggested_action:
                finding_argv.extend(["--suggested-action", args.finding_suggested_action])
        elif sub == "list":
            finding_argv.append("list")
        sys.argv = ["research"] + finding_argv
        return finding_main(finding_argv[1:])

    if args.command == "catalog":
        return catalog_main()

    cmds = {
        "import": cmd_import,
        "list": cmd_list,
        "show": cmd_show,
        "analyze": cmd_analyze,
        "query": cmd_query,
    }
    fn = cmds[args.command]
    return fn(args)


def cmd_workflow(args: argparse.Namespace) -> int:
    """Handle workflow subcommands: validate, dryrun, run."""
    sub = args.workflow_command
    path = Path(args.workflow_path)

    if sub == "validate":
        try:
            spec = validate_workflow_file(path)
            print(f"OK: {spec.name}")
            print(f"  hypothesis: {spec.hypothesis}")
            print(f"  phase: {spec.phase}")
            print(f"  variant: {spec.variant}")
            print(f"  steps: {len(spec.steps)}")
            if spec.envelope:
                print(f"  envelope: {len(spec.envelope)} bounds")
                for b in spec.envelope:
                    print(f"    {b.kind}:{b.key} = [{b.lo}, {b.hi}] ({b.mode})")
            print(f"  revert: {spec.revert}")
            return 0
        except (ValueError, FileNotFoundError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

    elif sub == "dryrun":
        try:
            spec = validate_workflow_file(path)
        except (ValueError, FileNotFoundError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        backend = SimBackend()
        try:
            run = backend.dry_run(spec)
            print(f"dry_run: {run.id}")
            print(f"  outcome: {run.outcome}")
            print(f"  metrics: {json.dumps(run.metrics, default=str)}")
            return 0
        except Exception as exc:
            print(f"dry_run FAILED: {exc}", file=sys.stderr)
            return 1

    elif sub == "run":
        try:
            spec = validate_workflow_file(path)
        except (ValueError, FileNotFoundError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        backend = SimBackend()
        store = Store()
        try:
            result = run_workflow(spec, backend, store=store)
            print(f"run: {result.run.id}")
            print(f"  outcome: {result.outcome}")
            print(f"  steps_run: {result.steps_run}")
            print(f"  sim_run_id: {result.sim_run_id}")
            store.close()
            return 0
        except Exception as exc:
            print(f"run FAILED: {exc}", file=sys.stderr)
            store.close()
            return 1

    else:
        print(f"unknown workflow subcommand: {sub}", file=sys.stderr)
        return 1


def cmd_campaign(args: argparse.Namespace) -> int:
    """Handle campaign subcommand: plan."""
    sub = args.campaign_command
    if sub != "plan":
        print(f"unknown campaign subcommand: {sub}", file=sys.stderr)
        return 1

    envelope_path = Path(args.envelope_path)
    if not envelope_path.exists():
        print(f"NOT FOUND: {envelope_path}", file=sys.stderr)
        return 1

    with open(envelope_path, encoding="utf-8") as fh:
        envelope_data = json.load(fh)

    from .campaign import CampaignEnvelope
    envelope = CampaignEnvelope.from_dict(envelope_data)
    budget = int(args.budget_steps)
    strategy = args.strategy

    plan = plan_campaign(envelope, budget, strategy=strategy)
    print(json.dumps(plan.to_dict(), indent=2, default=str))

    points = []
    p = next_point(plan)
    while p and len(points) < 5:
        points.append(p)
        p = next_point(plan)

    if points:
        print(f"\nfirst {len(points)} points:")
        for pt in points:
            print(f"  {json.dumps(pt.to_dict())}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
