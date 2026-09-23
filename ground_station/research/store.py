"""Run storage: data directory, SQLite index, import helpers.

Location: ``UAV_RUNS_DIR`` env var, default ``D:/uav-runs`` when D: exists,
else ``~/uav-runs``.

Layout::

    <dir>/runs/<id>/run.json
    <dir>/runs/<id>/captures/  (parquet or csv per capture)
    <dir>/index.sqlite          (one row per run + metrics table)

API: ``create``, ``get``, ``update``, ``query``, ``import_capture``.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

from .run import Run

# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------


def _resolve_data_dir() -> Path:
    env = os.environ.get("UAV_RUNS_DIR")
    if env:
        return Path(env)
    if Path("D:/").exists():
        return Path("D:/uav-runs")
    return Path.home() / "uav-runs"


def _default_data_dir() -> Path:
    """Return the default data dir (cached once at first call)."""
    if not hasattr(_default_data_dir, "_cache"):
        _default_data_dir._cache = _resolve_data_dir()
    return _default_data_dir._cache


UAV_RUNS_DIR: Path = _default_data_dir()

# ---------------------------------------------------------------------------
# SQLite schema helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id   TEXT PRIMARY KEY,
    kind     TEXT,
    intent   TEXT,
    phase    TEXT,
    tags     TEXT,
    created_at TEXT,
    outcome  TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    run_id  TEXT,
    name    TEXT,
    value   REAL,
    PRIMARY KEY (run_id, name),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


def _ensure_db(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)

# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class Store:
    """Manages the ``<dir>/index.sqlite`` and ``<dir>/runs/<id>/`` tree."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else _resolve_data_dir()
        self.runs_dir = self.data_dir / "runs"
        self.db_path = self.data_dir / "index.sqlite"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.db_path))
        _ensure_db(self._db)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    # -- CRUD ---------------------------------------------------------------

    def create(self, run: Run) -> str:
        """Persist *run* and return its id."""
        run_dir = self.runs_dir / run.id
        run_dir.mkdir(parents=True, exist_ok=True)

        run.save_json(run_dir / "run.json")

        self._index_run(run)
        self._index_metrics(run)
        return run.id

    def get(self, run_id: str) -> Run | None:
        run_dir = self.runs_dir / run_id
        if not run_dir.exists():
            return None
        return Run.load_json(run_dir / "run.json")

    def update(self, run: Run) -> None:
        run_dir = self.runs_dir / run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        run.save_json(run_dir / "run.json")
        self._index_run(run)
        self._index_metrics(run)

    def query(self, sql_where: str = "",
              params: tuple = ()) -> Iterator[Run]:
        """Run a query against the index and yield ``Run`` objects.

        *sql_where* is the WHERE clause (without the keyword) and *params*
        are bound values.  Common example: ``"kind = ?"`` with ``( "experiment", )``.
        """
        sql = "SELECT run_id FROM runs"
        if sql_where:
            sql += " WHERE " + sql_where
        sql += " ORDER BY created_at DESC"
        for row in self._db.execute(sql, params):
            run = self.get(row[0])
            if run is not None:
                yield run

    # -- imports -------------------------------------------------------------

    def import_capture(self, path: str | Path, kind: str = "csv",
                       **meta: Any) -> Run:
        """Import an existing capture file as a new ``Run``.

        Accepts:
        * ``stream_log`` CSV (``t_src_ms, t_host_s, seq, ...``)
        * ``capture_preset`` CSV (``sample_idx, tick, data_hex``)
        * service session recordings (telemetry CSV + events.jsonl)

        Converts the capture to Parquet (``pyarrow``) if available, else
        keeps the original CSV.  Returns the newly created ``Run``.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        run = Run(kind=kind, captures=[str(path)])
        run_dir = self.runs_dir / run.id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Convert to parquet/csv in captures/
        captures_dir = run_dir / "captures"
        captures_dir.mkdir(exist_ok=True)
        self._convert_capture(path, captures_dir, meta)

        converted = [str(captures_dir / p.name)
                     for p in captures_dir.iterdir()]

        # Build a new frozen Run with updated captures
        run_dict = run.to_dict()
        run_dict["captures"] = converted
        updated_run = Run(**run_dict)
        self.create(updated_run)
        return updated_run

    # -- metrics -------------------------------------------------------------

    def set_metrics(self, run_id: str, metrics: dict[str, Any]) -> None:
        cur = self._db.execute(
            "SELECT name FROM metrics WHERE run_id=?", (run_id,))
        existing = {r[0] for r in cur.fetchall()}
        new_names = set(metrics.keys())

        # Delete old metrics for this run so we replace them entirely
        self._db.execute("DELETE FROM metrics WHERE run_id=?", (run_id,))
        for name, value in metrics.items():
            if value is not None:
                self._db.execute(
                    "INSERT INTO metrics (run_id, name, value) VALUES (?, ?, ?)",
                    (run_id, name, float(value)),
                )

    # -- internal helpers ----------------------------------------------------

    def _index_run(self, run: Run) -> None:
        self._db.execute(
            """INSERT OR REPLACE INTO runs
               (run_id, kind, intent, phase, tags, created_at, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run.id, run.kind, run.intent, run.phase,
             ",".join(run.tags), run.created_at, run.outcome),
        )
        self._db.commit()

    def _index_metrics(self, run: Run) -> None:
        self.set_metrics(run.id, run.metrics)

    def _convert_capture(self, src: Path, dst_dir: Path,
                         meta: dict) -> None:
        """Convert a capture file to parquet (or leave as CSV)."""
        suffix = src.suffix.lower()
        if suffix == ".csv":
            self._csv_to_parquet_or_csv(src, dst_dir, meta)
        else:
            # Copy as-is
            dst = dst_dir / src.name
            dst.write_bytes(src.read_bytes())

    def _csv_to_parquet_or_csv(self, src: Path, dst_dir: Path,
                               meta: dict) -> None:
        """Read a CSV, write parquet if pyarrow is available, else CSV."""
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
            self._write_parquet(src, dst_dir, meta)
        except ImportError:
            self._write_csv_copy(src, dst_dir, meta)

    def _write_parquet(self, src: Path, dst_dir: Path,
                       meta: dict) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows = list(csv.DictReader(src.read_text(encoding="utf-8")))
        if not rows:
            return
        # Build list-of-dicts for pyarrow (simpler than dict-of-lists)
        table = pa.Table.from_pandas(None) if False else None  # placeholder
        # Simpler: just write via pandas-like list-of-dicts
        try:
            import pyarrow as pa  # noqa: F811
            # Convert each row to typed dicts
            typed_rows: list[dict[str, Any]] = []
            for row in rows:
                typed: dict[str, Any] = {}
                for key, val in row.items():
                    try:
                        typed[key] = float(val)
                    except (ValueError, TypeError):
                        typed[key] = val
                typed_rows.append(typed)
            table = pa.Table.from_pylist(typed_rows)
        except Exception:
            self._write_csv_copy(src, dst_dir, meta)
            return

        out = dst_dir / (src.stem + ".parquet")
        pq.write_table(table, str(out))

    def _write_csv_copy(self, src: Path, dst_dir: Path,
                        meta: dict) -> None:
        dst = dst_dir / src.name
        dst.write_bytes(src.read_bytes())
