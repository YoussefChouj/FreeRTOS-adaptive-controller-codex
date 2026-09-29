"""load(path) -> FlightLog. Dispatches on the source format.

Owned by the supervisor (WP0). Accepts a VOFA stem ("flight16"), a *.meta.json path, or a
dashboard session directory (v2). Any failure to read the source raises LoadError (CLI exit 2).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
VOFA_DIR = REPO_ROOT / "logs" / "vofa"


class LoadError(Exception):
    pass


def resolve(src) -> Path:
    p = Path(src)
    if p.exists():
        return p
    cand = VOFA_DIR / f"{src}.meta.json"
    if cand.exists():
        return cand
    raise LoadError(f"no such log: {src} (also tried {cand})")


def load(src):
    p = resolve(src)
    if p.is_dir():
        from .session import load_session
        return load_session(p)
    if p.name.endswith(".meta.json"):
        from .vofa import load_vofa
        try:
            return load_vofa(p)
        except LoadError:
            raise
        except (OSError, ValueError, KeyError) as exc:
            raise LoadError(f"{p.name}: {type(exc).__name__}: {exc}") from exc
    raise LoadError(f"unsupported source: {p}")
