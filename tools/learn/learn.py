#!/usr/bin/env python3
"""LearnHarness CLI and local viewer API server (stdlib only).

Maintains 100% on-disk compatibility with amosblomqvist/learnharness native v1
format (skills/learnharness/references/data-format.md) while adding session
plans, card event logs, adaptive practice tracking, helper job dispatch, and
the local viewer HTTP JSON API on port 8770.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "docs" / "learning" / "harness" / "data"
DEFAULT_INTERNSHIP_DIR = REPO_ROOT / "docs" / "learning" / "internship"
DEFAULT_VIEWER_DIR = Path(__file__).resolve().parent / "viewer"
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"

COMPACT_BYTE_LIMIT = 1450

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "agent": {
        "tools": {
            "explain": False,
        }
    },
    "models": {
        "strong": {
            "model": {
                "provider": "claude-code",
                "id": "claude-desktop",
            },
            "thinkingLevel": "low",
        },
        "cheap": {
            "model": {
                "provider": "claude-code",
                "id": "claude-desktop",
            },
            "thinkingLevel": "low",
        },
        "vision": {
            "model": {
                "provider": "claude-code",
                "id": "claude-desktop",
            },
            "thinkingLevel": "low",
        },
    },
    "compaction": {
        "thresholdTokens": 50000,
    },
    "helper_worker": {
        "research": "argon",
        "demo": "argon",
        "pdf": "inline",
        "visual": "inline",
        "default": "inline",
    },
}

DEFAULT_RENDER_SETTINGS: dict[str, Any] = {
    "sizeUnits": "world",
    "nodeSize": 2,
    "edgeWidth": 1,
    "arrowLength": 2,
    "labelZoom": 0.5,
    "labelSize": 3,
    "clusterLabels": True,
    "clusterLabelSize": 14,
    "regions": {
        "dilation": 20,
        "blur": 40,
        "bitmap": 384,
        "edgeAlpha": 0.2,
        "enabled": True,
    },
    "physics": {
        "chargeStrength": -240,
        "linkStrength": 1,
        "linkDistance": 40,
        "gravity": 0.12,
        "clusterPull": 0.15,
        "velocityDecay": 0.4,
        "depthMode": "vertical",
        "depthStrength": 0.8,
        "depthSpacing": 60,
        "depthBaseRadius": 220,
    },
}


class CardPayload(dict):
    """Dict subclass returning None for missing keys while preserving exact dict semantics."""

    def __missing__(self, key: str) -> Any:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def now_ms() -> int:
    return int(time.time() * 1000)


def short_hash(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def resolve_data_dir(override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env_dir = os.environ.get("LH_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return DEFAULT_DATA_DIR.resolve()


def resolve_internship_progress(data_dir: Path, override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env_path = os.environ.get("LH_INTERNSHIP_PROGRESS")
    if env_path:
        return Path(env_path).expanduser().resolve()
    local_copy = data_dir / "progress.json"
    if local_copy.exists():
        return local_copy.resolve()
    if data_dir.resolve() != DEFAULT_DATA_DIR.resolve():
        # In a temp data dir, do not mutate the real repo progress.json unless explicitly pointed to.
        default_real = DEFAULT_INTERNSHIP_DIR / "data" / "progress.json"
        if default_real.exists():
            shutil.copyfile(default_real, local_copy)
            return local_copy.resolve()
        return local_copy.resolve()
    return (DEFAULT_INTERNSHIP_DIR / "data" / "progress.json").resolve()


def resolve_briefs_dir(data_dir: Path, override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env_dir = os.environ.get("LH_BRIEFS_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    if data_dir.resolve() == DEFAULT_DATA_DIR.resolve():
        return (REPO_ROOT / ".agent-ops" / "briefs").resolve()
    return (data_dir / "briefs").resolve()


def ensure_data_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("chats", "graph", "jobs", "demos", "problems"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    settings_path = root / "settings.json"
    if not settings_path.exists():
        write_json_atomic(settings_path, DEFAULT_SETTINGS)
    else:
        try:
            current = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(current, dict) and "helper_worker" not in current:
                current["helper_worker"] = dict(DEFAULT_SETTINGS["helper_worker"])
                write_json_atomic(settings_path, current)
        except Exception:
            pass

    format_path = root / "graph" / "format.json"
    if not format_path.exists():
        write_json_atomic(format_path, {"version": 1})

    sources_path = root / "graph" / "sources.json"
    if not sources_path.exists():
        write_json_atomic(sources_path, {})

    positions_path = root / "graph" / "positions.json"
    if not positions_path.exists():
        write_json_atomic(positions_path, {"positions": {}})

    render_path = root / "graph" / "render-settings.json"
    if not render_path.exists():
        write_json_atomic(render_path, DEFAULT_RENDER_SETTINGS)

    for ledger in ("nodes.jsonl", "chains.jsonl", "coverage.jsonl", "sessions.jsonl"):
        lpath = root / "graph" / ledger
        if not lpath.exists():
            lpath.write_text("", encoding="utf-8")

    return root


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8")


def read_json_safe(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                out.append(item)
        except Exception:
            continue
    return out


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in records)
    path.write_text(content, encoding="utf-8")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def parse_card_payload(text: str) -> CardPayload:
    """Parse a `[learn] session=<id> step=<step> kind=<kind> confidence=<0-100>` card payload or JSON."""
    raw = (text or "").strip()
    result = CardPayload()
    if not raw:
        return result

    if raw.startswith("{"):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                for k, v in loaded.items():
                    result[k] = v
                if "confidence" in result and result["confidence"] is not None:
                    try:
                        result["confidence"] = int(float(result["confidence"]))
                    except (ValueError, TypeError):
                        pass
                if "hints" in result and result["hints"] is not None:
                    try:
                        result["hints"] = int(float(result["hints"]))
                    except (ValueError, TypeError):
                        pass
                return result
        except Exception:
            pass

    lines = raw.splitlines()
    start_idx = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[learn]"):
            header_rest = stripped[len("[learn]"):].strip()
            for match in re.finditer(r"([A-Za-z0-9_]+)=(\"[^\"]*\"|'[^']*'|\S+)", header_rest):
                k = match.group(1)
                v = match.group(2).strip("\"'")
                result[k] = v
            start_idx = idx + 1
            break

    known_keys = {
        "session", "step", "kind", "confidence", "hints", "choice",
        "think1", "think2", "think3", "thinking", "readingThinking",
        "q1", "q2", "q3", "score", "feedback", "note", "text", "problem", "result",
    }
    current_key: str | None = None
    current_lines: list[str] = []

    def flush_field() -> None:
        nonlocal current_key, current_lines
        if current_key is not None:
            val = "\n".join(current_lines).strip()
            result[current_key] = val
        current_key = None
        current_lines = []

    key_pattern = re.compile(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$")
    for line in lines[start_idx:]:
        m = key_pattern.match(line)
        if m and m.group(1) in known_keys:
            flush_field()
            current_key = m.group(1)
            current_lines = [m.group(2)]
        elif current_key is not None:
            current_lines.append(line)
        else:
            # Handle key=value lines if any
            if "=" in line and ":" not in line:
                for match in re.finditer(r"([A-Za-z0-9_]+)=(\"[^\"]*\"|'[^']*'|\S+)", line.strip()):
                    k = match.group(1)
                    v = match.group(2).strip("\"'")
                    if k in known_keys:
                        result[k] = v

    flush_field()

    for int_key in ("confidence", "hints"):
        if int_key in result and result[int_key] is not None and result[int_key] != "":
            try:
                result[int_key] = int(float(str(result[int_key]).strip()))
            except (ValueError, TypeError):
                pass

    if "score" in result and result["score"] is not None and result["score"] != "":
        try:
            num = float(str(result["score"]).strip())
            result["score"] = int(num) if num.is_integer() else num
        except (ValueError, TypeError):
            pass

    return result


def resolve_session_path(root: Path, session_id: str) -> Path:
    chats_dir = root / "chats"
    direct = chats_dir / f"{session_id}.json"
    if direct.exists():
        return direct
    if not session_id.startswith("chat_"):
        prefixed = chats_dir / f"chat_{session_id}.json"
        if prefixed.exists():
            return prefixed
    # Prefix match fallback
    if chats_dir.exists():
        for p in sorted(chats_dir.glob("*.json")):
            if p.stem == session_id or p.stem.endswith(f"_{session_id}"):
                return p
    return direct


def load_session(root: Path, session_id: str) -> dict[str, Any]:
    path = resolve_session_path(root, session_id)
    if not path.exists():
        raise ValueError(f"Session not found: {session_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid session record: {session_id}")
    return data


def save_session(root: Path, record: dict[str, Any]) -> Path:
    session_id = record["id"]
    path = root / "chats" / f"{session_id}.json"
    write_json_atomic(path, record)
    return path


def list_all_sessions(root: Path) -> list[dict[str, Any]]:
    chats_dir = root / "chats"
    if not chats_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(chats_dir.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and rec.get("id") == path.stem:
                out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: (r.get("updatedAt") or "", r.get("createdAt") or "", r.get("id") or ""), reverse=True)
    return out


def get_current_step_info(record: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None, list[dict[str, Any]]]:
    tree = record.get("tree")
    if not isinstance(tree, dict):
        return None, None, []
    steps = tree.get("steps") or tree.get("nodes") or []
    if not isinstance(steps, list) or not steps:
        return None, None, []
    cur_id = tree.get("currentStep")
    for idx, step in enumerate(steps, 1):
        if isinstance(step, dict) and (step.get("status") == "current" or (cur_id and step.get("id") == cur_id)):
            return idx, step, steps
    for idx, step in enumerate(steps, 1):
        if isinstance(step, dict) and step.get("status") == "todo":
            return idx, step, steps
    return len(steps), steps[-1] if isinstance(steps[-1], dict) else None, steps


def build_plan_mermaid(record: dict[str, Any]) -> str:
    tree = record.get("tree") if isinstance(record.get("tree"), dict) else {}
    steps = tree.get("steps") or tree.get("nodes") or []
    edges = tree.get("edges") or []
    lines = ["flowchart TD"]
    if not steps:
        lines.append('  empty["No plan steps defined"]')
        return "\n".join(lines)
    for idx, step in enumerate(steps, 1):
        sid = re.sub(r"[^A-Za-z0-9_]", "_", str(step.get("id") or f"s{idx}"))
        title = str(step.get("title") or step.get("id") or f"Step {idx}").replace('"', "'")
        status = step.get("status", "todo")
        marker = " <-- YOU ARE HERE" if status == "current" else ""
        badge = "✅ " if status == "done" else ("👉 " if status == "current" else "⬜ ")
        lines.append(f'  {sid}["{idx}. {badge}{title}{marker}"]:::{status}')
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if isinstance(edge, dict) and edge.get("from") and edge.get("to"):
            u = re.sub(r"[^A-Za-z0-9_]", "_", str(edge["from"]))
            v = re.sub(r"[^A-Za-z0-9_]", "_", str(edge["to"]))
            if (u, v) not in seen_edges:
                lines.append(f"  {u} --> {v}")
                seen_edges.add((u, v))
    for step in steps:
        v = re.sub(r"[^A-Za-z0-9_]", "_", str(step.get("id")))
        for dep in step.get("deps") or []:
            u = re.sub(r"[^A-Za-z0-9_]", "_", str(dep))
            if (u, v) not in seen_edges:
                lines.append(f"  {u} --> {v}")
                seen_edges.add((u, v))
    lines.append("  classDef done fill:#166534,stroke:#22c55e,color:#fff;")
    lines.append("  classDef current fill:#1d4ed8,stroke:#60a5fa,stroke-width:3px,color:#fff;")
    lines.append("  classDef todo fill:#1e293b,stroke:#64748b,color:#e2e8f0;")
    return "\n".join(lines)


def load_merged_graph(root: Path) -> dict[str, Any]:
    """Load and merge nodes and chains using LearnHarness first-identity/union rules, plus depth & coverage."""
    raw_nodes = read_jsonl(root / "graph" / "nodes.jsonl")
    raw_chains = read_jsonl(root / "graph" / "chains.jsonl")
    coverage_rows = read_jsonl(root / "graph" / "coverage.jsonl")
    practice_rows = read_jsonl(root / "practice_attempts.jsonl")
    positions_doc = read_json_safe(root / "graph" / "positions.json", {"positions": {}})
    positions = positions_doc.get("positions", {}) if isinstance(positions_doc, dict) else {}
    render_settings = read_json_safe(root / "graph" / "render-settings.json", DEFAULT_RENDER_SETTINGS)

    sessions_by_id = {s["id"]: s for s in list_all_sessions(root)}

    nodes_map: dict[str, dict[str, Any]] = {}
    for line in raw_nodes:
        nid = line.get("id")
        if not isinstance(nid, str):
            continue
        existing = nodes_map.get(nid)
        if existing is None:
            nodes_map[nid] = {
                **line,
                "aliases": list(line.get("aliases") or []),
                "provenance": list(line.get("provenance") or []),
            }
        else:
            for phrasing in [line.get("statement", ""), *(line.get("aliases") or [])]:
                if phrasing and phrasing not in existing["aliases"] and phrasing != existing["statement"]:
                    existing["aliases"].append(phrasing)
            seen = {(p.get("chatId"), p.get("messageId")) for p in existing["provenance"] if isinstance(p, dict)}
            for p in line.get("provenance") or []:
                if isinstance(p, dict):
                    key = (p.get("chatId"), p.get("messageId"))
                    if key not in seen:
                        existing["provenance"].append(p)
                        seen.add(key)

    chains_map: dict[str, dict[str, Any]] = {}
    for line in raw_chains:
        premises = line.get("premises")
        conclusion = line.get("conclusion")
        if not isinstance(premises, list) or not premises or not isinstance(conclusion, str):
            continue
        key = "+".join(sorted(str(p) for p in premises)) + ">" + conclusion
        existing = chains_map.get(key)
        if existing is None:
            chains_map[key] = {
                **line,
                "premises": list(premises),
                "provenance": list(line.get("provenance") or []),
            }
        else:
            seen = {(p.get("chatId"), p.get("messageId")) for p in existing["provenance"] if isinstance(p, dict)}
            for p in line.get("provenance") or []:
                if isinstance(p, dict):
                    pkey = (p.get("chatId"), p.get("messageId"))
                    if pkey not in seen:
                        existing["provenance"].append(p)
                        seen.add(pkey)

    # Compute node practice stats & coverage
    node_stats: dict[str, dict[str, Any]] = {
        nid: {"attempts": 0, "correct": 0, "partial": 0, "wrong": 0, "lastResult": None, "lastHints": 0}
        for nid in nodes_map
    }
    all_attempts = [*coverage_rows, *practice_rows]
    seen_attempt_ids: set[str] = set()
    for row in all_attempts:
        aid = str(row.get("attemptId") or row.get("fp") or "")
        if aid and aid in seen_attempt_ids:
            continue
        res = row.get("result")
        if res not in ("correct", "partial", "wrong"):
            continue
        if aid:
            seen_attempt_ids.add(aid)
        target_nodes = list(row.get("nodes") or [])
        if row.get("nodeId") and row["nodeId"] not in target_nodes:
            target_nodes.append(row["nodeId"])
        hints = int(row.get("hints") or 0)
        for nid in target_nodes:
            if nid in node_stats:
                st = node_stats[nid]
                st["attempts"] += 1
                st[res] += 1
                st["lastResult"] = res
                st["lastHints"] = hints

    # Compute longest-chain depth from roots
    depths: dict[str, int] = {nid: 0 for nid in nodes_map}
    chain_list = list(chains_map.values())
    for _ in range(len(nodes_map) + 1):
        changed = False
        for ch in chain_list:
            conc = ch["conclusion"]
            if conc not in depths:
                continue
            prem_depths = [depths.get(p, 0) for p in ch["premises"]]
            cand = (max(prem_depths) + 1) if prem_depths else 1
            if cand > depths[conc]:
                depths[conc] = cand
                changed = True
        if not changed:
            break

    enriched_nodes: list[dict[str, Any]] = []
    for nid in sorted(nodes_map.keys()):
        node = nodes_map[nid]
        prov = node.get("provenance") or []
        sids = []
        tracks = []
        for p in prov:
            cid = p.get("chatId")
            if cid and cid not in sids:
                sids.append(cid)
                srec = sessions_by_id.get(cid)
                if srec:
                    tr = srec.get("track", "general")
                    if tr not in tracks:
                        tracks.append(tr)
        stats = node_stats.get(nid, {"attempts": 0, "correct": 0, "partial": 0, "wrong": 0, "lastResult": None, "lastHints": 0})
        if stats["attempts"] > 0:
            if stats["lastResult"] == "correct" and stats["lastHints"] <= 1:
                cov = "mastered"
            else:
                cov = "shaky"
        else:
            cov = node.get("initialCoverage") or "unseen"

        pos = positions.get(nid)
        enriched_nodes.append({
            **node,
            "depth": depths.get(nid, 0),
            "coverage": cov,
            "practiceStats": stats,
            "sessionIds": sids,
            "tracks": tracks or ["general"],
            "position": pos,
        })

    node_sessions_lookup = {n["id"]: set(n.get("sessionIds") or []) for n in enriched_nodes}
    enriched_chains: list[dict[str, Any]] = []
    for ch in chain_list:
        c_sids: set[str] = {p.get("chatId") for p in (ch.get("provenance") or []) if isinstance(p, dict) and p.get("chatId")}
        for pid in ch.get("premises") or []:
            c_sids |= node_sessions_lookup.get(pid, set())
        c_sids |= node_sessions_lookup.get(ch.get("conclusion"), set())
        enriched_chains.append({
            **ch,
            "crossSession": len(c_sids) > 1,
            "sessionIds": sorted(c_sids),
        })

    return {
        "nodes": enriched_nodes,
        "chains": enriched_chains,
        "positions": positions,
        "renderSettings": render_settings,
    }


def cmd_session(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    sub = args.session_cmd
    if sub == "new":
        now = now_iso()
        slug = re.sub(r"[^a-z0-9]+", "", (args.topic or "session").lower())[:8] or "sess"
        raw_id = args.id or f"chat_{slug}_{short_hash(f'{args.topic}:{args.goal}:{now}', 8)}"
        session_id = raw_id if raw_id.startswith("chat_") else f"chat_{raw_id}"
        links: list[str] = []
        for target in args.link or []:
            try:
                other = load_session(root, target)
                oid = other["id"]
                if oid not in links:
                    links.append(oid)
                olinks = list(other.get("links") or [])
                if session_id not in olinks:
                    olinks.append(session_id)
                    other["links"] = olinks
                    other["updatedAt"] = now
                    save_session(root, other)
            except ValueError:
                if target not in links:
                    links.append(target)

        record = {
            "id": session_id,
            "title": args.title or f"{args.topic}: {args.goal}",
            "topic": args.topic,
            "goal": args.goal,
            "track": args.track or "general",
            "status": "open",
            "kind": "teach",
            "version": 1,
            "createdAt": now,
            "updatedAt": now,
            "links": links,
            "cards": [],
            "openQuestions": [],
            "messages": [],
            "settings": {
                "model": {"provider": "claude-code", "id": "claude-desktop"},
                "thinkingLevel": "low",
            },
            "turns": [],
            "tree": {
                "nodes": [],
                "steps": [],
                "edges": [],
                "currentStep": None,
                "version": 1,
            },
        }
        save_session(root, record)
        if args.json:
            return {"id": session_id, "topic": args.topic, "goal": args.goal, "track": record["track"], "links": links}
        return session_id

    if sub == "list":
        sessions = list_all_sessions(root)
        if args.track:
            sessions = [s for s in sessions if s.get("track") == args.track]
        if args.limit:
            sessions = sessions[: args.limit]
        summaries = []
        for s in sessions:
            idx, cur_step, steps = get_current_step_info(s)
            done_cnt = sum(1 for st in steps if isinstance(st, dict) and st.get("status") == "done")
            summaries.append({
                "id": s["id"],
                "title": s.get("title", ""),
                "topic": s.get("topic") or s.get("title", ""),
                "goal": s.get("goal", ""),
                "track": s.get("track", "general"),
                "status": s.get("status", "open"),
                "kind": s.get("kind", "teach"),
                "createdAt": s.get("createdAt", ""),
                "updatedAt": s.get("updatedAt", ""),
                "links": s.get("links") or [],
                "currentStep": cur_step.get("id") if cur_step else None,
                "currentStepIndex": idx,
                "stepCount": len(steps),
                "doneCount": done_cnt,
                "cardCount": len(s.get("cards") or []),
            })
        if args.json:
            return {"sessions": summaries}
        if not summaries:
            return "No sessions found."
        lines = [f"Sessions ({len(summaries)}):"]
        for s in summaries:
            step_str = f"step {s['currentStepIndex']}/{s['stepCount']} ({s['currentStep']})" if s["currentStep"] else "no-plan"
            link_str = f" links={','.join(s['links'])}" if s["links"] else ""
            lines.append(f"- {s['id']} [{s['track']}/{s['status']}] {s['topic']} — {step_str}{link_str}")
        return "\n".join(lines)

    if sub == "show":
        rec = load_session(root, args.id)
        idx, cur_step, steps = get_current_step_info(rec)
        linked_details = []
        for lid in rec.get("links") or []:
            try:
                lrec = load_session(root, lid)
                linked_details.append({
                    "id": lrec["id"],
                    "title": lrec.get("title", ""),
                    "topic": lrec.get("topic") or lrec.get("title", ""),
                    "track": lrec.get("track", "general"),
                    "status": lrec.get("status", "open"),
                })
            except ValueError:
                linked_details.append({"id": lid, "title": lid, "topic": lid, "track": "unknown", "status": "unknown"})
        payload = {
            **rec,
            "linkedSessions": linked_details,
            "currentStepIndex": idx,
            "currentStep": cur_step.get("id") if cur_step else None,
        }
        if args.json:
            return {"session": payload}
        lines = [
            f"Session: {rec['id']} ({rec.get('status', 'open')})",
            f"Track: {rec.get('track', 'general')} | Topic: {rec.get('topic') or rec.get('title')}",
            f"Goal: {rec.get('goal', '')}",
            f"Current step: {idx or '-'} ({cur_step.get('id') + ': ' + cur_step.get('title', '') if cur_step else 'none'})",
            f"Links: {', '.join(rec.get('links') or []) or 'none'}",
            f"Cards: {len(rec.get('cards') or [])} | Messages: {len(rec.get('messages') or [])}",
        ]
        return "\n".join(lines)

    if sub == "link":
        rec_a = load_session(root, args.id)
        rec_b = load_session(root, args.other)
        id_a, id_b = rec_a["id"], rec_b["id"]
        now = now_iso()
        links_a = list(rec_a.get("links") or [])
        links_b = list(rec_b.get("links") or [])
        if id_b not in links_a:
            links_a.append(id_b)
        if id_a not in links_b:
            links_b.append(id_a)
        rec_a["links"] = links_a
        rec_b["links"] = links_b
        rec_a["updatedAt"] = now
        rec_b["updatedAt"] = now
        save_session(root, rec_a)
        save_session(root, rec_b)
        if args.json:
            return {"ok": True, "a": id_a, "b": id_b, "linksA": links_a, "linksB": links_b}
        return f"Linked {id_a} <-> {id_b}"

    if sub == "close":
        rec = load_session(root, args.id)
        rec["status"] = "closed"
        rec["updatedAt"] = now_iso()
        save_session(root, rec)
        if args.json:
            return {"id": rec["id"], "status": "closed"}
        return f"Closed {rec['id']}"

    raise ValueError(f"Unknown session command: {sub}")


def cmd_continue(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    if args.id:
        rec = load_session(root, args.id)
    else:
        sessions = list_all_sessions(root)
        if not sessions:
            raise ValueError("No sessions found to continue.")
        open_sessions = [s for s in sessions if s.get("status", "open") == "open"]
        rec = open_sessions[0] if open_sessions else sessions[0]

    idx, cur_step, steps = get_current_step_info(rec)
    cards = list(rec.get("cards") or [])[-3:]
    graph_data = load_merged_graph(root)
    weak_nodes = [
        {"id": n["id"], "name": n["name"]}
        for n in graph_data["nodes"]
        if n.get("coverage") in ("shaky", "unseen") and (rec["id"] in (n.get("sessionIds") or []) or not n.get("sessionIds"))
    ][:5]
    open_qs = list(rec.get("openQuestions") or [])[-3:]

    if args.json:
        return {
            "id": rec["id"],
            "topic": rec.get("topic") or rec.get("title"),
            "goal": rec.get("goal", ""),
            "track": rec.get("track", "general"),
            "status": rec.get("status", "open"),
            "currentStepIndex": idx,
            "currentStep": cur_step,
            "plan": [
                {"index": i, "id": s.get("id"), "title": s.get("title"), "status": s.get("status", "todo")}
                for i, s in enumerate(steps, 1)
            ],
            "lastCards": cards,
            "openQuestions": open_qs,
            "weakNodes": weak_nodes,
            "links": rec.get("links") or [],
        }

    lines = [
        f"RESUME: {rec['id']} [{rec.get('track', 'general')}]",
        f"Topic: {rec.get('topic') or rec.get('title')} | Goal: {rec.get('goal', '')}",
    ]
    if cur_step:
        lines.append(f"Current step {idx}: {cur_step.get('id')} - {cur_step.get('title')} <-- YOU ARE HERE")
    else:
        lines.append("Current step: none")

    if steps:
        lines.append("Plan:")
        for i, s in enumerate(steps[:8], 1):
            st = s.get("status", "todo")
            here = " <-- you are here" if (idx == i and st == "current") else ""
            lines.append(f"  {i}. [{st}] {s.get('id')}: {s.get('title')}{here}")

    if cards:
        lines.append("Last cards (up to 3):")
        for c in cards:
            ans = c.get("q1") or c.get("think1") or c.get("choice") or c.get("text") or ""
            ans_short = (str(ans).replace("\n", " "))[:60]
            lines.append(
                f"  - [{c.get('kind')}] step={c.get('step')} conf={c.get('confidence')} score={c.get('score')} {ans_short}"
            )

    if open_qs:
        lines.append("Open questions: " + "; ".join(str(q)[:60] for q in open_qs))
    if weak_nodes:
        lines.append("Weak/shaky nodes: " + ", ".join(f"{n['id']}({n['name'][:24]})" for n in weak_nodes))
    if rec.get("links"):
        lines.append("Linked sessions: " + ", ".join(rec["links"]))

    return "\n".join(lines)


def cmd_plan(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    sub = args.plan_cmd
    rec = load_session(root, args.id)

    if sub == "set":
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw_steps = raw
            raw_edges = []
        elif isinstance(raw, dict):
            raw_steps = raw.get("steps") or raw.get("nodes") or []
            raw_edges = raw.get("edges") or []
        else:
            raise ValueError("Plan file must contain a JSON list of steps or object with steps/nodes.")

        edge_deps: dict[str, list[str]] = {}
        for edge in raw_edges:
            if isinstance(edge, dict) and edge.get("from") and edge.get("to"):
                edge_deps.setdefault(str(edge["to"]), []).append(str(edge["from"]))

        steps: list[dict[str, Any]] = []
        has_current = False
        for i, item in enumerate(raw_steps, 1):
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or f"s{i}")
            title = str(item.get("title") or item.get("name") or sid)
            deps = [str(d) for d in (item.get("deps") or item.get("prerequisites") or edge_deps.get(sid, []))]
            status = str(item.get("status") or "todo")
            if status not in ("todo", "current", "done"):
                status = "todo"
            if status == "current":
                has_current = True
            steps.append({"id": sid, "title": title, "deps": deps, "status": status})

        if not has_current:
            for s in steps:
                if s["status"] == "todo":
                    s["status"] = "current"
                    break

        edges: list[dict[str, str]] = []
        for s in steps:
            for dep in s["deps"]:
                edges.append({"from": dep, "to": s["id"]})

        cur_id = next((s["id"] for s in steps if s["status"] == "current"), None)
        old_tree = rec.get("tree") if isinstance(rec.get("tree"), dict) else {}
        version = int(old_tree.get("version") or 1)
        rec["tree"] = {
            "nodes": steps,
            "steps": steps,
            "edges": edges,
            "currentStep": cur_id,
            "version": version,
        }
        rec["updatedAt"] = now_iso()
        rec.setdefault("messages", []).append({
            "id": f"msg_plan_{now_ms()}",
            "role": "custom",
            "customType": "plan-block",
            "timestamp": now_ms(),
            "content": [{"type": "text", "text": f"Plan updated ({len(steps)} steps)"}],
            "details": {"block": {"steps": steps, "currentStep": cur_id}},
        })
        save_session(root, rec)
        if args.json:
            return {"sessionId": rec["id"], "currentStep": cur_id, "steps": steps, "edges": edges}
        return f"Plan set for {rec['id']}: {len(steps)} steps (current: {cur_id})"

    if sub == "advance":
        tree = rec.get("tree") if isinstance(rec.get("tree"), dict) else {}
        steps = list(tree.get("steps") or tree.get("nodes") or [])
        if not steps:
            raise ValueError(f"Session {rec['id']} has no plan steps.")

        cur_idx = next((i for i, s in enumerate(steps) if s.get("status") == "current"), None)
        if cur_idx is not None:
            steps[cur_idx]["status"] = "done"
        else:
            first_todo = next((i for i, s in enumerate(steps) if s.get("status") == "todo"), None)
            if first_todo is not None:
                steps[first_todo]["status"] = "done"

        done_ids = {s["id"] for s in steps if s.get("status") == "done"}
        next_step = None
        for s in steps:
            if s.get("status") == "todo" and all(d in done_ids for d in (s.get("deps") or [])):
                s["status"] = "current"
                next_step = s
                break
        if next_step is None:
            for s in steps:
                if s.get("status") == "todo":
                    s["status"] = "current"
                    next_step = s
                    break

        cur_id = next_step["id"] if next_step else None
        tree["nodes"] = steps
        tree["steps"] = steps
        tree["currentStep"] = cur_id
        rec["tree"] = tree
        rec["updatedAt"] = now_iso()
        save_session(root, rec)
        idx, cur_obj, _ = get_current_step_info(rec)
        if args.json:
            return {"sessionId": rec["id"], "currentStep": cur_id, "currentStepIndex": idx, "steps": steps}
        if cur_obj:
            return f"Advanced {rec['id']} to step {idx}: {cur_obj['id']} ({cur_obj['title']}) <-- you are here"
        return f"Plan completed for {rec['id']} (all {len(steps)} steps done)."

    if sub == "show":
        idx, cur_step, steps = get_current_step_info(rec)
        if args.json:
            return {
                "sessionId": rec["id"],
                "currentStepIndex": idx,
                "currentStep": cur_step.get("id") if cur_step else None,
                "steps": steps,
            }
        if not steps:
            return f"Plan for {rec['id']}: (empty)"
        lines = [f"Plan for {rec['id']} ({rec.get('topic') or rec.get('title')}):"]
        for i, s in enumerate(steps, 1):
            st = s.get("status", "todo")
            deps = f" (deps: {','.join(s.get('deps') or [])})" if s.get("deps") else ""
            marker = " <-- you are here" if st == "current" else ""
            lines.append(f"  {i}. [{st}] {s.get('id')}: {s.get('title')}{deps}{marker}")
        return "\n".join(lines)

    if sub == "mermaid":
        mermaid = build_plan_mermaid(rec)
        if args.json:
            return {"sessionId": rec["id"], "mermaid": mermaid}
        return mermaid

    raise ValueError(f"Unknown plan command: {sub}")


def cmd_log(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    rec = load_session(root, args.id)
    raw_text = Path(args.payload_file).read_text(encoding="utf-8")
    parsed = parse_card_payload(raw_text)

    kind = args.kind or parsed.get("kind") or "note"
    _, cur_step, _ = get_current_step_info(rec)
    step = args.step or parsed.get("step") or (cur_step.get("id") if cur_step else "s1")
    now = now_iso()
    ts_ms = now_ms()

    confidence = parsed.get("confidence")
    score = args.score if getattr(args, "score", None) is not None else parsed.get("score")
    feedback = args.feedback if getattr(args, "feedback", None) is not None else parsed.get("feedback")

    card_event: dict[str, Any] = {
        "id": f"card_{len(rec.get('cards') or []) + 1}_{short_hash(f'{kind}:{step}:{now}', 6)}",
        "kind": kind,
        "step": step,
        "confidence": confidence,
        "think1": parsed.get("think1") or parsed.get("thinking"),
        "think2": parsed.get("think2"),
        "think3": parsed.get("think3"),
        "q1": parsed.get("q1") or parsed.get("text"),
        "q2": parsed.get("q2"),
        "choice": parsed.get("choice"),
        "hints": parsed.get("hints"),
        "score": score,
        "feedback": feedback,
        "timestamp": now,
    }
    card_event = {k: v for k, v in card_event.items() if v is not None}

    rec.setdefault("cards", []).append(card_event)

    if parsed.get("openQuestion"):
        rec.setdefault("openQuestions", []).append(str(parsed["openQuestion"]))
    elif isinstance( parsed.get("openQuestions"), list):
        rec.setdefault("openQuestions", []).extend(str(q) for q in parsed["openQuestions"])

    # Also append native v1 messages for sessions.py compatibility
    call_id = f"call_{short_hash(card_event['id'], 12)}"
    tool_name = "probe" if kind == "probe" else ("problem" if kind == "practice" else "quiz")
    thinking_parts = [str(card_event[k]) for k in ("think1", "think2", "think3") if card_event.get(k)]
    answer_parts = [str(card_event[k]) for k in ("q1", "q2", "choice") if card_event.get(k)]
    combined_thinking = " | ".join(thinking_parts) if thinking_parts else None
    combined_answer = " | ".join(answer_parts) if answer_parts else raw_text.strip()[:240]

    messages = rec.setdefault("messages", [])
    if kind in ("predict", "check", "probe"):
        messages.append({
            "id": f"msg_{len(messages) + 1}_call",
            "role": "assistant",
            "timestamp": ts_ms,
            "content": [
                {
                    "type": "toolCall",
                    "id": call_id,
                    "name": tool_name,
                    "arguments": {
                        "step": step,
                        "questionType": kind,
                        "question": f"{kind.capitalize()} check on step {step}",
                    },
                }
            ],
        })
        details: dict[str, Any] = {
            "kind": "quiz",
            "tool": tool_name,
            "questionType": kind,
            "step": step,
            "text": combined_answer,
            "answeredAt": now,
        }
        if combined_thinking:
            details["thinking"] = combined_thinking
        if confidence is not None:
            details["confidence"] = confidence
        if card_event.get("choice"):
            details["answers"] = [{"label": str(card_event["choice"]), "value": str(card_event["choice"]), "index": 1}]
        if score is not None:
            details["score"] = score
        if feedback:
            details["explanation"] = feedback
        messages.append({
            "id": f"msg_{len(messages) + 1}_res",
            "role": "toolResult",
            "toolName": tool_name,
            "toolCallId": call_id,
            "isError": False,
            "timestamp": ts_ms + 1,
            "content": [{"type": "text", "text": json.dumps({"step": step, "text": combined_answer}, ensure_ascii=False)}],
            "details": details,
        })
    elif kind == "reading":
        messages.append({
            "id": f"msg_{len(messages) + 1}_read",
            "role": "user",
            "timestamp": ts_ms,
            "content": f"[reading step={step} confidence={confidence}] {combined_thinking or combined_answer}",
        })
    else:
        messages.append({
            "id": f"msg_{len(messages) + 1}_user",
            "role": "user",
            "timestamp": ts_ms,
            "content": f"[{kind} step={step}] {combined_answer}",
        })

    if feedback:
        messages.append({
            "id": f"msg_{len(messages) + 1}_fb",
            "role": "assistant",
            "timestamp": ts_ms + 2,
            "content": [{"type": "text", "text": str(feedback)}],
        })

    rec["updatedAt"] = now
    save_session(root, rec)
    if args.json:
        return {"sessionId": rec["id"], "card": card_event}
    return f"Logged {kind} card on {rec['id']} (step={step}, confidence={confidence})"


def cmd_extract(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    rec = load_session(root, args.id)
    session_id = rec["id"]
    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        in_nodes = raw
        in_chains = []
    elif isinstance(raw, dict):
        in_nodes = raw.get("nodes") or raw.get("propositions") or []
        in_chains = raw.get("chains") or []
    else:
        raise ValueError("Extract file must contain a JSON object with nodes/chains or list of nodes.")

    now = now_iso()
    existing_nodes = read_jsonl(root / "graph" / "nodes.jsonl")
    by_norm_stmt: dict[str, str] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for n in existing_nodes:
        nid = n.get("id")
        stmt = n.get("statement", "")
        if isinstance(nid, str):
            by_id[nid] = n
            if stmt:
                by_norm_stmt[normalize_text(stmt)] = nid
            for al in n.get("aliases") or []:
                if isinstance(al, str) and al:
                    by_norm_stmt.setdefault(normalize_text(al), nid)

    local_id_map: dict[str, str] = {}
    added_nodes: list[dict[str, Any]] = []

    for idx, item in enumerate(in_nodes, 1):
        if not isinstance(item, dict):
            continue
        stmt = str(item.get("statement") or item.get("text") or "").strip()
        name = str(item.get("name") or item.get("title") or stmt[:48] or f"Node {idx}").strip()
        if not stmt:
            stmt = name
        raw_nid = item.get("id")
        norm = normalize_text(stmt)
        canonical_id = by_norm_stmt.get(norm)
        if canonical_id is None and isinstance(raw_nid, str) and raw_nid in by_id:
            canonical_id = raw_nid
        if canonical_id is None:
            if isinstance(raw_nid, str) and raw_nid.startswith("n_"):
                canonical_id = raw_nid
            else:
                canonical_id = f"n_{short_hash(norm, 12)}"
            by_norm_stmt[norm] = canonical_id

        if isinstance(raw_nid, str):
            local_id_map[raw_nid] = canonical_id
        local_id_map[name] = canonical_id

        step_val = str(item.get("step") or "")
        msg_id = str(item.get("messageId") or step_val or "")
        prov_point: dict[str, Any] = {
            "chatId": session_id,
            "messageId": msg_id,
            "step": step_val,
            "source": str(item.get("source") or "session"),
            "timestamp": str(item.get("timestamp") or now),
        }
        aliases = [str(a) for a in (item.get("aliases") or []) if isinstance(a, str) and str(a).strip()]
        node_record: dict[str, Any] = {
            "id": canonical_id,
            "name": name,
            "statement": stmt,
            "aliases": aliases,
            "createdAt": str(item.get("createdAt") or now),
            "provenance": [prov_point],
        }
        append_jsonl(root / "graph" / "nodes.jsonl", node_record)
        added_nodes.append(node_record)

    added_chains: list[dict[str, Any]] = []
    for idx, ch in enumerate(in_chains, 1):
        if not isinstance(ch, dict):
            continue
        raw_premises = ch.get("premises") or ch.get("from") or []
        if isinstance(raw_premises, str):
            raw_premises = [raw_premises]
        premises = [local_id_map.get(str(p), str(p)) for p in raw_premises if str(p).strip()]
        raw_conc = str(ch.get("conclusion") or ch.get("to") or "").strip()
        conclusion = local_id_map.get(raw_conc, raw_conc)
        if not premises or not conclusion:
            continue
        cid = str(ch.get("id") or f"arg_{short_hash('+'.join(sorted(premises)) + '>' + conclusion, 12)}")
        if not cid.startswith("arg_"):
            cid = f"arg_{cid}"
        step_val = str(ch.get("step") or "")
        msg_id = str(ch.get("messageId") or step_val or "")
        chain_record: dict[str, Any] = {
            "id": cid,
            "premises": premises,
            "conclusion": conclusion,
            "createdAt": str(ch.get("createdAt") or now),
            "provenance": [
                {
                    "chatId": session_id,
                    "messageId": msg_id,
                    "step": step_val,
                    "source": str(ch.get("source") or "session"),
                    "timestamp": str(ch.get("timestamp") or now),
                }
            ],
        }
        append_jsonl(root / "graph" / "chains.jsonl", chain_record)
        added_chains.append(chain_record)

    append_jsonl(root / "graph" / "sessions.jsonl", {"chatId": session_id, "foldedAt": now})
    fp = short_hash(json.dumps({"s": session_id, "n": [n["id"] for n in added_nodes], "t": now}), 64)
    append_jsonl(
        root / "graph" / "coverage.jsonl",
        {"chatId": session_id, "fp": fp, "tokens": max(1, len(added_nodes) * 32), "foldedAt": now},
    )

    rec["updatedAt"] = now
    save_session(root, rec)

    if args.json:
        return {"sessionId": session_id, "nodes": added_nodes, "chains": added_chains}
    return f"Extracted {len(added_nodes)} nodes and {len(added_chains)} chains from {session_id}"


def run_search(root: Path, query: str, scopes: set[str], limit: int = 20) -> list[dict[str, Any]]:
    q_norm = (query or "").strip().lower()
    if not q_norm:
        return []
    tokens = [t for t in re.split(r"\s+", q_norm) if t]

    def score_text(primary: str, secondary: str = "") -> int:
        p_low = (primary or "").lower()
        s_low = (secondary or "").lower()
        score = 0
        if q_norm in p_low:
            score += 100
        if q_norm in s_low:
            score += 50
        for tok in tokens:
            if tok in p_low:
                score += 25
            elif tok in s_low:
                score += 10
        return score

    def make_snippet(text: str) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        idx = clean.lower().find(q_norm)
        if idx == -1 and tokens:
            for tok in tokens:
                idx = clean.lower().find(tok)
                if idx != -1:
                    break
        if idx == -1 or len(clean) <= 110:
            return clean[:110]
        start = max(0, idx - 30)
        end = min(len(clean), start + 110)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(clean) else ""
        return f"{prefix}{clean[start:end]}{suffix}"

    hits: list[dict[str, Any]] = []
    sessions = list_all_sessions(root)

    if "chats" in scopes:
        for s in sessions:
            sid = s["id"]
            title_blob = f"{s.get('title', '')} {s.get('topic', '')} {s.get('goal', '')}"
            cards_blob = " ".join(
                f"{c.get('think1', '')} {c.get('q1', '')} {c.get('feedback', '')}" for c in (s.get("cards") or [])
            )
            msg_blob = " ".join(json.dumps(m, ensure_ascii=False) for m in (s.get("messages") or []))
            body_blob = f"{cards_blob} {msg_blob}"
            sc = score_text(title_blob, body_blob)
            if sc > 0:
                _, cur_step, _ = get_current_step_info(s)
                step_id = cur_step.get("id") if cur_step else "-"
                for c in reversed(s.get("cards") or []):
                    if q_norm in json.dumps(c, ensure_ascii=False).lower():
                        step_id = c.get("step") or step_id
                        break
                hits.append({
                    "kind": "chat",
                    "sessionId": sid,
                    "step": step_id,
                    "id": sid,
                    "title": s.get("topic") or s.get("title") or sid,
                    "snippet": make_snippet(f"{title_blob} — {cards_blob or msg_blob}"),
                    "score": sc,
                })

    if "plans" in scopes:
        for s in sessions:
            sid = s["id"]
            _, _, steps = get_current_step_info(s)
            for st in steps:
                st_title = str(st.get("title") or "")
                sc = score_text(st_title, str(st.get("id") or ""))
                if sc > 0:
                    hits.append({
                        "kind": "plan",
                        "sessionId": sid,
                        "step": st.get("id") or "-",
                        "id": f"{sid}:{st.get('id')}",
                        "title": st_title,
                        "snippet": make_snippet(f"[{st.get('status', 'todo')}] {st.get('id')}: {st_title}"),
                        "score": sc,
                    })

    if "nodes" in scopes:
        graph = load_merged_graph(root)
        for n in graph["nodes"]:
            name_str = n.get("name", "")
            stmt_str = f"{n.get('statement', '')} {' '.join(n.get('aliases') or [])}"
            sc = score_text(name_str, stmt_str)
            if sc > 0:
                prov = (n.get("provenance") or [{}])[0]
                sid = prov.get("chatId") or (n.get("sessionIds") or ["-"])[0]
                step_id = prov.get("step") or prov.get("messageId") or "-"
                hits.append({
                    "kind": "node",
                    "sessionId": sid,
                    "step": step_id,
                    "id": n["id"],
                    "title": name_str,
                    "snippet": make_snippet(f"{name_str}: {n.get('statement', '')}"),
                    "score": sc + 5,
                })

    if "problems" in scopes:
        for prob in load_problems_catalog(root):
            sc = score_text(prob.get("title", ""), prob.get("statement", ""))
            if sc > 0:
                hits.append({
                    "kind": "problem",
                    "sessionId": prob.get("sessionId") or "-",
                    "step": "-",
                    "id": prob["id"],
                    "title": prob.get("title") or prob["id"],
                    "snippet": make_snippet(prob.get("statement", "")),
                    "score": sc,
                })

    hits.sort(key=lambda h: (-h["score"], h["kind"], h["id"]))
    return hits[:limit]


def cmd_search(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    scopes = {s.strip().lower() for s in (args.scopes or "chats,nodes,plans,problems").split(",") if s.strip()}
    hits = run_search(root, args.query, scopes, limit=args.limit or 15)
    if args.json:
        return {"query": args.query, "hits": hits}
    if not hits:
        return f"No matches for '{args.query}'."
    lines = [f"Search '{args.query}' ({len(hits)} hits):"]
    for h in hits:
        lines.append(f"- [{h['kind']}] {h['id']} (session={h['sessionId']} step={h['step']}): {h['snippet']}")
    return "\n".join(lines)


def cmd_probe(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    rec = load_session(root, args.id)
    graph = load_merged_graph(root)
    topic_words = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", f"{rec.get('topic', '')} {rec.get('goal', '')}".lower()))

    candidates: list[dict[str, Any]] = []
    for n in graph["nodes"]:
        blob = f"{n.get('name', '')} {n.get('statement', '')}".lower()
        overlap = sum(1 for w in topic_words if len(w) > 2 and w in blob)
        priority = overlap * 10 + (15 if n.get("coverage") in ("shaky", "unseen") else 0)
        if rec["id"] in (n.get("sessionIds") or []):
            priority += 20
        candidates.append({
            "id": n["id"],
            "name": n["name"],
            "statement": n["statement"],
            "depth": n.get("depth", 0),
            "coverage": n.get("coverage", "unseen"),
            "priority": priority,
        })
    candidates.sort(key=lambda c: (-c["priority"], c["depth"], c["id"]))
    top_candidates = candidates[:8]

    prog_path = resolve_internship_progress(root, getattr(args, "internship_progress", None))
    prog = read_json_safe(prog_path, {})
    breakpoints = [
        bp for bp in (prog.get("breakpoints") or [])
        if isinstance(bp, dict) and bp.get("status") == "open"
    ][:6]

    if args.json:
        return {"sessionId": rec["id"], "candidates": top_candidates, "breakpoints": breakpoints}
    lines = [f"Probe seed for {rec['id']} ({rec.get('topic')}):"]
    if top_candidates:
        lines.append("Candidate nodes:")
        for c in top_candidates[:5]:
            lines.append(f"  - {c['id']} [{c['coverage']} d={c['depth']}] {c['name']}: {c['statement'][:80]}")
    if breakpoints:
        lines.append("Open internship breakpoints:")
        for bp in breakpoints[:4]:
            lines.append(f"  - [{bp.get('lesson')}] {bp.get('topic')}: {bp.get('gap', '')[:80]}")
    return "\n".join(lines)


def load_problems_catalog(root: Path) -> list[dict[str, Any]]:
    pdir = root / "problems"
    out: list[dict[str, Any]] = []
    if not pdir.exists():
        return out
    for path in sorted(pdir.iterdir()):
        if path.suffix.lower() == ".json":
            data = read_json_safe(path)
            items = data if isinstance(data, list) else (data.get("problems") if isinstance(data, dict) and isinstance(data.get("problems"), list) else ([data] if isinstance(data, dict) else []))
            for idx, item in enumerate(items, 1):
                if isinstance(item, dict):
                    pid = str(item.get("id") or f"{path.stem}_{idx}")
                    nodes = [str(n) for n in (item.get("nodes") or item.get("nodeIds") or [])]
                    cue = int(item.get("cue_unfamiliarity") if item.get("cue_unfamiliarity") is not None else item.get("cueUnfamiliarity", 1))
                    ideas = max(1, int(item.get("number_of_ideas_combined") or len(nodes) or 1))
                    out.append({
                        "id": pid,
                        "title": str(item.get("title") or pid),
                        "statement": str(item.get("statement") or item.get("question") or item.get("problem") or ""),
                        "nodes": nodes,
                        "number_of_ideas_combined": ideas,
                        "cue_unfamiliarity": cue,
                        "difficulty": ideas + cue,
                        "familiarity": item.get("familiarity") or ("familiar" if cue == 0 else ("transfer" if cue == 1 else "novel")),
                        "hints": list(item.get("hints") or []),
                        "solution": str(item.get("solution") or item.get("modelSolution") or ""),
                    })
        elif path.suffix.lower() == ".md":
            text = path.read_text(encoding="utf-8").strip()
            nodes = re.findall(r"\bn_[A-Za-z0-9_]+\b", text)
            ideas = max(1, len(nodes))
            cue = 1
            out.append({
                "id": path.stem,
                "title": path.stem.replace("_", " ").title(),
                "statement": text[:500],
                "nodes": nodes,
                "number_of_ideas_combined": ideas,
                "cue_unfamiliarity": cue,
                "difficulty": ideas + cue,
                "familiarity": "transfer",
                "hints": [],
                "solution": "",
            })
    return out


def cmd_practice(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    sub = args.practice_cmd
    graph = load_merged_graph(root)
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}

    if sub == "next":
        target_node_ids = list(args.nodes or [])
        session_rec = None
        if args.id:
            try:
                session_rec = load_session(root, args.id)
            except ValueError:
                if not target_node_ids:
                    target_node_ids = [args.id]

        if not target_node_ids:
            # Pick shaky/unseen nodes first
            pool = [
                n for n in graph["nodes"]
                if (not session_rec or session_rec["id"] in (n.get("sessionIds") or []) or not n.get("sessionIds"))
            ]
            if not pool:
                pool = list(graph["nodes"])
            pool.sort(key=lambda n: (0 if n.get("coverage") == "shaky" else (1 if n.get("coverage") == "unseen" else 2), n.get("depth", 0), n["id"]))
            target_node_ids = [n["id"] for n in pool[:2]]

        target_nodes = [nodes_by_id[nid] for nid in target_node_ids if nid in nodes_by_id]
        coverages = [n.get("coverage", "unseen") for n in target_nodes]
        if any(c == "unseen" for c in coverages) or not coverages:
            cue_unfamiliarity = 0
            familiarity = "familiar"
        elif any(c == "shaky" for c in coverages):
            cue_unfamiliarity = 1
            familiarity = "transfer"
        else:
            cue_unfamiliarity = 2
            familiarity = "novel"

        catalog = load_problems_catalog(root)
        chosen: dict[str, Any] | None = None
        for prob in catalog:
            if target_node_ids and any(nid in (prob.get("nodes") or []) for nid in target_node_ids):
                chosen = dict(prob)
                break
        if chosen is None and catalog and not args.nodes:
            chosen = dict(catalog[0])

        if chosen is not None:
            combined_nodes = list(dict.fromkeys([*(chosen.get("nodes") or []), *target_node_ids]))
            ideas_count = max(1, len(combined_nodes))
            cue_val = int(chosen.get("cue_unfamiliarity", cue_unfamiliarity))
            chosen["nodes"] = combined_nodes
            chosen["number_of_ideas_combined"] = ideas_count
            chosen["cue_unfamiliarity"] = cue_val
            chosen["difficulty"] = ideas_count + cue_val
            chosen["rule"] = "difficulty = number_of_ideas_combined + cue_unfamiliarity"
            problem = chosen
        else:
            ideas_count = max(1, len(target_node_ids) or 1)
            difficulty = ideas_count + cue_unfamiliarity
            names = ", ".join(n["name"] for n in target_nodes) if target_nodes else ", ".join(target_node_ids) or "core concept"
            stmts = " ".join(n["statement"] for n in target_nodes)
            pid = f"prob_{short_hash(names + str(difficulty), 8)}"
            problem = {
                "id": pid,
                "title": f"Practice: {names}",
                "statement": f"Combine ({names}) with {familiarity} cues: Given {stmts[:180] or names}, compute the quantitative response and explain why each premise is required.",
                "nodes": target_node_ids or ["n_default"],
                "number_of_ideas_combined": ideas_count,
                "cue_unfamiliarity": cue_unfamiliarity,
                "difficulty": difficulty,
                "familiarity": familiarity,
                "rule": "difficulty = number_of_ideas_combined + cue_unfamiliarity",
                "hints": [
                    f"Start from the definition of {names}.",
                    "Check units and steady-state behavior when error = 0.",
                ],
            }

        if args.json:
            return problem
        return (
            f"Problem {problem['id']} (difficulty={problem['difficulty']} = "
            f"{problem['number_of_ideas_combined']} ideas + {problem['cue_unfamiliarity']} cue [{problem['familiarity']}]):\n"
            f"Nodes: {', '.join(problem['nodes'])}\n"
            f"{problem['statement']}"
        )

    if sub == "record":
        now = now_iso()
        session_rec = None
        session_id = args.id
        try:
            session_rec = load_session(root, args.id)
            session_id = session_rec["id"]
        except ValueError:
            if not session_id.startswith("chat_"):
                session_id = f"chat_{session_id}"

        target_nodes = list(args.nodes or [])
        if not target_nodes:
            for prob in load_problems_catalog(root):
                if prob["id"] == args.problem:
                    target_nodes = list(prob.get("nodes") or [])
                    break
        if not target_nodes and graph["nodes"]:
            target_nodes = [graph["nodes"][0]["id"]]

        attempt_id = f"att_{short_hash(f'{session_id}:{args.problem}:{now}', 10)}"
        fp = short_hash(f"{attempt_id}:{args.result}:{args.hints}", 64)
        cov_entry = {
            "chatId": session_id,
            "fp": fp,
            "tokens": 64,
            "foldedAt": now,
            "attemptId": attempt_id,
            "problemId": args.problem,
            "result": args.result,
            "hints": int(args.hints or 0),
            "nodes": target_nodes,
            "nodeId": target_nodes[0] if target_nodes else None,
        }
        append_jsonl(root / "graph" / "coverage.jsonl", cov_entry)

        attempt_record = {
            "id": attempt_id,
            "sessionId": session_id,
            "problemId": args.problem,
            "result": args.result,
            "hints": int(args.hints or 0),
            "nodes": target_nodes,
            "timestamp": now,
        }
        append_jsonl(root / "practice_attempts.jsonl", attempt_record)

        if session_rec is not None:
            session_rec.setdefault("cards", []).append({
                "id": attempt_id,
                "kind": "practice",
                "step": args.problem,
                "result": args.result,
                "hints": int(args.hints or 0),
                "nodes": target_nodes,
                "timestamp": now,
            })
            session_rec["updatedAt"] = now
            save_session(root, session_rec)

        breakpoint_updated = False
        track = session_rec.get("track") if session_rec else ("internship" if "internship" in session_id else "general")
        if track == "internship" or getattr(args, "internship_progress", None) or os.environ.get("LH_INTERNSHIP_PROGRESS"):
            prog_path = resolve_internship_progress(root, getattr(args, "internship_progress", None))
            prog = read_json_safe(prog_path)
            if isinstance(prog, dict) and isinstance(prog.get("breakpoints"), list):
                bps = prog["breakpoints"]
                prob_low = args.problem.lower()
                matched_bp = None
                for bp in bps:
                    if isinstance(bp, dict) and (
                        prob_low in str(bp.get("topic", "")).lower()
                        or prob_low in str(bp.get("lesson", "")).lower()
                        or any(nid.lower() in str(bp.get("topic", "")).lower() for nid in target_nodes)
                    ):
                        matched_bp = bp
                        break
                if matched_bp is None:
                    for bp in bps:
                        if isinstance(bp, dict) and bp.get("status") == "open":
                            matched_bp = bp
                            break
                if matched_bp is None and bps:
                    matched_bp = bps[0]
                if matched_bp is not None:
                    matched_bp["status"] = "retested" if (args.result == "correct" and int(args.hints or 0) <= 1) else "open"
                    matched_bp["last_practice"] = {
                        "problem": args.problem,
                        "result": args.result,
                        "hints": int(args.hints or 0),
                        "date": now[:10],
                    }
                    breakpoint_updated = True
                write_json_atomic(prog_path, prog)

        if args.json:
            return {
                "ok": True,
                "attempt": attempt_record,
                "breakpointUpdated": breakpoint_updated,
            }
        return f"Recorded practice {args.problem} on {session_id}: result={args.result} hints={args.hints} (breakpoint_updated={breakpoint_updated})"

    raise ValueError(f"Unknown practice command: {sub}")


def cmd_job(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    sub = args.job_cmd

    if sub == "new":
        settings = read_json_safe(root / "settings.json", DEFAULT_SETTINGS)
        hw = settings.get("helper_worker", DEFAULT_SETTINGS["helper_worker"]) if isinstance(settings, dict) else DEFAULT_SETTINGS["helper_worker"]
        worker = args.worker or hw.get(args.kind) or hw.get("default", "inline")

        brief_path_in = Path(args.brief_file)
        if brief_path_in.exists():
            brief_text = brief_path_in.read_text(encoding="utf-8")
        else:
            brief_text = str(args.brief_file)

        now = now_iso()
        short_id = short_hash(f"{args.kind}:{brief_text[:120]}:{now}", 8)
        job_id = f"job_{short_id}"
        chat_id = args.session or "chat_general"

        argon_cmd: str | None = None
        written_brief_path: str | None = None
        if worker == "argon":
            bdir = resolve_briefs_dir(root, getattr(args, "briefs_dir", None))
            bdir.mkdir(parents=True, exist_ok=True)
            bfile = bdir / f"learn-job-{short_id}.md"
            bfile.write_text(
                f"# Learn Helper Job: {job_id} ({args.kind})\n\n"
                f"- Kind: `{args.kind}`\n"
                f"- Session: `{chat_id}`\n"
                f"- Created: `{now}`\n\n"
                f"## Brief\n\n{brief_text.strip()}\n",
                encoding="utf-8",
            )
            try:
                rel_brief = bfile.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel_brief = str(bfile)
            written_brief_path = str(bfile)
            argon_cmd = f".agent-ops/argon.ps1 -Id learn-{short_id} -Brief {rel_brief}"

        job_record: dict[str, Any] = {
            "id": job_id,
            "kind": args.kind,
            "chatId": chat_id,
            "toolCallId": f"call_{short_id}",
            "status": "queued" if worker == "argon" else "running",
            "activity": "dispatched_argon" if worker == "argon" else "inline",
            "worker": worker,
            "title": args.title or f"{args.kind.capitalize()} job {short_id}",
            "brief": brief_text,
            "briefFile": written_brief_path or str(brief_path_in),
            "argonCommand": argon_cmd,
            "output": None,
            "demoId": f"demo_{short_id}" if args.kind == "demo" else None,
            "createdAt": now,
            "updatedAt": now,
            "usage": {},
        }
        write_json_atomic(root / "jobs" / f"{job_id}.json", job_record)
        if args.json:
            return job_record
        if argon_cmd:
            return f"Created {job_id} (kind={args.kind}, worker=argon)\nBrief: {written_brief_path}\nRun: {argon_cmd}"
        return f"Created {job_id} (kind={args.kind}, worker={worker})"

    if sub == "list":
        jobs = list_all_jobs(root)
        if args.json:
            return {"jobs": jobs}
        if not jobs:
            return "No jobs found."
        lines = [f"Jobs ({len(jobs)}):"]
        for j in jobs:
            lines.append(f"- {j['id']} [{j.get('kind')}/{j.get('status')}] worker={j.get('worker', 'inline')} {j.get('title', '')}")
        return "\n".join(lines)

    if sub == "show":
        job = load_job(root, args.id)
        if args.json:
            return job
        lines = [
            f"Job: {job['id']} ({job.get('kind')})",
            f"Status: {job.get('status')} | Worker: {job.get('worker', 'inline')}",
            f"Title: {job.get('title', '')}",
            f"Output: {job.get('output') or 'none'}",
        ]
        if job.get("argonCommand"):
            lines.append(f"Argon: {job['argonCommand']}")
        return "\n".join(lines)

    if sub == "done":
        job = load_job(root, args.id)
        now = now_iso()
        job["status"] = "done"
        job["activity"] = "delivered"
        job["output"] = str(args.output)
        job["updatedAt"] = now
        out_path = Path(args.output)
        if job.get("kind") == "demo" and out_path.exists():
            demos_dir = root / "demos"
            demos_dir.mkdir(parents=True, exist_ok=True)
            demo_stem = out_path.stem
            target_demo = demos_dir / f"{demo_stem}.html"
            if out_path.resolve() != target_demo.resolve():
                shutil.copyfile(out_path, target_demo)
            job["demoId"] = demo_stem
            job["result"] = {
                "title": job.get("title") or demo_stem,
                "notes": f"Interactive demo {demo_stem}",
                "html": target_demo.read_text(encoding="utf-8"),
            }
        write_json_atomic(root / "jobs" / f"{job['id']}.json", job)
        if args.json:
            return job
        return f"Job {job['id']} marked done (output={args.output})"

    raise ValueError(f"Unknown job command: {sub}")


def list_all_jobs(root: Path) -> list[dict[str, Any]]:
    jdir = root / "jobs"
    if not jdir.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(jdir.glob("*.json")):
        rec = read_json_safe(p)
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    out.sort(key=lambda r: (r.get("updatedAt") or "", r.get("id") or ""), reverse=True)
    return out


def load_job(root: Path, job_id: str) -> dict[str, Any]:
    jdir = root / "jobs"
    direct = jdir / f"{job_id}.json"
    if direct.exists():
        return json.loads(direct.read_text(encoding="utf-8"))
    if not job_id.startswith("job_"):
        pref = jdir / f"job_{job_id}.json"
        if pref.exists():
            return json.loads(pref.read_text(encoding="utf-8"))
    raise ValueError(f"Job not found: {job_id}")


DEFAULT_STEP_RESPONSE_DEMO_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>First-Order Step Response & PID Integral Action Demo</title>
<style>
  :root { --bg: #0f172a; --panel: #1e293b; --text: #f8fafc; --accent: #38bdf8; --border: #334155; }
  body { margin: 0; font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 16px; }
  .card { max-width: 760px; margin: 0 auto; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 12px 0; }
  label { display: flex; flex-direction: column; font-size: 13px; gap: 4px; }
  canvas { width: 100%; height: 260px; background: #090d16; border-radius: 8px; border: 1px solid var(--border); }
  .metrics { display: flex; gap: 16px; font-size: 13px; margin-top: 10px; color: #cbd5e1; flex-wrap: wrap; }
</style>
</head>
<body>
<div class="card">
  <h2 style="margin:0 0 6px 0;font-size:18px;">First-Order Step Response & Payload Disturbance</h2>
  <p style="margin:0;font-size:13px;color:#94a3b8;">Adjust proportional gain Kp, integral gain Ki, and constant payload disturbance d to inspect steady-state sag vs zero steady-state error.</p>
  <div class="controls">
    <label>Proportional Gain Kp: <span id="kpVal">2.5</span>
      <input id="kp" type="range" min="0.5" max="8.0" step="0.1" value="2.5">
    </label>
    <label>Integral Gain Ki: <span id="kiVal">0.0</span>
      <input id="ki" type="range" min="0.0" max="4.0" step="0.1" value="0.0">
    </label>
    <label>Payload Disturbance d: <span id="dVal">1.0</span>
      <input id="dist" type="range" min="0.0" max="3.0" step="0.1" value="1.0">
    </label>
  </div>
  <canvas id="plot" width="720" height="260"></canvas>
  <div class="metrics" id="metrics"></div>
</div>
<script>
const kpEl = document.getElementById('kp'), kiEl = document.getElementById('ki'), dEl = document.getElementById('dist');
const canvas = document.getElementById('plot'), ctx = canvas.getContext('2d');
function render() {
  const kp = parseFloat(kpEl.value), ki = parseFloat(kiEl.value), d = parseFloat(dEl.value);
  document.getElementById('kpVal').textContent = kp.toFixed(1);
  document.getElementById('kiVal').textContent = ki.toFixed(1);
  document.getElementById('dVal').textContent = d.toFixed(1);
  const dt = 0.02, steps = 400, r = 1.0;
  let y = 0, integ = 0, pts = [];
  for (let i = 0; i <= steps; i++) {
    const e = r - y;
    integ += e * dt;
    const u = kp * e + ki * integ - d;
    y += (-y + u) * dt;
    pts.push({t: i * dt, y});
  }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const yToPx = v => canvas.height - 30 - (v / 1.5) * (canvas.height - 50);
  ctx.strokeStyle = '#475569'; ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(0, yToPx(1.0)); ctx.lineTo(canvas.width, yToPx(1.0)); ctx.stroke();
  ctx.setLineDash([]); ctx.strokeStyle = '#38bdf8'; ctx.lineWidth = 2.5;
  ctx.beginPath();
  pts.forEach((p, idx) => {
    const x = (idx / steps) * canvas.width, py = yToPx(p.y);
    if (idx === 0) ctx.moveTo(x, py); else ctx.lineTo(x, py);
  });
  ctx.stroke();
  const finalY = pts[pts.length - 1].y, ssErr = Math.abs(r - finalY);
  document.getElementById('metrics').innerHTML =
    `<span>Final y(T): <b>${finalY.toFixed(3)}</b></span>` +
    `<span>Steady-state sag |r - y|: <b>${ssErr.toFixed(3)}</b></span>` +
    `<span>Regime: <b>${ki > 0.01 ? 'Integral pulls error to 0 (积分消除稳态误差)' : 'P-only steady sag'}</b></span>`;
}
[kpEl, kiEl, dEl].forEach(el => el.addEventListener('input', render));
render();
</script>
</body>
</html>
"""


def cmd_import_internship(args: argparse.Namespace, root: Path) -> dict[str, Any] | str:
    intern_dir = Path(getattr(args, "internship_dir", None) or DEFAULT_INTERNSHIP_DIR).resolve()
    prog_path = intern_dir / "data" / "progress.json"
    gloss_path = intern_dir / "data" / "glossary.json"

    prog = read_json_safe(prog_path, {})
    glossary = read_json_safe(gloss_path, [])
    base_date = str(prog.get("updated") or "2026-10-24")
    base_iso = f"{base_date}T10:00:00.000Z"

    lessons = prog.get("lessons") if isinstance(prog, dict) and isinstance(prog.get("lessons"), list) else []
    breakpoints = prog.get("breakpoints") if isinstance(prog, dict) and isinstance(prog.get("breakpoints"), list) else []

    imported_sessions: list[str] = []
    # Import taught/quizzed lessons + first planned lesson (L01, L01R, L02) with deterministic IDs
    target_lessons = [l for l in lessons if isinstance(l, dict) and l.get("id") in ("L01", "L01R", "L02")]
    if not target_lessons and lessons:
        target_lessons = [l for l in lessons[:3] if isinstance(l, dict)]

    for idx, lesson in enumerate(target_lessons):
        lid = str(lesson.get("id", f"L0{idx+1}"))
        sid = f"chat_internship_{lid.lower()}"
        ldate = str(lesson.get("date") or base_date)
        created_iso = f"{ldate}T09:0{idx}:00.000Z"
        updated_iso = f"{ldate}T10:0{idx}:00.000Z"
        status_raw = str(lesson.get("status") or "planned")

        steps = [
            {"id": "s1_physics", "title": "Rotor thrust and hover balance (T = mg)", "deps": [], "status": "done" if status_raw != "planned" else "current"},
            {"id": "s2_tilt", "title": "Tilted thrust components (T cos theta, T sin theta, a = g tan theta)", "deps": ["s1_physics"], "status": "done" if status_raw == "quizzed" else ("current" if status_raw == "taught" else "todo")},
            {"id": "s3_cascade", "title": "Cascade control loops (位置环 -> 速度环 -> 姿态环 -> 角速度环)", "deps": ["s2_tilt"], "status": "done" if status_raw == "quizzed" else "todo"},
            {"id": "s4_integral", "title": "Payload disturbance and integral term (积分项消除稳态误差)", "deps": ["s3_cascade"], "status": "current" if status_raw == "quizzed" else "todo"},
        ]
        edges = [{"from": dep, "to": s["id"]} for s in steps for dep in s["deps"]]
        cur_step = next((s["id"] for s in steps if s["status"] == "current"), steps[0]["id"])

        lesson_bps = [bp.get("topic", "") + ": " + bp.get("gap", "") for bp in breakpoints if isinstance(bp, dict) and bp.get("lesson") == lid and bp.get("status") == "open"]

        links = []
        if lid == "L01":
            links = ["chat_internship_l01r", "chat_internship_l02"]
        elif lid == "L01R":
            links = ["chat_internship_l01"]
        elif lid == "L02":
            links = ["chat_internship_l01"]

        existing_rec = read_json_safe(root / "chats" / f"{sid}.json")
        cards = [
            {
                "id": f"card_{lid.lower()}_1",
                "kind": "check",
                "step": "s2_tilt",
                "confidence": 75,
                "think1": "Vertical component holds weight mg = T cos(theta); horizontal component accelerates mass m a = T sin(theta).",
                "q1": "a = g tan(theta) in m/s^2",
                "score": 8,
                "feedback": "Good derivation of lateral acceleration from tilt angle.",
                "timestamp": updated_iso,
            }
        ]
        messages = [
            {
                "id": f"msg_{lid.lower()}_1",
                "role": "user",
                "timestamp": 1792836000000 + idx * 1000,
                "content": f"Imported internship lesson {lid}: {lesson.get('en')} ({lesson.get('zh')})",
            },
            {
                "id": f"msg_{lid.lower()}_2",
                "role": "assistant",
                "timestamp": 1792836000500 + idx * 1000,
                "content": [
                    {
                        "type": "text",
                        "text": f"Lesson {lid} ({lesson.get('zh')}): focus on thrust vector decomposition, cascade PID inner/outer loops, and integral steady-state error removal.",
                    }
                ],
            },
        ]
        if isinstance(existing_rec, dict):
            # Preserve any extra links added later while keeping deterministic base
            for l in existing_rec.get("links") or []:
                if l not in links:
                    links.append(l)

        session_doc = {
            "id": sid,
            "title": f"{lid} · {lesson.get('en', '')} ({lesson.get('zh', '')})",
            "topic": f"{lesson.get('en', '')} ({lesson.get('zh', '')})",
            "goal": f"Master {lid} for Chinese UAV algorithm interview",
            "track": "internship",
            "status": "open",
            "kind": "teach",
            "version": 1,
            "createdAt": created_iso,
            "updatedAt": updated_iso,
            "links": links,
            "cards": cards,
            "openQuestions": lesson_bps[:4],
            "messages": messages,
            "settings": {
                "model": {"provider": "claude-code", "id": "claude-desktop"},
                "thinkingLevel": "low",
            },
            "turns": [],
            "tree": {
                "nodes": steps,
                "steps": steps,
                "edges": edges,
                "currentStep": cur_step,
                "version": 1,
            },
        }
        save_session(root, session_doc)
        imported_sessions.append(sid)

    # Register source metadata for L01.md in graph/sources.json
    sources = read_json_safe(root / "graph" / "sources.json", {})
    if not isinstance(sources, dict):
        sources = {}
    sources["src_internship_l01"] = {
        "id": "src_internship_l01",
        "filePath": "docs/learning/internship/data/lessons/L01.md",
        "importedAt": base_iso,
    }
    write_json_atomic(root / "graph" / "sources.json", sources)

    # Deterministic internship propositions & chains
    intern_nodes = [
        {
            "id": "n_intern_rotor_thrust",
            "name": "Quadrotor rotor thrust (推力 T = k omega^2)",
            "statement": "Each propeller spinning at angular speed omega produces thrust T_i = k * omega^2 along the body z-axis (机体 z 轴).",
            "aliases": ["推力 T_i = k omega^2", "body z-axis thrust"],
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01", "messageId": "msg_l01_1", "step": "s1_physics", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "n_intern_hover_balance",
            "name": "Hover thrust balance (悬停 T = mg)",
            "statement": "A quadrotor holds constant altitude in hover (悬停) when total vertical thrust equals weight: T = m * g.",
            "aliases": ["悬停推力 T = mg"],
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01", "messageId": "msg_l01_1", "step": "s1_physics", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "n_intern_underactuated",
            "name": "Underactuated 6-DoF dynamics (欠驱动)",
            "statement": "A quadrotor has 4 independent motor inputs and 6 degrees of freedom (自由度), making it underactuated (欠驱动): horizontal acceleration requires tilting the thrust vector.",
            "aliases": ["欠驱动 4 inputs 6 DoF"],
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01", "messageId": "msg_l01_1", "step": "s2_tilt", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "n_intern_tilt_tan",
            "name": "Tilt-to-move acceleration (a = g tan theta)",
            "statement": "When tilted by angle theta while holding altitude (T cos theta = m g), horizontal thrust T sin theta produces lateral acceleration a = g * tan(theta) in m/s^2.",
            "aliases": ["T cos theta vertical, T sin theta horizontal", "a = g tan theta"],
            "createdAt": base_iso,
            "provenance": [
                {"chatId": "chat_internship_l01", "messageId": "msg_l01_1", "step": "s2_tilt", "source": "L01.md", "timestamp": base_iso},
                {"chatId": "chat_internship_l01r", "messageId": "msg_l01r_1", "step": "s2_tilt", "source": "L01.md", "timestamp": base_iso},
            ],
        },
        {
            "id": "n_intern_thrust_vector_3d",
            "name": "3D thrust vector from desired acceleration (推力矢量)",
            "statement": "For desired acceleration a = (ax, ay, az), the controller commands thrust magnitude T = m * sqrt(ax^2 + ay^2 + (az + g)^2) and tilt tan(theta) = sqrt(ax^2 + ay^2) / (az + g).",
            "aliases": ["期望加速度 推力矢量合成"],
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01r", "messageId": "msg_l01r_1", "step": "s2_tilt", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "n_intern_cascade_loops",
            "name": "Cascaded flight control architecture (串级控制)",
            "statement": "Flight control runs as a fast-inner to slow-outer cascade (串级控制): 位置环 -> 速度环 -> 姿态环 -> 角速度环 -> 混控器; the attitude loop is the inner loop of the velocity loop (姿态环是速度环的内环).",
            "aliases": ["姿态环是速度环的内环", "位置环 速度环 姿态环 角速度环"],
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01", "messageId": "msg_l01_2", "step": "s3_cascade", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "n_intern_integral_steady_state",
            "name": "Integral term removes constant payload sag (积分项消除稳态误差)",
            "statement": "Under a constant payload disturbance (载荷扰动), proportional control alone leaves a steady-state altitude sag; the integral term (积分项) accumulates past error so its output holds non-zero offset at zero error (e = 0).",
            "aliases": ["积分项 稳态误差", "integral action removes steady-state error"],
            "createdAt": base_iso,
            "provenance": [
                {"chatId": "chat_internship_l01", "messageId": "msg_l01_2", "step": "s4_integral", "source": "L01.md", "timestamp": base_iso},
                {"chatId": "chat_internship_l02", "messageId": "msg_l02_1", "step": "s4_integral", "source": "progress.json", "timestamp": base_iso},
            ],
        },
    ]

    intern_chains = [
        {
            "id": "arg_intern_tilt_accel",
            "premises": ["n_intern_rotor_thrust", "n_intern_hover_balance", "n_intern_underactuated"],
            "conclusion": "n_intern_tilt_tan",
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01", "messageId": "msg_l01_1", "step": "s2_tilt", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "arg_intern_3d_vector",
            "premises": ["n_intern_tilt_tan", "n_intern_hover_balance"],
            "conclusion": "n_intern_thrust_vector_3d",
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01r", "messageId": "msg_l01r_1", "step": "s2_tilt", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "arg_intern_cascade_from_underactuated",
            "premises": ["n_intern_underactuated", "n_intern_thrust_vector_3d"],
            "conclusion": "n_intern_cascade_loops",
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l01", "messageId": "msg_l01_2", "step": "s3_cascade", "source": "L01.md", "timestamp": base_iso}],
        },
        {
            "id": "arg_intern_integral_hold",
            "premises": ["n_intern_hover_balance", "n_intern_cascade_loops"],
            "conclusion": "n_intern_integral_steady_state",
            "createdAt": base_iso,
            "provenance": [{"chatId": "chat_internship_l02", "messageId": "msg_l02_1", "step": "s4_integral", "source": "L01.md", "timestamp": base_iso}],
        },
    ]

    # Merge idempotently into graph/nodes.jsonl and graph/chains.jsonl
    existing_nodes = [n for n in read_jsonl(root / "graph" / "nodes.jsonl") if not str(n.get("id", "")).startswith("n_intern_")]
    write_jsonl(root / "graph" / "nodes.jsonl", [*existing_nodes, *intern_nodes])

    existing_chains = [c for c in read_jsonl(root / "graph" / "chains.jsonl") if not str(c.get("id", "")).startswith("arg_intern_")]
    write_jsonl(root / "graph" / "chains.jsonl", [*existing_chains, *intern_chains])

    existing_sessions_ledger = [
        s for s in read_jsonl(root / "graph" / "sessions.jsonl")
        if not str(s.get("chatId", "")).startswith("chat_internship_")
    ]
    for sid in imported_sessions:
        existing_sessions_ledger.append({"chatId": sid, "foldedAt": base_iso})
    write_jsonl(root / "graph" / "sessions.jsonl", existing_sessions_ledger)

    existing_cov = [
        c for c in read_jsonl(root / "graph" / "coverage.jsonl")
        if not str(c.get("chatId", "")).startswith("chat_internship_")
    ]
    intern_cov = [
        {
            "chatId": "chat_internship_l01",
            "fp": short_hash("intern_cov_n_intern_hover_balance", 64),
            "tokens": 64,
            "foldedAt": base_iso,
            "attemptId": "att_intern_hover",
            "problemId": "intern_hover_balance",
            "result": "correct",
            "hints": 0,
            "nodes": ["n_intern_hover_balance", "n_intern_rotor_thrust"],
            "nodeId": "n_intern_hover_balance",
        },
        {
            "chatId": "chat_internship_l01",
            "fp": short_hash("intern_cov_n_intern_tilt_tan", 64),
            "tokens": 80,
            "foldedAt": base_iso,
            "attemptId": "att_intern_tilt",
            "problemId": "intern_tilt_vector",
            "result": "partial",
            "hints": 1,
            "nodes": ["n_intern_tilt_tan", "n_intern_thrust_vector_3d"],
            "nodeId": "n_intern_tilt_tan",
        },
        {
            "chatId": "chat_internship_l01",
            "fp": short_hash("intern_cov_n_intern_integral", 64),
            "tokens": 72,
            "foldedAt": base_iso,
            "attemptId": "att_intern_integral",
            "problemId": "intern_payload_integral",
            "result": "partial",
            "hints": 1,
            "nodes": ["n_intern_integral_steady_state"],
            "nodeId": "n_intern_integral_steady_state",
        },
    ]
    write_jsonl(root / "graph" / "coverage.jsonl", [*existing_cov, *intern_cov])

    # Ensure example demo exists and is registered as a deterministic job
    demos_dir = root / "demos"
    demos_dir.mkdir(parents=True, exist_ok=True)
    demo_file = demos_dir / "demo_step_response.html"
    if not demo_file.exists():
        demo_file.write_text(DEFAULT_STEP_RESPONSE_DEMO_HTML, encoding="utf-8")

    demo_job = {
        "id": "job_demo_step_response",
        "kind": "demo",
        "chatId": "chat_internship_l01",
        "toolCallId": "call_demo_step_response",
        "status": "done",
        "activity": "delivered",
        "worker": "inline",
        "title": "First-Order Step Response & Payload Integral Demo",
        "brief": "Interactive step response demo showing P-only steady-state sag vs PID integral action under payload disturbance.",
        "briefFile": "docs/learning/harness/data/demos/demo_step_response.html",
        "argonCommand": None,
        "output": "docs/learning/harness/data/demos/demo_step_response.html",
        "demoId": "demo_step_response",
        "createdAt": base_iso,
        "updatedAt": base_iso,
        "usage": {},
        "result": {
            "title": "First-Order Step Response & Payload Integral Demo",
            "notes": "Sliders for Kp, Ki, and payload disturbance d.",
            "html": demo_file.read_text(encoding="utf-8"),
        },
    }
    write_json_atomic(root / "jobs" / "job_demo_step_response.json", demo_job)

    if args.json:
        return {
            "importedSessions": imported_sessions,
            "nodes": len(intern_nodes),
            "chains": len(intern_chains),
            "glossaryCount": len(glossary) if isinstance(glossary, list) else 0,
        }
    return f"Imported {len(imported_sessions)} internship sessions ({', '.join(imported_sessions)}), {len(intern_nodes)} nodes, {len(intern_chains)} chains."


def build_api_response(root: Path, method: str, path: str, query_params: dict[str, list[str]], body_bytes: bytes = b"") -> tuple[int, str, bytes]:
    """Dispatch HTTP requests for `learn.py serve` and unit tests."""
    clean_path = unquote(path).rstrip("/") or "/"

    if method == "POST" and clean_path in ("/api/graph", "/api/graph/positions"):
        try:
            payload = json.loads(body_bytes.decode("utf-8") or "{}")
        except Exception as exc:
            return HTTPStatus.BAD_REQUEST, "application/json", json.dumps({"error": str(exc)}).encode("utf-8")
        incoming = payload.get("positions", payload) if isinstance(payload, dict) else {}
        pos_path = root / "graph" / "positions.json"
        current_doc = read_json_safe(pos_path, {"positions": {}})
        positions = current_doc.get("positions", {}) if isinstance(current_doc, dict) else {}
        if isinstance(incoming, dict):
            for nid, coords in incoming.items():
                if isinstance(coords, dict) and "x" in coords and "y" in coords:
                    positions[str(nid)] = {"x": float(coords["x"]), "y": float(coords["y"])}
        write_json_atomic(pos_path, {"positions": positions})
        return HTTPStatus.OK, "application/json", json.dumps({"ok": True, "positions": positions}).encode("utf-8")

    if method == "GET":
        if clean_path == "/api/sessions":
            ns = argparse.Namespace(session_cmd="list", track=None, limit=None, json=True)
            data = cmd_session(ns, root)
            return HTTPStatus.OK, "application/json", json.dumps(data, ensure_ascii=False).encode("utf-8")

        if clean_path.startswith("/api/session/"):
            sid = clean_path[len("/api/session/"):]
            try:
                ns = argparse.Namespace(session_cmd="show", id=sid, json=True)
                data = cmd_session(ns, root)
                return HTTPStatus.OK, "application/json", json.dumps(data, ensure_ascii=False).encode("utf-8")
            except ValueError as exc:
                return HTTPStatus.NOT_FOUND, "application/json", json.dumps({"error": str(exc)}).encode("utf-8")

        if clean_path.startswith("/api/plan/"):
            sid = clean_path[len("/api/plan/"):]
            try:
                rec = load_session(root, sid)
                idx, cur_step, steps = get_current_step_info(rec)
                tree = rec.get("tree") if isinstance(rec.get("tree"), dict) else {}
                payload = {
                    "sessionId": rec["id"],
                    "topic": rec.get("topic") or rec.get("title", ""),
                    "goal": rec.get("goal", ""),
                    "updatedAt": rec.get("updatedAt", ""),
                    "currentStepIndex": idx,
                    "currentStep": cur_step.get("id") if cur_step else None,
                    "steps": steps,
                    "edges": tree.get("edges") or [],
                    "mermaid": build_plan_mermaid(rec),
                }
                return HTTPStatus.OK, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except ValueError as exc:
                return HTTPStatus.NOT_FOUND, "application/json", json.dumps({"error": str(exc)}).encode("utf-8")

        if clean_path == "/api/graph":
            graph = load_merged_graph(root)
            session_filter = (query_params.get("session") or [None])[0]
            track_filter = (query_params.get("track") or [None])[0]
            nodes = graph["nodes"]
            if session_filter:
                nodes = [n for n in nodes if session_filter in (n.get("sessionIds") or [])]
            if track_filter:
                nodes = [n for n in nodes if track_filter in (n.get("tracks") or [])]
            kept_ids = {n["id"] for n in nodes}
            chains = [
                c for c in graph["chains"]
                if c.get("conclusion") in kept_ids or any(p in kept_ids for p in (c.get("premises") or []))
            ]
            return HTTPStatus.OK, "application/json", json.dumps({**graph, "nodes": nodes, "chains": chains}, ensure_ascii=False).encode("utf-8")

        if clean_path == "/api/search":
            q = (query_params.get("q") or [""])[0]
            in_param = (query_params.get("in") or ["chats,nodes,plans,problems"])[0]
            limit_param = int((query_params.get("limit") or ["20"])[0])
            scopes = {s.strip().lower() for s in in_param.split(",") if s.strip()}
            hits = run_search(root, q, scopes, limit=limit_param)
            return HTTPStatus.OK, "application/json", json.dumps({"query": q, "hits": hits}, ensure_ascii=False).encode("utf-8")

        if clean_path == "/api/practice" or clean_path.startswith("/api/practice/"):
            sid = clean_path[len("/api/practice/"):] if clean_path.startswith("/api/practice/") else "all"
            cov = read_jsonl(root / "graph" / "coverage.jsonl")
            att = read_jsonl(root / "practice_attempts.jsonl")
            merged_attempts: list[dict[str, Any]] = []
            seen: set[str] = set()
            for r in [*att, *cov]:
                if r.get("result") not in ("correct", "partial", "wrong"):
                    continue
                aid = str(r.get("id") or r.get("attemptId") or r.get("fp") or "")
                if aid and aid in seen:
                    continue
                if aid:
                    seen.add(aid)
                chat_id = r.get("sessionId") or r.get("chatId") or ""
                if sid not in ("", "all") and chat_id != sid and chat_id != f"chat_{sid}":
                    continue
                merged_attempts.append({
                    "id": aid or f"att_{len(merged_attempts)+1}",
                    "sessionId": chat_id,
                    "problemId": r.get("problemId", ""),
                    "result": r.get("result"),
                    "hints": int(r.get("hints") or 0),
                    "nodes": r.get("nodes") or ([r["nodeId"]] if r.get("nodeId") else []),
                    "timestamp": r.get("timestamp") or r.get("foldedAt") or "",
                })
            problems = load_problems_catalog(root)
            return HTTPStatus.OK, "application/json", json.dumps({
                "sessionId": sid or "all",
                "attempts": merged_attempts,
                "problems": problems,
            }, ensure_ascii=False).encode("utf-8")

        if clean_path == "/api/jobs":
            jobs = list_all_jobs(root)
            return HTTPStatus.OK, "application/json", json.dumps({"jobs": jobs}, ensure_ascii=False).encode("utf-8")

        if clean_path == "/api/demos" or clean_path.startswith("/api/demo/"):
            demo_id = clean_path[len("/api/demo/"):] if clean_path.startswith("/api/demo/") else "default"
            demos_dir = root / "demos"
            demo_files = sorted(demos_dir.glob("*.html")) if demos_dir.exists() else []
            chosen_file: Path | None = None
            for df in demo_files:
                if df.stem == demo_id or df.name == demo_id or f"job_{df.stem}" == demo_id:
                    chosen_file = df
                    break
            if chosen_file is None and demo_files:
                chosen_file = demo_files[0]
            if chosen_file is None:
                # Auto-provision fallback demo so /api/demo/<id> is always available
                demos_dir.mkdir(parents=True, exist_ok=True)
                chosen_file = demos_dir / "demo_step_response.html"
                chosen_file.write_text(DEFAULT_STEP_RESPONSE_DEMO_HTML, encoding="utf-8")
            html_text = chosen_file.read_text(encoding="utf-8")
            payload = {
                "id": chosen_file.stem,
                "title": chosen_file.stem.replace("_", " ").title(),
                "jobId": f"job_{chosen_file.stem}",
                "path": str(chosen_file),
                "url": f"/demos/{chosen_file.name}",
                "html": html_text,
            }
            return HTTPStatus.OK, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8")

        if clean_path.startswith("/demos/"):
            rel_name = clean_path[len("/demos/"):]
            demos_dir = root / "demos"
            candidate = demos_dir / rel_name
            if not candidate.exists() and not rel_name.endswith(".html"):
                candidate = demos_dir / f"{rel_name}.html"
            if candidate.exists() and candidate.is_file():
                return HTTPStatus.OK, "text/html; charset=utf-8", candidate.read_bytes()
            return HTTPStatus.NOT_FOUND, "application/json", json.dumps({"error": "Demo not found"}).encode("utf-8")

        # Static viewer files
        viewer_dir = DEFAULT_VIEWER_DIR
        rel = "index.html" if clean_path == "/" else clean_path.lstrip("/")
        target = (viewer_dir / rel).resolve()
        if viewer_dir.exists() and target.is_relative_to(viewer_dir.resolve()) and target.is_file():
            ctype, _ = mimetypes.guess_type(str(target))
            return HTTPStatus.OK, ctype or "application/octet-stream", target.read_bytes()
        if clean_path == "/":
            fallback_html = b"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Learn Viewer</title></head><body><h1>Learn Viewer API Running</h1></body></html>"
            return HTTPStatus.OK, "text/html; charset=utf-8", fallback_html

    return HTTPStatus.NOT_FOUND, "application/json", json.dumps({"error": f"Not found: {clean_path}"}).encode("utf-8")


def cmd_serve(args: argparse.Namespace, root: Path) -> None:
    host = args.host or "127.0.0.1"
    port = int(args.port or 8770)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            status, ctype, data = build_api_response(root, "GET", parsed.path, qs)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else b""
            status, ctype, data = build_api_response(root, "POST", parsed.path, qs, body)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *log_args: Any) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"LearnHarness viewer listening on http://{host}:{port} (data={root})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = ensure_data_dir(Path(tmp) / "data")
        # 1. Create two sessions and link
        s1 = main_Return(["--data-dir", str(root), "session", "new", "--topic", "PID Control", "--goal", "Understand integral action", "--track", "internship"]).strip()
        s2 = main_Return(["--data-dir", str(root), "session", "new", "--topic", "Cascaded Loops", "--goal", "Inner vs outer loop", "--track", "internship"]).strip()
        main_Return(["--data-dir", str(root), "session", "link", s1, s2])

        # 2. Plan set (5 steps) and advance
        plan_file = Path(tmp) / "plan.json"
        plan_file.write_text(
            json.dumps([
                {"id": "s1", "title": "Proportional action", "deps": [], "status": "current"},
                {"id": "s2", "title": "Steady-state sag under gravity", "deps": ["s1"], "status": "todo"},
                {"id": "s3", "title": "Integral term accumulation", "deps": ["s2"], "status": "todo"},
                {"id": "s4", "title": "Anti-windup saturation", "deps": ["s3"], "status": "todo"},
                {"id": "s5", "title": "Cascade tuning", "deps": ["s4"], "status": "todo"},
            ]),
            encoding="utf-8",
        )
        main_Return(["--data-dir", str(root), "plan", "set", s1, "--file", str(plan_file)])

        # 3. Log predict, reading, check cards
        for kind, text in [
            ("predict", f"[learn] session={s1} step=s1 kind=predict confidence=65\nthink1: P gain scales error\nq1: Steady sag remains"),
            ("reading", f"[learn] session={s1} step=s1 kind=reading confidence=75\nthink1: Weight needs non-zero thrust at zero error"),
            ("check", f"[learn] session={s1} step=s1 kind=check confidence=85\nthink1: Integral stores sum\nthink2: Holds output when e=0\nthink3: Removes sag\nq1: Integral term\nq2: Zero error"),
        ]:
            pfile = Path(tmp) / f"{kind}.txt"
            pfile.write_text(text, encoding="utf-8")
            main_Return(["--data-dir", str(root), "log", s1, "--kind", kind, "--step", "s1", "--payload-file", str(pfile)])

        main_Return(["--data-dir", str(root), "plan", "advance", s1])

        # 4. Extract 3 nodes + 2 chains
        nodes_file = Path(tmp) / "nodes.json"
        nodes_file.write_text(
            json.dumps({
                "nodes": [
                    {"id": "n1", "name": "P-only sag", "statement": "Proportional control requires non-zero error to counter a constant load."},
                    {"id": "n2", "name": "Integral memory", "statement": "The integral term accumulates error over time and holds its value when error reaches zero."},
                    {"id": "n3", "name": "Zero steady-state error", "statement": "Adding integral action eliminates steady-state altitude sag under constant payload disturbance."},
                ],
                "chains": [
                    {"premises": ["n1", "n2"], "conclusion": "n3"},
                    {"premises": ["n2"], "conclusion": "n3"},
                ],
            }),
            encoding="utf-8",
        )
        main_Return(["--data-dir", str(root), "extract", s1, "--file", str(nodes_file)])

        # 5. Search & continue
        search_out = main_Return(["--data-dir", str(root), "search", "integral"])
        assert s1 in search_out, f"Expected session {s1} in search output: {search_out}"
        cont_out = main_Return(["--data-dir", str(root), "continue", s1])
        assert "step 2" in cont_out.lower() and len(cont_out.encode("utf-8")) < 1500

        # 6. Practice & jobs
        prob_out = json.loads(main_Return(["--data-dir", str(root), "practice", "next", s1, "--json"]))
        assert prob_out["difficulty"] == prob_out["number_of_ideas_combined"] + prob_out["cue_unfamiliarity"]
        main_Return(["--data-dir", str(root), "practice", "record", s1, "--problem", prob_out["id"], "--result", "correct", "--hints", "0"])

        brief_f = Path(tmp) / "brief.md"
        brief_f.write_text("Build step response demo", encoding="utf-8")
        job_out = main_Return(["--data-dir", str(root), "job", "new", "--kind", "demo", "--brief-file", str(brief_f)])
        assert "argon.ps1" in job_out

        # 7. Vendor parity check
        for script, cmd in [(VENDOR_DIR / "graph.py", "sessions"), (VENDOR_DIR / "sessions.py", "list")]:
            proc = subprocess.run([sys.executable, str(script), "--data-dir", str(root), cmd], capture_output=True, text=True, check=False)
            assert proc.returncode == 0, f"{script.name} failed: {proc.stderr}"

    print("SELFTEST OK")
    return 0


def compact_output(text: str, *, full: bool = False) -> str:
    text = text.rstrip()
    if full:
        return text
    raw = text.encode("utf-8")
    if len(raw) <= COMPACT_BYTE_LIMIT:
        return text
    clipped = raw[: COMPACT_BYTE_LIMIT - 40].decode("utf-8", errors="ignore").rstrip()
    return f"{clipped}\n... (truncated; pass --full for all)"


def extract_global_flags(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Extract global flags regardless of whether they precede or follow the subcommand."""
    globals_found: dict[str, Any] = {
        "data_dir": None,
        "json": False,
        "full": False,
        "briefs_dir": None,
        "internship_progress": None,
    }
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--json":
            globals_found["json"] = True
            i += 1
        elif tok == "--full":
            globals_found["full"] = True
            i += 1
        elif tok in ("--data-dir", "--briefs-dir", "--internship-progress") and i + 1 < len(argv):
            key = tok.lstrip("-").replace("-", "_")
            globals_found[key] = argv[i + 1]
            i += 2
        elif tok.startswith("--data-dir="):
            globals_found["data_dir"] = tok.split("=", 1)[1]
            i += 1
        elif tok.startswith("--briefs-dir="):
            globals_found["briefs_dir"] = tok.split("=", 1)[1]
            i += 1
        elif tok.startswith("--internship-progress="):
            globals_found["internship_progress"] = tok.split("=", 1)[1]
            i += 1
        else:
            remaining.append(tok)
            i += 1
    return globals_found, remaining


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LearnHarness CLI and local viewer API")
    parser.add_argument("--data-dir", metavar="PATH", help="Data directory (default: docs/learning/harness/data)")
    parser.add_argument("--json", action="store_true", help="Output valid JSON")
    parser.add_argument("--full", action="store_true", help="Disable 1.5 KB compact output cap")
    parser.add_argument("--briefs-dir", metavar="PATH", help="Directory for Argon helper briefs")
    parser.add_argument("--internship-progress", metavar="PATH", help="Path to internship progress.json")

    sub = parser.add_subparsers(dest="command", required=True)

    # session
    p_sess = sub.add_parser("session", help="Manage learning sessions")
    s_sub = p_sess.add_subparsers(dest="session_cmd", required=True)
    s_new = s_sub.add_parser("new", help="Create a new learning session")
    s_new.add_argument("--topic", required=True)
    s_new.add_argument("--goal", required=True)
    s_new.add_argument("--track", choices=("internship", "thesis", "general"), default="general")
    s_new.add_argument("--link", nargs="*", default=[])
    s_new.add_argument("--title")
    s_new.add_argument("--id")

    s_list = s_sub.add_parser("list", help="List sessions")
    s_list.add_argument("--track")
    s_list.add_argument("--limit", type=int)

    s_show = s_sub.add_parser("show", help="Show session details")
    s_show.add_argument("id")

    s_link = s_sub.add_parser("link", help="Link two sessions bidirectionally")
    s_link.add_argument("id")
    s_link.add_argument("other")

    s_close = s_sub.add_parser("close", help="Close a session")
    s_close.add_argument("id")

    # continue
    p_cont = sub.add_parser("continue", help="Print compact resume pack (< 1.5 KB)")
    p_cont.add_argument("id", nargs="?")

    # plan
    p_plan = sub.add_parser("plan", help="Manage session dependency plan")
    pl_sub = p_plan.add_subparsers(dest="plan_cmd", required=True)
    pl_set = pl_sub.add_parser("set", help="Set session plan from JSON file")
    pl_set.add_argument("id")
    pl_set.add_argument("--file", required=True)

    pl_adv = pl_sub.add_parser("advance", help="Mark current step done and advance to next step")
    pl_adv.add_argument("id")

    pl_show = pl_sub.add_parser("show", help="Show text dependency tree")
    pl_show.add_argument("id")

    pl_merm = pl_sub.add_parser("mermaid", help="Print Mermaid flowchart of plan")
    pl_merm.add_argument("id")

    # log
    p_log = sub.add_parser("log", help="Log a card response and append to session messages")
    p_log.add_argument("id")
    p_log.add_argument("--kind", choices=("predict", "reading", "check", "probe", "practice", "note"), required=True)
    p_log.add_argument("--step", required=True)
    p_log.add_argument("--payload-file", required=True)
    p_log.add_argument("--score", type=float)
    p_log.add_argument("--feedback")

    # extract
    p_ext = sub.add_parser("extract", help="Extract propositions and reasoning chains into the knowledge graph")
    p_ext.add_argument("id")
    p_ext.add_argument("--file", required=True)

    # search
    p_search = sub.add_parser("search", help="Search across chats, nodes, plans, problems")
    p_search.add_argument("query")
    p_search.add_argument("--in", dest="scopes", default="chats,nodes,plans,problems")
    p_search.add_argument("--limit", type=int, default=15)

    # probe
    p_probe = sub.add_parser("probe", help="Diagnostic probe tools")
    pr_sub = p_probe.add_subparsers(dest="probe_cmd", required=True)
    pr_seed = pr_sub.add_parser("seed", help="Seed probe candidates from graph and breakpoints")
    pr_seed.add_argument("id")

    # practice
    p_prac = sub.add_parser("practice", help="Adaptive practice stream")
    prac_sub = p_prac.add_subparsers(dest="practice_cmd", required=True)
    prac_next = prac_sub.add_parser("next", help="Select or generate next problem")
    prac_next.add_argument("id", nargs="?")
    prac_next.add_argument("--nodes", nargs="*")

    prac_rec = prac_sub.add_parser("record", help="Record practice outcome")
    prac_rec.add_argument("id")
    prac_rec.add_argument("--problem", required=True)
    prac_rec.add_argument("--result", choices=("correct", "partial", "wrong"), required=True)
    prac_rec.add_argument("--hints", type=int, default=0)
    prac_rec.add_argument("--nodes", nargs="*")

    # job
    p_job = sub.add_parser("job", help="Manage helper subagent jobs (research, pdf, visual, demo)")
    j_sub = p_job.add_subparsers(dest="job_cmd", required=True)
    j_new = j_sub.add_parser("new", help="Create a helper job")
    j_new.add_argument("--kind", choices=("research", "pdf", "visual", "demo"), required=True)
    j_new.add_argument("--brief-file", required=True)
    j_new.add_argument("--session")
    j_new.add_argument("--worker", choices=("inline", "argon"))
    j_new.add_argument("--title")

    j_sub.add_parser("list", help="List jobs")
    j_show = j_sub.add_parser("show", help="Show job details")
    j_show.add_argument("id")
    j_done = j_sub.add_parser("done", help="Mark job done with output artifact path")
    j_done.add_argument("id")
    j_done.add_argument("--output", required=True)

    # import-internship
    p_imp = sub.add_parser("import-internship", help="Idempotently import existing internship lessons and progress")
    p_imp.add_argument("--internship-dir")

    # selftest
    sub.add_parser("selftest", help="Run internal self-test in a temp directory")

    # serve
    p_serve = sub.add_parser("serve", help="Serve local viewer and JSON API")
    p_serve.add_argument("--port", type=int, default=8770)
    p_serve.add_argument("--host", default="127.0.0.1")

    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any] | str:
    root = ensure_data_dir(resolve_data_dir(args.data_dir))
    cmd = args.command
    if cmd == "session":
        return cmd_session(args, root)
    if cmd == "continue":
        return cmd_continue(args, root)
    if cmd == "plan":
        return cmd_plan(args, root)
    if cmd == "log":
        return cmd_log(args, root)
    if cmd == "extract":
        return cmd_extract(args, root)
    if cmd == "search":
        return cmd_search(args, root)
    if cmd == "probe":
        return cmd_probe(args, root)
    if cmd == "practice":
        return cmd_practice(args, root)
    if cmd == "job":
        return cmd_job(args, root)
    if cmd == "import-internship":
        return cmd_import_internship(args, root)
    raise ValueError(f"Unsupported command: {cmd}")


def main_Return(argv: list[str]) -> str:
    """Execute CLI arguments in-process and return formatted string output."""
    globals_found, remaining = extract_global_flags(argv)
    parser = build_parser()
    args = parser.parse_args(remaining)
    for k, v in globals_found.items():
        if v:
            setattr(args, k, v)
    out = dispatch(args)
    if isinstance(out, dict):
        return json.dumps(out, ensure_ascii=False, indent=2 if args.full else None, allow_nan=False)
    return compact_output(str(out), full=bool(args.full))


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    globals_found, remaining = extract_global_flags(raw_argv)
    parser = build_parser()
    args = parser.parse_args(remaining)
    for k, v in globals_found.items():
        if v:
            setattr(args, k, v)

    try:
        if args.command == "selftest":
            return cmd_selftest()
        if args.command == "serve":
            root = ensure_data_dir(resolve_data_dir(args.data_dir))
            cmd_serve(args, root)
            return 0
        out = dispatch(args)
        if isinstance(out, dict):
            text = json.dumps(out, ensure_ascii=False, indent=2 if args.full else None, allow_nan=False)
            print(text)
        else:
            print(compact_output(str(out), full=bool(args.full)))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
