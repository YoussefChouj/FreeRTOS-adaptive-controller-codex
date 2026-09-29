"""analyze(): load -> segments -> plugins -> rules -> render -> ledger (spec sections 3, 8).

Owned by the supervisor (WP0). Workers must not edit this file.

Stage modules owned by workers are imported when analyze() runs:
  segments.segment(log, cfg) -> {"armed","airborne","landing","steady": [(t0, t1), ...], "warnings"?: [str]}
  report.render_md.render(metrics, recs, figs, out_dir) -> Path
  report.render_html.render(metrics, recs, figs, out_dir) -> Path
  ledger.read_rows(path) -> list[dict]
  ledger.upsert(metrics, recs, path) -> None
"""
from __future__ import annotations

import importlib
import json
import math
import pkgutil
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from . import registry
from .loaders import load
from .model import FlightLog

SCHEMA_VERSION = 1
PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parents[2]
CONFIG_DIR = PKG_DIR / "config"
SCHEMA_PATH = PKG_DIR / "schema" / "metrics.schema.json"
REPORTS_DIR = REPO_ROOT / "logs" / "vofa" / "reports"
LEDGER_PATH = REPO_ROOT / "docs" / "flights" / "ledger.csv"
PLUGIN_KEYS = ("data_quality", "loops", "motors", "battery", "position", "spectrum", "mrac")
SEGMENT_KEYS = ("armed", "airborne", "landing", "steady")


class SchemaError(Exception):
    pass


@dataclass
class AnalysisResult:
    out_dir: Path
    metrics: dict
    recommendations: list


# ---------------------------------------------------------------- config

def load_config(config_dir: Path = CONFIG_DIR) -> dict:
    """Merge the top-level keys of loops.yaml, rules.yaml, battery.yaml into one dict."""
    cfg: dict = {}
    for fname in ("loops.yaml", "rules.yaml", "battery.yaml"):
        data = yaml.safe_load((Path(config_dir) / fname).read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            if k in cfg:
                raise ValueError(f"config key {k!r} defined twice ({fname})")
            cfg[k] = v
    return cfg


# ---------------------------------------------------------------- discovery

def discover(pkg_name: str) -> list[str]:
    """Import every module of flightlab.<pkg_name> so their decorators register."""
    pkg = importlib.import_module(f"{__package__}.{pkg_name}")
    names = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        importlib.import_module(f"{pkg.__name__}.{info.name}")
        names.append(info.name)
    return names


# ---------------------------------------------------------------- json hygiene

def jsonify(x):
    """numpy scalars -> python, NaN/inf -> None, tuples -> lists. Recursive."""
    if isinstance(x, dict):
        return {str(k): jsonify(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonify(v) for v in x]
    if isinstance(x, np.ndarray):
        return [jsonify(v) for v in x.tolist()]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    return x


# ---------------------------------------------------------------- stages

def run_plugins(log: FlightLog, segs: dict, cfg: dict, plugins=None) -> dict:
    """Returns {"results": {name: dict}, "skipped": {name: [missing]}, "failed": {name: tb}, "warnings": [str]}."""
    plugins = sorted((plugins if plugins is not None else registry.PLUGINS.values()), key=registry.plugin_order)
    results, skipped, failed, warns = {}, {}, {}, []
    for p in plugins:
        try:
            missing = list(p.requires(log, cfg))
        except Exception:
            failed[p.name] = traceback.format_exc()
            continue
        if missing:
            skipped[p.name] = missing
            continue
        try:
            res = p.run(log, segs, cfg)
            if not isinstance(res, dict):
                raise TypeError(f"plugin {p.name} returned {type(res).__name__}, expected dict")
        except Exception:
            failed[p.name] = traceback.format_exc()
            continue
        res = jsonify(res)
        for w in res.pop("warnings", None) or []:
            warns.append(f"{p.name}: {w}")
        results[p.name] = res
    return {"results": results, "skipped": skipped, "failed": failed, "warnings": warns}


def run_figures(log, segs, cfg, plugin_names, out_dir: Path) -> tuple[list[Path], list[str]]:
    figs, warns = [], []
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in plugin_names:
        p = registry.PLUGINS[name]
        try:
            figs.extend(Path(f) for f in p.figures(log, segs, cfg, out_dir))
        except Exception as exc:
            warns.append(f"{name}: figures failed: {type(exc).__name__}: {exc}")
    return figs, warns


def run_rules(metrics: dict, cfg: dict, ctx: dict, rules=None) -> tuple[list, dict, dict]:
    """Returns (sorted recommendations, skipped {rule: [paths]}, failed {rule: traceback})."""
    entries = rules if rules is not None else list(registry.RULES.values())
    recs, skipped, failed = [], {}, {}
    for e in sorted(entries, key=lambda e: e.name):
        missing = [p for p in e.requires if registry.get_path(metrics, p) is None]
        if missing:
            skipped[e.name] = missing
            continue
        try:
            out = e.fn(metrics, cfg, ctx) or []
            for r in out:
                if not isinstance(r, registry.Recommendation):
                    raise TypeError(f"rule {e.name} returned {type(r).__name__}")
            recs.extend(out)
        except Exception:
            failed[e.name] = traceback.format_exc()
    return registry.sort_recommendations(recs), skipped, failed


def _intervals_mask(t: np.ndarray, intervals) -> np.ndarray:
    m = np.zeros(t.shape, dtype=bool)
    for a, b in intervals or []:
        m |= (t >= a) & (t < b)
    return m


def controller_info(log: FlightLog, segs: dict) -> dict:
    """mrac_mode (off/shadow/active, most frequent over airborne, else whole log) and ctrl_select."""
    out = {"mrac_mode": None, "ctrl_select": None}
    ad, inj = "mrac_flags.adaptation_on", "mrac_flags.output_injection_on"
    air = segs.get("airborne") or []
    if log.has(ad) and log.has(inj):
        t, a = log.aligned([ad, inj])
        m = _intervals_mask(t, air) if air else np.ones(t.shape, dtype=bool)
        m &= np.isfinite(a[ad]) & np.isfinite(a[inj])
        if m.any():
            on, injon = a[ad][m] > 0.5, a[inj][m] > 0.5
            counts = {"off": int((~on).sum()), "shadow": int((on & ~injon).sum()), "active": int((on & injon).sum())}
            out["mrac_mode"] = max(counts, key=counts.get)
    if log.has("g_ctrl_select"):
        s = log.get("g_ctrl_select")
        m = (_intervals_mask(s.t, air) if air else np.ones(s.t.shape, dtype=bool)) & np.isfinite(s.v)
        if m.any():
            vals, cnt = np.unique(np.round(s.v[m]).astype(int), return_counts=True)
            out["ctrl_select"] = int(vals[np.argmax(cnt)])
    return out


def flight_info(log: FlightLog) -> dict:
    m = log.meta or {}
    preset = m.get("preset") or {}
    return {
        "name": log.name, "source_format": log.source_format,
        "started_at": m.get("started_at"), "ended_at": m.get("ended_at"),
        "git": m.get("git"), "elf_mtime": m.get("elf_mtime"), "notes": m.get("notes"),
        "preset": preset.get("name") if isinstance(preset, dict) else preset,
        "duration_s": log.duration_s,
    }


def build_metrics(log: FlightLog, segs: dict, pr: dict, extra_warnings=()) -> dict:
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "flight": flight_info(log),
        "segments": {k: [[a, b] for a, b in segs.get(k, [])] for k in SEGMENT_KEYS},
        "controller": controller_info(log, segs),
    }
    for k in PLUGIN_KEYS:
        metrics[k] = pr["results"].get(k)
    for k, v in pr["results"].items():          # plugins added after v1 keep their own key
        metrics.setdefault(k, v)
    metrics["plugins_run"] = sorted(pr["results"])
    metrics["plugins_skipped"] = pr["skipped"]
    metrics["plugins_failed"] = pr["failed"]
    metrics["warnings"] = list(extra_warnings) + pr["warnings"]
    return jsonify(metrics)


def validate(metrics: dict, schema_path: Path = SCHEMA_PATH) -> None:
    import jsonschema
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(metrics), key=lambda e: list(e.path))
    if errors:
        lines = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:20]]
        raise SchemaError("metrics.json violates schema:\n" + "\n".join(lines))


# ---------------------------------------------------------------- entry point

def analyze(src, out_dir=None, pdf=False, html=True, ledger=True, cfg=None) -> AnalysisResult:
    log = load(src)
    cfg = dict(cfg if cfg is not None else load_config())
    cfg["runtime"] = {"pdf": bool(pdf)}
    out_dir = Path(out_dir) if out_dir else REPORTS_DIR / log.name
    out_dir.mkdir(parents=True, exist_ok=True)

    from . import segments as seg_mod
    segs = dict(seg_mod.segment(log, cfg))
    seg_warnings = [f"segments: {w}" for w in segs.pop("warnings", None) or []]

    discover("plugins")
    discover("rules")
    pr = run_plugins(log, segs, cfg)
    metrics = build_metrics(log, segs, pr, seg_warnings)
    validate(metrics)

    ledger_mod = importlib.import_module(f"{__package__}.ledger") if ledger else None
    ctx = {"ledger_rows": ledger_mod.read_rows(LEDGER_PATH) if ledger_mod else [], "log": log}
    recs, rules_skipped, rules_failed = run_rules(metrics, cfg, ctx)

    figs, fig_warnings = run_figures(log, segs, cfg, metrics["plugins_run"], out_dir / "figs")
    metrics["warnings"].extend(fig_warnings)

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    rec_doc = {
        "schema_version": SCHEMA_VERSION, "flight": metrics["flight"]["name"],
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        "recommendations": [jsonify(r.to_dict()) for r in recs],
        "rules_skipped": rules_skipped, "rules_failed": rules_failed,
    }
    (out_dir / "recommendations.json").write_text(json.dumps(rec_doc, indent=2), encoding="utf-8")

    from .report import render_md
    render_md.render(metrics, recs, figs, out_dir)
    if html:
        from .report import render_html
        render_html.render(metrics, recs, figs, out_dir)
    if ledger_mod:
        ledger_mod.upsert(metrics, recs, LEDGER_PATH)
    return AnalysisResult(out_dir, metrics, recs)
