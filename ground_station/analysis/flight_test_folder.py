"""Flight-test folder organization and background analysis launcher.

Handles:
  1. Creating organized flight-test folders from session directories
  2. Copying raw logs or creating session_path.txt pointers
  3. Appending to flight_tests/<date>/index.csv
  4. Launching analysis as a background subprocess
  5. Tracking analysis status (pending / running / done / failed)
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ground_station.analysis.flight_report import generate_report


def _get_project_root() -> Path:
    """Derive project root from this module's location."""
    return Path(__file__).resolve().parents[2]  # ground_station/analysis/<this> -> repo root


def create_flight_test_folder(
    session_dir: Path | str,
    controller: str = "unknown",
    payload: str = "unknown",
    label: str = "",
) -> Path:
    """Create an organized flight-test folder.

    Creates logs/flight_tests/<YYYY-MM-DD>/<HHMMSS>_<controller>_<payload>[_<label>]/
    containing raw/ and the analysis outputs.

    Returns the output directory path.
    """
    session_dir = Path(session_dir)
    project_root = _get_project_root()
    flight_tests_root = project_root / "logs" / "flight_tests"
    flight_tests_root.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    safe_label = label.replace(" ", "_").replace("/", "_")[:30]
    folder_name = f"{time_str}_{controller}_{payload}"
    if safe_label:
        folder_name += f"_{safe_label}"

    date_dir = flight_tests_root / date_str
    date_dir.mkdir(parents=True, exist_ok=True)

    output_dir = date_dir / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # raw/ folder
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    # Copy session files or create pointer
    session_files = list(session_dir.iterdir())
    total_size = sum(f.stat().st_size for f in session_files if f.is_file())

    if total_size < 200 * 1024 * 1024:  # 200 MB
        # Copy files
        for f in session_files:
            if f.is_file():
                shutil.copy2(f, raw_dir / f.name)
    else:
        # Create pointer
        with open(raw_dir / "session_path.txt", "w", encoding="utf-8") as pf:
            pf.write(str(session_dir))

    return output_dir


def update_index_csv(
    session_dir: Path | str,
    controller: str = "unknown",
    payload: str = "unknown",
    label: str = "",
    output_path: str = "",
) -> None:
    """Append a row to logs/flight_tests/<date>/index.csv."""
    session_dir = Path(session_dir)
    project_root = _get_project_root()
    flight_tests_root = project_root / "logs" / "flight_tests"

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    index_path = flight_tests_root / date_str / "index.csv"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not index_path.exists() or index_path.stat().st_size == 0

    with open(index_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "timestamp", "controller", "payload", "label",
                "session_dir", "output_path", "analysis_status",
            ])
        writer.writerow([
            now.isoformat(),
            controller,
            payload,
            label or "",
            str(session_dir),
            output_path,
            "pending",
        ])


def _run_analysis_subprocess(
    session_dir: Path | str,
    output_dir: Path | str,
    controller: str = "unknown",
    payload: str = "unknown",
    notes: str = "",
    preset: str = "",
) -> subprocess.Popen:
    """Launch the analysis as a background subprocess.

    Arguments travel as JSON on argv (notes are free text; splicing them into
    source code broke on quotes). The child records done/failed on exit.
    """
    args = {
        "session_dir": str(session_dir), "output_dir": str(output_dir),
        "controller": controller, "payload": payload, "notes": notes,
        "preset": preset,
    }
    cmd = [sys.executable, "-m", "ground_station.analysis.flight_test_folder",
           json.dumps(args)]
    return subprocess.Popen(cmd, cwd=str(_get_project_root()))


def _set_index_status(output_dir: Path | str, status: str) -> None:
    """Set analysis_status on the index.csv row whose output_path is output_dir."""
    output_dir = Path(output_dir)
    index_path = output_dir.parent.parent / "index.csv"  # <date>/<run>/report
    if not index_path.exists():
        return
    with open(index_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for row in rows[1:]:
        if len(row) >= 7 and Path(row[5]) == output_dir:
            row[6] = status
    with open(index_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


def _analysis_main(args: dict[str, str]) -> int:
    """Child-process entry: build the report, then record done or failed."""
    session_id = Path(args["session_dir"]).name
    status = "failed"
    try:
        generate_report(
            session_dir=args["session_dir"], out_dir=args["output_dir"],
            notes=args["notes"], controller=args["controller"],
            payload=args["payload"], preset=args.get("preset", ""),
        )
        status = "done"
    finally:
        update_analysis_status(session_id, status, output_path=args["output_dir"])
        _set_index_status(args["output_dir"], status)
    return 0


# ---------------------------------------------------------------------------
# Analysis status tracking
# ---------------------------------------------------------------------------

def _get_status_file() -> Path:
    project_root = _get_project_root()
    status_path = project_root / "logs" / "flight_tests" / "analysis_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    return status_path


def load_analysis_status() -> dict[str, Any]:
    """Load analysis status from file."""
    status_path = _get_status_file()
    if status_path.exists():
        try:
            with open(status_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def update_analysis_status(
    session_id: str,
    status: str,
    output_path: str = "",
) -> None:
    """Update analysis status for a session."""
    status_path = _get_status_file()
    status_data = load_analysis_status()
    status_data[session_id] = {
        "status": status,
        "output_path": output_path,
        "updated_at": datetime.now().isoformat(),
    }
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2)


def run_analysis_and_track(
    session_dir: Path | str,
    controller: str = "unknown",
    payload: str = "unknown",
    label: str = "",
    notes: str = "",
    preset: str = "",
) -> tuple[Path, subprocess.Popen]:
    """Create folder, update index, launch analysis, track status.

    Returns (flight_test_dir, subprocess).
    """
    session_dir = Path(session_dir)
    session_id = session_dir.name

    # Step 1: Create organized flight-test folder
    output_dir = create_flight_test_folder(
        session_dir, controller=controller, payload=payload, label=label
    )
    report_dir = output_dir / "report"

    # Step 2: Update index with pending status
    update_index_csv(
        session_dir, controller=controller, payload=payload,
        label=label, output_path=str(report_dir)
    )

    # Step 3: Update status tracking
    update_analysis_status(session_id, "pending", output_path=str(report_dir))

    # Step 4: Mark running before launch, so a fast child's done/failed is not overwritten
    update_analysis_status(session_id, "running", output_path=str(report_dir))
    _set_index_status(report_dir, "running")
    proc = _run_analysis_subprocess(
        session_dir, report_dir,
        controller=controller, payload=payload, notes=notes, preset=preset
    )

    return output_dir, proc


def list_flight_tests(date_str: str | None = None) -> list[dict[str, Any]]:
    """List all flight tests, optionally filtered by date.

    Reads from logs/flight_tests/<date>/index.csv files.
    """
    project_root = _get_project_root()
    flight_tests_root = project_root / "logs" / "flight_tests"
    results = []

    if date_str:
        date_dir = flight_tests_root / date_str
        if date_dir.exists():
            index_path = date_dir / "index.csv"
            if index_path.exists():
                with open(index_path, encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        results.append(dict(row))
    else:
        if not flight_tests_root.exists():
            return results
        for date_dir in sorted(flight_tests_root.iterdir()):
            if date_dir.is_dir():
                index_path = date_dir / "index.csv"
                if index_path.exists():
                    with open(index_path, encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            row["date"] = date_dir.name
                            results.append(dict(row))

    # Enrich with status tracking
    status_data = load_analysis_status()
    for entry in results:
        sid = entry.get("session_dir", "")
        # Find matching status
        for sid_key, sdata in status_data.items():
            if sid_key in sid:
                entry["analysis_status"] = sdata.get("status", entry.get("analysis_status", "unknown"))
                break

    return results


if __name__ == "__main__":
    sys.exit(_analysis_main(json.loads(sys.argv[1])))
