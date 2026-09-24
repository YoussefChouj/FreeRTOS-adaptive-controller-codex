"""Research findings channel: create, list, and manage findings.

Findings live in ``docs/research-platform/findings/<id>.md`` with YAML
frontmatter followed by a ``---`` separator and Markdown body.

Frontmatter fields:
    id, date, severity, status (open|in-progress|resolved),
    runs (list of run IDs), summary, evidence, suggested_action.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FINDINGS_DIR = Path(__file__).resolve().parents[3] / "docs" / "research-platform" / "findings"

# Frontmatter regex: captures key: value lines before the --- separator
_FM_RE = re.compile(
    r"^id:\s*(.+)$\n"
    r"^date:\s*(.+)$\n"
    r"^severity:\s*(.+)$\n"
    r"^status:\s*(.+)$\n"
    r"^runs:\s*(.+)$\n"
    r"^summary:\s*(.+)$",
    re.MULTILINE,
)


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Parse the frontmatter section from a finding file."""
    m = _FM_RE.match(text)
    if not m:
        return None
    runs_raw = m.group(5).strip()
    if runs_raw.startswith("[") and runs_raw.endswith("]"):
        try:
            runs = json.loads(runs_raw)
        except json.JSONDecodeError:
            runs = []
    else:
        runs = [r.strip() for r in runs_raw.split(",") if r.strip()]
    return {
        "id": m.group(1).strip(),
        "date": m.group(2).strip(),
        "severity": m.group(3).strip(),
        "status": m.group(4).strip(),
        "runs": runs,
        "summary": m.group(6).strip(),
    }


def list_findings() -> list[dict[str, Any]]:
    """List all findings with their frontmatter."""
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for f in sorted(FINDINGS_DIR.glob("*.md")):
        if f.name == "TEMPLATE.md":
            continue
        text = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm:
            fm["file"] = str(f.relative_to(FINDINGS_DIR.parents[2]))
            results.append(fm)
    return results


def create_finding(fid: str, date: str, severity: str,
                   status: str, runs: list[str], summary: str,
                   evidence: str = "", suggested_action: str = "",
                   findings_dir: Path | None = None) -> Path:
    """Create a new finding file."""
    d = findings_dir or FINDINGS_DIR
    d.mkdir(parents=True, exist_ok=True)
    frontmatter = [
        f"id: {fid}",
        f"date: {date}",
        f"severity: {severity}",
        f"status: {status}",
        f"runs: {json.dumps(runs)}",
        f"summary: {summary}",
        "",
        "---",
        "",
    ]
    body = []
    if evidence:
        body.append("## Evidence")
        body.append(evidence)
        body.append("")
    if suggested_action:
        body.append("## Suggested Action")
        body.append(suggested_action)
        body.append("")
    content = "\n".join(frontmatter) + "\n".join(body)
    fpath = d / f"{fid}.md"
    fpath.write_text(content, encoding="utf-8")
    return fpath


def cmd_finding_list(args: argparse.Namespace) -> int:
    findings = list_findings()
    if not findings:
        print("no findings found")
        return 0
    for f in findings:
        print(f"{f['id']:12s}  {f['severity']:10s}  {f['status']:12s}  "
              f"{f['date']:12s}  {f['summary'][:60]}")
    return 0


def cmd_finding_new(args: argparse.Namespace) -> int:
    runs = []
    if args.runs:
        runs = [r.strip() for r in args.runs.split(",") if r.strip()]
    fpath = create_finding(
        fid=args.id,
        date=args.date or "",
        severity=args.severity or "medium",
        status=args.status or "open",
        runs=runs,
        summary=args.summary or "",
        evidence=getattr(args, "evidence", "") or "",
        suggested_action=getattr(args, "suggested_action", "") or "",
    )
    print(f"created: {fpath}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ground_station.research.finding",
        description="Research findings channel: create, list, manage.",
    )
    sub = parser.add_subparsers(dest="command")

    # --- finding subcommand ----------------------------------------------
    f_list = sub.add_parser("list", help="list all findings")
    f_new = sub.add_parser("new", help="create a new finding")
    f_new.add_argument("id", help="finding ID (e.g. F-001)")
    f_new.add_argument("--date", default="", help="date string")
    f_new.add_argument("--severity", default="medium",
                       choices=("low", "medium", "high", "critical"))
    f_new.add_argument("--status", default="open",
                       choices=("open", "in-progress", "resolved"))
    f_new.add_argument("--runs", default="", help="comma-separated run IDs")
    f_new.add_argument("--summary", default="", help="one-line summary")
    f_new.add_argument("--evidence", default="", help="evidence text")
    f_new.add_argument("--suggested-action", default="", help="suggested action")

    args = parser.parse_args(argv)
    if not args:
        parser.print_help()
        return 0
    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "list":
        return cmd_finding_list(args)
    if args.command == "new":
        runs = []
        if args.runs:
            runs = [r.strip() for r in args.runs.split(",") if r.strip()]
        fpath = create_finding(
            fid=args.id,
            date=args.date or "",
            severity=args.severity or "medium",
            status=args.status or "open",
            runs=runs,
            summary=args.summary or "",
            evidence=getattr(args, "evidence", "") or "",
            suggested_action=getattr(args, "suggested_action", "") or "",
        )
        print(f"created: {fpath}")
        return 0

    print(f"unknown subcommand: {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
