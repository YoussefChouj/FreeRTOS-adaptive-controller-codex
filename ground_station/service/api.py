"""Small JSON API and WebSocket-style publication hub.

The HTTP server intentionally uses only the standard library so the service can
run on the flight-test laptop without adding a web framework dependency.
"""
from __future__ import annotations

import json
import math
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .agent import (
    AgentDisabledError,
    AgentManager,
    PlanBusyError,
    SSE_HEARTBEAT_S,
    build_agent_manager,
)
from .copilot import Copilot
# Largest request body any route accepts.  The biggest real body is a plan or a
# subscribe range list, both far under this; 1 MiB leaves room without letting a
# single POST decide how much the service will read.
_MAX_BODY_BYTES = 1 << 20


def _json_safe(value: Any) -> Any:
    """Replace non-finite floats with ``None``, recursively.

    ``json.dumps`` writes NaN and Infinity as the bare tokens ``NaN`` and
    ``Infinity``.  Python reads those back, so every server-side test passed,
    but they are not JSON and ``JSON.parse`` rejects the whole document.  An
    uninitialised float channel in one telemetry slot therefore made the
    *entire* ``/state`` response unreadable to the browser, and the shell's
    poll threw before it rendered anything -- which is what left the sidebar
    reading "NOT PUBLISHED" with the drone connected and streaming cleanly.

    ``None`` is the honest mapping: the reading exists but has no value.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _validate_request_headers(headers: dict[str, str],
                               bound_host: str | None = None) -> tuple[int, str] | None:
    """Validate Host, Origin, and Content-Type headers for POST/PUT/DELETE
    requests.

    Returns ``None`` if valid, or ``(status_code, message)`` tuple if invalid.

    ``bound_host`` is the host the server is bound to (e.g. ``"192.168.1.10"``).
    If ``bound_host`` is a loopback name it is ignored (no extra host is
    permitted).  When ``bound_host`` is ``"0.0.0.0"`` only loopback names are
    accepted for the Host and Origin headers.
    """
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}

    # If the server was bound to a specific non-loopback address, allow it too.
    if bound_host and bound_host not in (
        "0.0.0.0", "127.0.0.1", "localhost", "::1",
        "::", "[::]",
    ):
        allowed_hosts.add(bound_host)

    # ── Host header validation ───────────────────────────────────────────
    host_header = headers.get("Host")
    if host_header is None:
        return (403, "Missing Host header")
    # Extract hostname from Host header (remove port if present)
    host = host_header
    if host.startswith("["):
        # IPv6 address
        end = host.find("]")
        if end == -1:
            return (403, "Invalid Host header")
        host = host[1:end]
    else:
        # Remove port separator
        colon = host.find(":")
        if colon != -1:
            host = host[:colon]
    if host not in allowed_hosts:
        return (403, f"Host header not allowed: {host}")

    # ── Origin header validation ─────────────────────────────────────────
    origin_header = headers.get("Origin")
    if origin_header is not None:
        # Parse origin: scheme://host[:port]
        if not origin_header.startswith(("http://", "https://")):
            return (403, "Invalid Origin header")
        # Extract the host part
        origin = origin_header.split("://")[1]
        if origin.startswith("["):
            end = origin.find("]")
            if end == -1:
                return (403, "Invalid Origin header")
            origin_host = origin[1:end]
        else:
            colon = origin.find(":")
            if colon != -1:
                origin_host = origin[:colon]
            else:
                origin_host = origin
        if origin_host not in allowed_hosts:
            return (403, f"Origin header not allowed: {origin_host}")

    # ── Content-Type validation ──────────────────────────────────────────
    content_type = headers.get("Content-Type")
    content_length = headers.get("Content-Length")
    has_body = False
    if content_length is not None:
        try:
            if int(content_length) > 0:
                has_body = True
        except ValueError:
            pass
    if has_body:
        if content_type is None:
            return (415, "Missing Content-Type header")
        # Accept any media type whose type part is application/json
        # (e.g. ``application/json; charset=utf-8``).
        ctype = content_type.split(";")[0].strip().lower()
        if ctype != "application/json":
            return (415, f"Invalid Content-Type: {content_type}, "
                                 "expected application/json")

    # If no body, Content-Type can be anything or absent
    return None

# MIME type mapping for static file serving
_MIME_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject",
}


def _mime_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    return _MIME_TYPES.get(ext, "application/octet-stream")


def preset_carriers(symbol: str, *, include_all: bool = False) -> dict:
    """Item 14 — compute, from ``livewatch/multi_slot_presets.yaml`` and
    ``livewatch/manifests.yaml`` (NOT hardcoded), which preset(s) carry a given
    telemetry/sidebar symbol inside some slot's manifest. A symbol matches a
    manifest var by case-insensitive substring (the panel keys like ``pid.gyrox``
    and ``mrac.*`` are colloquial spellings of DWARF paths such as
    ``Ctrler.gyroXPID…``).

    Read-only API for the shell: panels that show a value as "not published"
    can append a hint naming the preset whose slot would publish it, so the
    operator knows which Live-Log preset to record to see the value.
    """
    from ground_station.livewatch.manifest import ManifestStore, MultiSlotPresetManager
    try:
        store = ManifestStore()
        pm = MultiSlotPresetManager()
        names = pm.list_presets()
    except Exception:
        return {"symbol": symbol, "presets": [], "note": "preset/manifest files unavailable"}
    hits = []
    for pname in names:
        try:
            pconf = pm.get(pname, validate_sync_contract=False)
        except Exception:
            continue
        for slot in pconf.get("slots", []):
            mname = slot.get("manifest")
            if not mname:
                continue
            try:
                vars_ = store.get(mname).vars
            except Exception:
                continue
            for var in vars_:
                vlow = var.lower()
                # Match the sidebar/colloquial key against a manifest var.
                # ``mrac.roll.u_ad`` and the DWARF ``mrac_state.roll.u_ad`` are
                # different spellings of the same value, so besides a full
                # substring match we also accept the symbol's last path segment
                # (``u_ad``, ``Theta[0]``, ``gyrox``, …) appearing in the var.
                tail = symbol.rsplit(".", 1)[-1].lower() if symbol else ""
                if (symbol and symbol.lower() in vlow) or (tail and tail in vlow):
                    hits.append({
                        "preset": pname,
                        "slot": slot.get("slot"),
                        "manifest": mname,
                        "hz": slot.get("hz"),
                        "matched_var": var,
                    })
    # Drop duplicate renders of the same (preset, slot, manifest) pair.
    uniq: dict[tuple, dict] = {}
    for h in hits:
        uniq.setdefault((h["preset"], h["slot"], h["manifest"]), h)
    result = list(uniq.values())
    result.sort(key=lambda h: (h["preset"], str(h["slot"])))
    return {"symbol": symbol, "presets": result}


def _recorder_status(service) -> dict[str, Any]:
    """Recorder block for GET /health — path/rows/errors of the CSV writer.

    Uses getattr so a store-less stub passed to the handler in tests cannot
    take the liveness endpoint down.
    """
    recorder = getattr(service, "recorder", None)
    if recorder is None:
        return {"enabled": False, "recording": False,
                "path": None, "rows": 0, "errors": 0}
    return {
        "enabled": bool(getattr(recorder, "enabled", False)),
        # ``recording`` is the current active state; a stopped-but-enabled
        # recorder reports enabled=True, recording=False (honest, not a fake 0).
        "recording": bool(getattr(recorder, "recording", False)),
        "path": str(getattr(recorder, "path", None)) if getattr(recorder, "path", None) else None,
        "rows": int(getattr(recorder, "rows", 0)),
        "errors": int(getattr(recorder, "errors", 0)),
    }


def _slot_status(data: dict, now_ns: int, ttl_ns: int) -> str:
    """Compute per-slot liveness status: live / mixed / stale / dead."""
    last = data.get("last_update_ns") or 0
    if not last:
        return "dead"
    age_ns = now_ns - last
    if age_ns > ttl_ns:
        return "stale"
    key_ts = data.get("_key_ts") or {}
    values = data.get("values") or {}
    fresh_keys = stale_keys = 0
    if isinstance(values, dict) and isinstance(key_ts, dict):
        for k in values:
            kt = key_ts.get(k)
            if kt and (now_ns - kt) < ttl_ns:
                fresh_keys += 1
            else:
                stale_keys += 1
    if fresh_keys == 0:
        return "stale"
    if stale_keys == 0:
        return "live"
    return "mixed"


# Polling interval for command result frames. The MicoAir replies with
# 0x30/0x31/0x32 result frames asynchronously over WiFi; the bridge queues
# them and this thread drains the queue and publishes updated service state.
_GATEWAY_POLL_SEC: float = 0.05   # 20 Hz — fast enough for ACK (few ms) and APPLIED (tens of ms)


def _start_gateway_polling(service, hub, stop_event):
    """Daemon thread: poll gateway for command results and push state to hub.

    Stops when ``stop_event`` is set. Safe to call even when the bridge is
    None (the service was created without a bridge) — in that case the
    function returns immediately.
    """
    if service.gateway is None:
        return  # no bridge, no polling needed
    import time
    while not stop_event.wait(timeout=_GATEWAY_POLL_SEC):
        try:
            result = service.poll_command(timeout=0.0)
            expired = service.expire_commands()
            # poll_command() updates service._last_transaction_result and
            # service._command_results; publish the new snapshot so every
            # browser polling /state picks up the command feedback.
            if result is not None or expired:
                snapshot = service.snapshot()
                # Guard: if snapshot can't be serialised (e.g. bytes in streams),
                # fall back to computing fresh from service internals.
                try:
                    import json as _json
                    _json.dumps(snapshot.__dict__)
                except TypeError:
                    # Snapshot has non-serialisable fields (e.g. bytes from raw
                    # DWARF reads). Compute a clean serialisable dict instead.
                    import time as _time
                    with service._state_lock:
                        streams = {str(k): dict(v) for k, v in service._streams.items()}
                        cmd_results = list(service._command_results)
                    snapshot = {
                        "schema_id": service.schema.schema_id,
                        "session_id": service.session_id,
                        "connected": service._connected,
                        "samples": service._samples,
                        "last_update_ns": service._last_update_ns,
                        "streams": streams,
                        "last_transaction_result": service._last_transaction_result,
                        "command_results": cmd_results,
                    }
                hub.publish(snapshot)
        except Exception:
            pass  # swallow — a bad sample should not kill the polling thread


# Maximum number of recent frames retained in the diagnostics bundle.
# Bounded to keep bundle size predictable.
_DIAGNOSTICS_MAX_FRAMES = 20
_DIAGNOSTICS_MAX_CMDS = 20

# Default page size for record dumps (/sessions/<id>/records, /replay/<id>).
# A flight session holds hundreds of thousands of rows; an uncapped body is
# hundreds of MB and stalls both the shell and any agent reading it.
RECORDS_DEFAULT_LIMIT = 1000

# Self-describing route map for agents (GET /api/routes). Keep in sync with
# the dispatch in do_GET/do_POST; test_http_api_routes_endpoint checks it.
_ROUTE_MAP = {
    "GET": {
        "/health": "service liveness + bridge connection",
        "/health/slots": "per-slot seq/loss/samples",
        "/slots": "active subscribe slots",
        "/state": "full service state snapshot",
        "/sessions": "session list (?limit=N)",
        "/sessions/<id>": "one session",
        "/sessions/<id>/records": "session telemetry records "
                                  "(?limit=N&offset=N, default 1000, limit=0 = all)",
        "/experiments": "active experiment runs (503 without runtime)",
        "/experiments/<name>": "one experiment",
        "/artifacts": "stored artifacts",
        "/analysis/compare": "?a=<sid>&b=<sid>&stream=<int>&key=<key>",
        "/analysis/jitter": "?session_id=<sid>&stream=<int>",
        "/analysis/gaps": "?session_id=<sid>&stream=<int>",
        "/analysis/effective-rate": "?session_id=<sid>&stream=<int>",
        "/api/diagnostics/bundle": "frames, commands, faults for bug reports",
        "/api/recording": "recording state {recording, session_dir, started_at, rows, bytes, reason}",
        "/api/session/notes": "operator notes buffered while not recording",
        "/api/contract": "firmware command/subscribe contract",
        "/api/preset-for-symbol": "preset(s) whose slot manifest carries the symbol (item 14; ?symbol=<key>; read-only, computed from multi_slot_presets.yaml)",
        "/api/view-model": "browser-renderable state for agents; ?stats=1 adds session_stats (full-session scan, slow on long sessions)",
        "/api/events": "event journal",
        "/api/faults": "fault log (rejections, timeouts)",
        "/api/actions": "action journal",
        "/api/symbols": "DWARF symbol names from the firmware ELF "
                        "(?prefix=N&parent=P&limit=N; default/max limit 100/1000)",
        "/api/manifest": "full system capability manifest (symbols, commands, telemetry, panels, routes)",
        "/api/routes": "this map",
        "/api/agent/control": "agent control state {mode, allow_agent_arm, tier0_access, changed_at, changed_by}",
        "/api/agent/actions": "agent action registry {actions: [{name, risk, args_schema, description}]}",
        "/api/agent/plans": "recent plans (last 20)",
        "/api/agent/plans/<id>": "one plan with per-step status/result/error",
        "/api/agent/approvals": "ordered pending approval queue",
        "/api/agent/state": "one agent-facing snapshot {control, ui, recording, layout, arm_state, stream_health, running_plan, pending_approvals, last_messages}",
        "/api/agent/history": "always-on activity journal ?since=&limit=&kind=&source= - {entries: [{seq,t,iso,kind,source,actor,data}]}",
        # Both of these block; test_http_api_routes_endpoint cannot GET them,
        # which is why they sat undeclared -- an agent reading this map would
        # conclude the service had no push channel at all.
        "/api/agent/stream": "server-sent events; the push channel (blocks, "
                             "heartbeat comment every SSE_HEARTBEAT_S)",
        "/api/agent/messages/wait": "long-poll for operator notes "
                                    "?since=&timeout=30 - {notes, last_seq}",
        # NOTE: /api/agent/stream (infinite SSE) and /api/agent/messages/wait
        # (long-poll) are intentionally NOT keys in this GET map: the existing
        # test_service route-smoke test fetches every GET route and would block
        # on either. Their contract is documented in agent.py / AGENT_GUIDE.md.
        "/api/session/notes?since=<seq>": "seq-tagged note log (new notes since seq)",
        "/replay/<id>": "stored records for replay "
                        "(?limit=N&offset=N, default 1000, limit=0 = all)",
    },
    "POST": {
        "/commands": "send a command to the drone (arm-gated)",
        "/subscribe": "program subscribe slots on the drone",
        "/subscribe/preview": "validate a subscribe request, sends nothing",
        "/api/recording/start": "start recording {reason?, requested_by?, label?}",
        "/api/recording/stop": "stop recording",
        "/api/session/note": "append a note {text, kind?, source?}",
        "/experiments": "start an experiment",
        "/experiments/<name>/abort": "abort an experiment",
        "/replay/<id>/play": "push stored telemetry onto the live bus; sends nothing to the drone",
        "/sessions/<id>/export": "export a session to CSV",
        "/api/agent/control": "set control {mode?, allow_agent_arm?, tier0_access?: partial|full, source} (423 while off; 403 if an agent: source sets allow_agent_arm/tier0_access)",
        "/api/agent/plans": "submit a plan {title, goal?, source, steps}, ?queue:true to enqueue (201 / 409 / 400)",
        "/api/agent/plans/<id>/cancel": "cancel a plan",
        "/api/agent/approvals/<plan_id>/<step_id>/approve|reject": "decide one approval {source?}",
        "/api/agent/ui-ack": "browser acks a ui_action {plan_id, step_id, ok, error?}",
        "/api/agent/ui-state": "browser shell reports UI state {active_tab, visible_panels, drawer_open, url}",
        "/api/agent/message": "agent -> operator message {text, source}",
    },
    "ui_testids": {
        "tab-<workspace>": "workspace tab button, e.g. tab-replay",
        "panel-<slug>": "plugin card",
        "session-id": "current session id",
        "replay-play": "Replay tab: Play to Live View button",
    },
    "ui_ids": {
        "plugin-body-<slug>": "plugin content element",
    },
}


# ---- GET /api/symbols — DWARF symbol-name enumeration -------------------
# Bounds for the symbol-name endpoint. The DWARF index is a few hundred base
# variable names; the default keeps an unfiltered body small and the hard cap
# applies no matter what the client asks for.
SYMBOLS_DEFAULT_LIMIT = 100
SYMBOLS_MAX_LIMIT = 1000

# Process-cached own resolver. The bridge may already hold a resolver
# (``_preset_resolver``); reuse it when present to avoid a second open ELF.
_own_symbol_resolver = None
_own_symbol_resolver_error: str | None = None
_drillable_cache: dict[str, bool] = {}


def _service_symbol_resolver(service):
    """Return a live DWARF SymbolResolver for the firmware ELF.

    Reuses ``service.bridge._preset_resolver`` when the bridge has built one,
    otherwise lazily builds and caches a resolver against ``OBJ/JX_FLY.axf``.
    Returns ``(resolver, None)`` or ``(None, error_string)``.
    """
    global _own_symbol_resolver, _own_symbol_resolver_error
    bridge = getattr(service, "bridge", None)
    resolver = getattr(bridge, "_preset_resolver", None) if bridge is not None else None
    if resolver is not None:
        return resolver, None
    if _own_symbol_resolver is not None:
        return _own_symbol_resolver, None
    if _own_symbol_resolver_error is None:
        try:
            elf_path = Path(__file__).parents[2] / "OBJ" / "JX_FLY.axf"
            if not elf_path.exists():
                raise FileNotFoundError(f"firmware ELF not found at {elf_path}")
            from ground_station.livewatch.symbols import SymbolResolver
            _own_symbol_resolver = SymbolResolver(str(elf_path))
        except Exception as exc:  # surfaced verbatim in the 503 payload
            _own_symbol_resolver_error = str(exc)
    if _own_symbol_resolver is not None:
        return _own_symbol_resolver, None
    return None, _own_symbol_resolver_error


def _is_drillable(resolver, full_name: str) -> bool:
    """True when a path has browsable struct members or array elements."""
    cached = _drillable_cache.get(full_name)
    if cached is not None:
        return cached
    try:
        drillable = bool(resolver.fields_of(full_name))
    except Exception:
        drillable = False
    _drillable_cache[full_name] = drillable
    return drillable


def _stale_catalog_fallback(prefix: str, limit: int) -> tuple[int, dict]:
    """Last-resort name list from the stale offline JSON catalog.

    Names only (never addresses), flagged plainly so the UI can warn that the
    list may not match the running build. Drill-down is unsupported here.
    """
    json_path = Path(__file__).parents[1] / "livewatch" / "symbol_catalog.json"
    if not json_path.exists():
        return 503, {"error": "symbol resolver and offline catalog unavailable",
                     "source": "unavailable", "prefix": prefix, "parent": None}
    try:
        from ground_station.livewatch.catalog import SymbolCatalog
        catalog = SymbolCatalog.load(json_path)
    except Exception as exc:
        return 503, {"error": f"symbol resolver unavailable ({exc})",
                     "source": "unavailable", "prefix": prefix, "parent": None}
    names = catalog.names()
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    total = len(names)
    page = names[:limit]
    return 200, {
        "source": "stale_json_catalog",
        "generated_at": catalog.generated_at,
        "warning": f"names from offline catalog generated {catalog.generated_at}; "
                   "may not match the running build",
        "prefix": prefix,
        "parent": None,
        "parent_kind": None,
        "names": page,
        "count": len(page),
        "total": total,
        "limit": limit,
        "truncated": total > len(page),
        "drillable": {},
    }


def _symbols_payload(service, prefix: str, parent: str,
                     limit: int) -> tuple[int, dict]:
    """Build the /api/symbols response. Returns ``(http_status, payload)``.

    Root mode (no ``parent``): resolvable base variable names, optionally
    prefix-filtered. Parent mode: immediate members/elements of one path as
    full names, with a per-name ``drillable`` map for nested browsing.
    Names only — addresses are intentionally never returned.
    """
    try:
        limit = max(1, min(int(limit), SYMBOLS_MAX_LIMIT))
    except (TypeError, ValueError):
        limit = SYMBOLS_DEFAULT_LIMIT

    resolver, error = _service_symbol_resolver(service)
    if resolver is None:
        if parent:
            return 503, {"error": f"symbol resolver unavailable: {error}",
                         "source": "unavailable", "prefix": None, "parent": parent}
        return _stale_catalog_fallback(prefix, limit)

    try:
        if parent:
            children = resolver.fields_of(parent)
            if children and children[0].startswith("["):
                kind = "array"
                full_names = [f"{parent}{c}" for c in children]
            else:
                kind = "struct"
                full_names = [f"{parent}.{c}" for c in children]
            total = len(full_names)
            page = full_names[:limit]
            payload = {
                "source": "dwarf",
                "generated_at": None,
                "warning": None,
                "prefix": None,
                "parent": parent,
                "parent_kind": kind,
                "names": page,
                "count": len(page),
                "total": total,
                "limit": limit,
                "truncated": total > len(page),
                "drillable": {n: _is_drillable(resolver, n) for n in page},
            }
            return 200, payload

        names = resolver.names()
        if prefix:
            names = [n for n in names if n.startswith(prefix)]
        total = len(names)
        page = names[:limit]
        return 200, {
            "source": "dwarf",
            "generated_at": None,
            "warning": None,
            "prefix": prefix,
            "parent": None,
            "parent_kind": None,
            "names": page,
            "count": len(page),
            "total": total,
            "limit": limit,
            "truncated": total > len(page),
            "drillable": {n: _is_drillable(resolver, n) for n in page},
        }
    except KeyError as exc:
        return 404, {"error": f"unknown symbol path {exc}", "source": "dwarf",
                     "prefix": None, "parent": parent}
    except (ValueError, TypeError) as exc:
        return 400, {"error": str(exc), "source": "dwarf",
                     "prefix": None, "parent": parent}


def _build_diagnostics_bundle(service) -> dict:
    """Build a deterministic diagnostics bundle for the current session.

    Collects everything needed to reproduce this session's state without
    manually navigating the dashboard or reconnecting the drone.

    Sections that cannot yet be collected are marked with explicit
    ``unavailable`` values so agents know the boundary without guessing.

    See IMPROVEMENT_SPEC_2026-09-18.md §WP0 for the full contract.
    """
    import sys
    import os

    bundle: dict[str, Any] = {
        "collected_at_ns": time.time_ns(),
        "evidence_status": "unverified",  # upgraded to source_confirmed/live_observed after live capture
    }

    # ── 1. Process provenance ─────────────────────────────────────────────
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        bundle["process"] = {
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "cwd": os.getcwd(),
            "python_version": sys.version,
            "argv": sys.argv,
            "rusage_maxrss_kb": int(rusage.ru_maxrss),
            "unavailable": [],
        }
    except Exception as e:
        bundle["process"] = {"error": str(e), "unavailable": []}

    # ── 2. Git provenance ─────────────────────────────────────────────────
    git_info: dict[str, Any] = {
        "source_confirmed": "unverified",
        "head": None,
        "dirty": None,
        "branch": None,
        "unavailable": [],
    }
    try:
        import subprocess
        repo_root = str(Path(__file__).parents[2])
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        if head.returncode == 0:
            git_info["head"] = head.stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        if branch.returncode == 0:
            git_info["branch"] = branch.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        if dirty.returncode == 0:
            git_info["dirty"] = bool(dirty.stdout.strip())
        git_info["source_confirmed"] = "source_confirmed"
    except Exception as e:
        git_info["error"] = str(e)
        git_info["unavailable"].append(f"git: {e}")
    bundle["git"] = git_info

    # ── 3. Firmware / ELF identity ──────────────────────────────────────
    elf_info: dict[str, Any] = {
        "path": None,
        "size_bytes": None,
        "sha256": None,
        "schema_id": None,
        "unavailable": [],
    }
    try:
        elf_path = Path(__file__).parents[2] / "OBJ" / "JX_FLY.axf"
        if elf_path.exists():
            elf_info["path"] = str(elf_path)
            elf_info["size_bytes"] = elf_path.stat().st_size
            import hashlib
            sha = hashlib.sha256()
            with elf_path.open("rb") as f:
                sha.update(f.read(64 * 1024))  # hash header + DWARF for speed
            elf_info["sha256"] = sha.hexdigest()[:16]  # truncated — not a secret
        else:
            elf_info["unavailable"].append("ELF not found at expected path")
    except Exception as e:
        elf_info["error"] = str(e)
        elf_info["unavailable"].append(f"elf: {e}")
    bundle["firmware"] = elf_info

    # ── 4. Service identity ───────────────────────────────────────────────
    try:
        snap = service.snapshot()
        bundle["service"] = {
            "schema_id": service.schema.schema_id,
            "telemetry_schema_id": getattr(snap, "telemetry_schema_id", None),
            "adapter_version": getattr(snap, "adapter_version", None),
            "slot_freshness_ttl_ns": getattr(snap, "slot_freshness_ttl_ns", None),
            "session_id": getattr(snap, "session_id", None),
            "connected": getattr(snap, "connected", False),
            "samples": getattr(snap, "samples", 0),
            "last_update_ns": getattr(snap, "last_update_ns", None),
            "unavailable": [],
        }
        # Cross-reference: the firmware section carries the schema ID the
        # service's adapter resolved, so agents can compare registry identity
        # (host) against what the firmware's subscribe request echoed.
        telemetry_schema_id = getattr(snap, "telemetry_schema_id", None)
        if telemetry_schema_id and bundle.get("firmware", {}).get("schema_id") is None:
            bundle["firmware"]["schema_id"] = telemetry_schema_id
    except Exception as e:
        bundle["service"] = {"error": str(e), "unavailable": []}

    # ── 5. Transport state ────────────────────────────────────────────────
    transport: dict[str, Any] = {
        "source_confirmed": "unverified",
        "bridge_available": service.bridge is not None,
        "wifi_connected": getattr(service.bridge, "_wifi_connected", None),
        "active_slots": [],
        "slot_states": {},
        "slot_schema_identity": {},
        "request_states": {},
        "last_error": None,
        "unavailable": [],
    }
    try:
        if service.bridge is not None:
            bridge = service.bridge
            # Expose active slot state from the bridge's stream stats.
            # _stream_stats is keyed by slot, updated by the RX thread.
            stats = getattr(bridge, "_stream_stats", {})
            pending = getattr(bridge, "_pending_schema_ranges", {})
            slot_states = getattr(bridge, "_slot_states", {})
            request_states = getattr(bridge, "_request_states", {})
            request_to_slot = getattr(bridge, "_request_to_slot", {})
            for slot_key, stat in stats.items():
                transport["active_slots"].append({
                    "slot": slot_key,
                    "received": stat.get("received", 0),
                    "dropped": stat.get("dropped", 0),
                    "loss_pct": stat.get("loss_pct", 0.0),
                    "last_seq": stat.get("last_seq", None),
                    "crc_errors": stat.get("crc_errors", 0),
                    "last_update_ns": stat.get("last_update_ns", None),
                    "state": slot_states.get(slot_key, "planned"),
                    # source_time_ms: the firmware's per-frame clock from the
                    # wire 0x09 frame. The typed subscribe path carries it;
                    # sidebar paths have no wire clock. Pull from the service's
                    # streams snapshot (where it is populated by the adapter
                    # from MultiStreamDecoder.feed).
                    "source_time_ms": None,
                })
            # Fill source_time_ms from the service snapshot where the
            # typed decoder populates it per-sample.
            snap = service.snapshot()
            for slot_key, data in (getattr(snap, "streams", {}) or {}).items():
                for active in transport["active_slots"]:
                    if str(active["slot"]) == str(slot_key):
                        active["source_time_ms"] = data.get("source_time_ms", 0) if isinstance(data, dict) else None
            for slot_key, ranges in pending.items():
                if slot_key not in slot_states:
                    transport["slot_states"][str(slot_key)] = {
                        "state": "planned",
                        "range_count": len(ranges) if ranges else 0,
                    }
            for slot_key, state in slot_states.items():
                if str(slot_key) not in transport["slot_states"]:
                    transport["slot_states"][str(slot_key)] = {"state": state}
            for rid, state in request_states.items():
                meta = getattr(bridge, "_request_metadata", {}).get(rid, {})
                transport["request_states"][str(rid)] = {
                    "state": state,
                    "slot": request_to_slot.get(rid),
                    # WP1 full request metadata
                    "created_ns": meta.get("created_ns"),
                    "sent_ns": meta.get("sent_ns"),
                    "response_ns": meta.get("response_ns"),
                    "timeout_ns": meta.get("timeout_ns"),
                    "retry_count": meta.get("retry_count", 0),
                    "failure_reason": meta.get("failure_reason"),
                    "batch_index": meta.get("batch_index"),
                    "total_batches": meta.get("total_batches"),
                    "range_count": meta.get("range_count"),
                    "divider": meta.get("divider"),
                    "transport": meta.get("transport"),
                }
            for slot_key, ranges in pending.items():
                transport["request_states"][f"pending:{slot_key}"] = {
                    "state": "planned",
                    "range_count": len(ranges) if ranges else 0,
                }
            # WP1: expose per-slot schema identity fingerprints so agents can
            # detect when the firmware restarted or was reflashed.
            # Schema identity is (total_bytes, frozenset of (address, size) pairs).
            schema_identity = getattr(bridge, "_slot_schema_identity", {})
            for slot_key, identity in schema_identity.items():
                if identity:
                    transport["slot_schema_identity"][str(slot_key)] = {
                        "total_bytes": identity[0],
                        "range_count": len(identity[1]),
                        # frozenset is not JSON-serialisable; convert to sorted list of (addr, size)
                        "ranges": sorted(list(identity[1])),
                    }
                else:
                    transport["slot_schema_identity"][str(slot_key)] = None
            transport["source_confirmed"] = "live_observed"
    except Exception as e:
        transport["last_error"] = str(e)
        transport["unavailable"].append(f"transport: {e}")
    bundle["transport"] = transport

    # ── 6. Recent frames (metadata only, bounded) ────────────────────────
    frames: dict[str, Any] = {
        "source_confirmed": "unverified",
        "requests": [],
        "responses": [],
        "total_request_frames": 0,
        "total_response_frames": 0,
        "unavailable": [],
    }
    try:
        if service.bridge is not None:
            bridge = service.bridge
            # Collect recent request/response metadata from the bridge's
            # instrumentation (set by _request_slot0_schema and _handle_*_frame).
            # These are bounded lists kept by the bridge; if not yet implemented,
            # the unavailable list tells agents to instrument the bridge.
            recent_requests = getattr(bridge, "_recent_requests", [])
            recent_responses = getattr(bridge, "_recent_responses", [])
            # Keep only the last N, truncated to payload_max_bytes each.
            payload_max = 64  # bytes — enough for header + start of payload
            for req in list(recent_requests)[-_DIAGNOSTICS_MAX_FRAMES:]:
                if isinstance(req, dict):
                    frames["requests"].append({
                        "slot": req.get("slot"),
                        "divider": req.get("divider"),
                        "transport": req.get("transport"),
                        "range_count": req.get("range_count"),
                        "batch_index": req.get("batch_index"),
                        "total_batches": req.get("total_batches"),
                        "request_bytes": req.get("request_bytes", "")[:payload_max * 2],
                        "frame_size_bytes": req.get("frame_size_bytes"),
                        "timestamp_ns": req.get("timestamp_ns"),
                    })
            for resp in list(recent_responses)[-_DIAGNOSTICS_MAX_FRAMES:]:
                if isinstance(resp, dict):
                    frames["responses"].append({
                        "frame_type": resp.get("frame_type"),
                        "slot": resp.get("slot"),
                        "total_bytes": resp.get("total_bytes"),
                        "range_count": resp.get("range_count"),
                        "timestamp_ns": resp.get("timestamp_ns"),
                    })
            frames["total_request_frames"] = len(recent_requests)
            frames["total_response_frames"] = len(recent_responses)
            if frames["requests"] or frames["responses"]:
                frames["source_confirmed"] = "live_observed"
    except Exception as e:
        frames["unavailable"].append(f"frames: {e}")
    bundle["frames"] = frames

    # ── 7. Decoded samples + raw frames ───────────────────────────────
    samples: dict[str, Any] = {
        "source_confirmed": "unverified",
        "streams": {},
        "raw_frames_received": 0,
        "raw_frames_sent": 0,
        "unavailable": [],
    }
    try:
        snap = service.snapshot()
        for slot_key, data in (getattr(snap, "streams", {}) or {}).items():
            if not isinstance(data, dict):
                continue
            samples["streams"][str(slot_key)] = {
                "tag": data.get("tag"),
                "received": data.get("received", 0),
                "dropped": data.get("dropped", 0),
                "loss_pct": data.get("loss_pct", 0.0),
                "sequence": data.get("sequence", 0),
                "source_time_ms": data.get("source_time_ms", 0),
                "last_update_ns": data.get("last_update_ns", None),
                "crc_errors": data.get("crc_errors", 0),
                "var_count": len(data.get("values", {}) or {}),
            }
        if samples["streams"]:
            samples["source_confirmed"] = "live_observed"
        # S6: count raw frames stored in this session
        if service.session_id:
            rx_count = service.store._db.execute(
                "SELECT COUNT(*) FROM raw_frames WHERE session_id=? AND direction='rx'",
                (service.session_id,),
            ).fetchone()[0]
            tx_count = service.store._db.execute(
                "SELECT COUNT(*) FROM raw_frames WHERE session_id=? AND direction='tx'",
                (service.session_id,),
            ).fetchone()[0]
            samples["raw_frames_received"] = rx_count or 0
            samples["raw_frames_sent"] = tx_count or 0
    except Exception as e:
        samples["unavailable"].append(str(e))
    bundle["samples"] = samples

    # ── 8. Command / action history ──────────────────────────────────────
    commands: dict[str, Any] = {
        "source_confirmed": "unverified",
        "results": [],
        "unavailable": [],
    }
    try:
        cmd_results = list(service._command_results)[-_DIAGNOSTICS_MAX_CMDS:]
        for r in cmd_results:
            if isinstance(r, dict):
                commands["results"].append({
                    "transaction_id": r.get("transaction_id"),
                    "command_id": r.get("command_id"),
                    "index": r.get("index"),
                    "status": r.get("status"),
                    "reason": r.get("reason"),
                    "time_ns": r.get("time_ns"),
                })
        if commands["results"]:
            commands["source_confirmed"] = "live_observed"
    except Exception as e:
        commands["unavailable"].append(str(e))
    bundle["commands"] = commands

    # ── 9. Browser / UI state ────────────────────────────────────────────────
    # The service never drives a browser, so it cannot capture screenshots or
    # console errors. Capture those client-side (e.g. with Playwright).
    # The view-model IS accessible at GET /api/view-model — the browser section
    # references it for agent use.
    bundle["browser"] = {
        "source_confirmed": "unverified",
        "screenshot": "unavailable server-side — capture client-side (Playwright)",
        "console_errors": "unavailable server-side — capture client-side (Playwright)",
        "view_model_endpoint": "GET /api/view-model",
        "view_model_note": "/api/view-model is implemented and live — "
                           "agents use it directly; no browser harness needed",
        "unavailable": ["screenshot", "console_errors"],
    }

    # ── 10. Evidence ledger summary ────────────────────────────────────────
    # Aggregate evidence status from each section.
    sections = ["git", "firmware", "service", "transport", "frames", "samples", "commands", "browser"]
    confirmed = [s for s in sections if bundle.get(s, {}).get("source_confirmed") == "source_confirmed"]
    live = [s for s in sections if bundle.get(s, {}).get("source_confirmed") == "live_observed"]
    bundle["evidence_summary"] = {
        "source_confirmed": confirmed,
        "live_observed": live,
        "unverified": [s for s in sections if bundle.get(s, {}).get("source_confirmed") in (None, "unverified", "unavailable")],
    }

    return bundle


class StateHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Any] = []
        self._latest_state: dict | None = None

    def subscribe(self, callback) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, state) -> None:
        payload = state if isinstance(state, dict) else state.__dict__
        with self._lock:
            self._latest_state = payload
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            callback(payload)

    def latest_state(self) -> dict | None:
        """Return the most recently published state, or None before the first publish."""
        with self._lock:
            return self._latest_state


def make_handler(service, hub: StateHub | None = None, static_root: Path | None = None,
                 experiment_runtime=None, agent: AgentManager | None = None,
                 copilot: Copilot | None = None):
    hub = hub or StateHub()
    _AGENT = agent  # captured; may be None in legacy tests
    _COPILOT_SOURCE = "agent:copilot"  # shared constant so agent can check it

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            # allow_nan=False turns a missed non-finite value into a loud
            # failure here rather than a silent JSON.parse error in the
            # browser; _json_safe is what makes that never fire.
            body = json.dumps(_json_safe(payload), sort_keys=True,
                              default=str, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def _paging(self, default_limit: int) -> tuple[int | None, int]:
            """Return (limit, offset) from the query string.

            ``limit=0`` means unlimited; a missing or unparsable limit uses
            ``default_limit``.
            """
            from urllib.parse import parse_qs, urlsplit
            qs = parse_qs(urlsplit(self.path).query)
            try:
                limit = int(qs.get("limit", [default_limit])[0])
            except (TypeError, ValueError):
                limit = default_limit
            try:
                offset = max(0, int(qs.get("offset", ["0"])[0]))
            except (TypeError, ValueError):
                offset = 0
            return (None if limit <= 0 else limit), offset

        def _static(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            from urllib.parse import parse_qs, urlsplit
            route = urlsplit(self.path).path

            if route == "/health":
                snap = service.snapshot()
                streams = getattr(snap, "streams", {}) or {}
                health = {
                    "ok": True,
                    "schema_id": service.schema.schema_id,
                    "started_commit": getattr(service, "started_commit", None),
                    "started_at": getattr(service, "started_at", None),
                    "bridge_available": service.bridge is not None,
                    "connected": getattr(snap, "connected", False),
                    "samples": getattr(snap, "samples", 0),
                    "last_update_ns": getattr(snap, "last_update_ns", None),
                    "active_streams": len(streams),
                    "session_id": getattr(snap, "session_id", None),
                    "recorder": _recorder_status(service),
                }
                self._json(200, health)
            elif route == "/health/slots":
                # Per-slot liveness report. Tells the operator which slots
                # are flowing, at what rate, with how many fresh keys, and
                # which haven't been seen recently (stale -> will be
                # evicted by snapshot()'s TTL pass).
                snap = service.snapshot()
                streams = getattr(snap, "streams", {}) or {}
                now_ns = time.time_ns()
                ttl_ns = getattr(service, "_slot_freshness_ttl_ns", 30 * 10**9)
                report = {"now_ns": now_ns, "ttl_ns": ttl_ns, "slots": {}}
                for slot_key, data in streams.items():
                    if not isinstance(data, dict):
                        continue
                    last = data.get("last_update_ns") or 0
                    age_ns = (now_ns - last) if last else None
                    fresh_keys = 0
                    stale_keys = 0
                    key_ts = data.get("_key_ts") or {}
                    values = data.get("values") or {}
                    if isinstance(values, dict):
                        for k in values:
                            kt = key_ts.get(k) if isinstance(key_ts, dict) else None
                            if kt and (now_ns - kt) < ttl_ns:
                                fresh_keys += 1
                            else:
                                stale_keys += 1
                    slot_states = getattr(service.bridge, "_slot_states", {}) if service.bridge else {}
                    stale = age_ns is None or age_ns > ttl_ns
                    # Derived status: the "liveness probe" pattern from
                    # PLANNING_PROMPT §8 Pattern 2. "live" = at least one
                    # fresh key, "mixed" = some fresh + some stale,
                    # "stale" = no fresh keys, "dead" = no data ever.
                    # Downstream plugins can render a single status
                    # badge per slot row without computing the rule
                    # themselves.
                    if not last:
                        status = "dead"
                    elif fresh_keys == 0:
                        status = "stale"
                    elif stale_keys == 0:
                        status = "live"
                    else:
                        status = "mixed"
                    report["slots"][slot_key] = {
                        "tag":              data.get("tag"),
                        "last_update_ns":   last,
                        "age_ns":           age_ns,
                        "stale":            stale,
                        "status":           status,
                        "received":         data.get("received", 0),
                        "dropped":          data.get("dropped", 0),
                        "loss_pct":         data.get("loss_pct", 0.0),
                        "key_count":        len(values) if isinstance(values, dict) else 0,
                        "fresh_keys":       fresh_keys,
                        "stale_keys":       stale_keys,
                        "request_state":    slot_states.get(slot_key, "planned"),
                    }
                # Top-level stream health. This is the signal the Diagnostics
                # tab needs: it survives per-slot TTL eviction. Once a slot is
                # older than the TTL the snapshot() pass evicts it, so
                # ``slots`` alone degrades to {} the same whether nothing was
                # ever subscribed OR the link silently stalled -- the two
                # cases are indistinguishable without a session-lifetime
                # counter. ``_last_update_ns`` is set on every ingest and is
                # never evicted, so a stall shows up here even minutes after
                # the last frame (when every slot has already been dropped
                # from ``slots``). None means no frame has EVER arrived this
                # session (honest "not published", not 0).
                last_frame_ns = getattr(service, "_last_update_ns", None)
                if last_frame_ns:
                    last_frame_age_ns = now_ns - last_frame_ns
                    report["stream_health"] = {
                        "telemetry_seen": True,
                        "last_frame_age_ns": last_frame_age_ns,
                        "stalled": last_frame_age_ns > ttl_ns,
                    }
                else:
                    report["stream_health"] = {
                        "telemetry_seen": False,
                        "last_frame_age_ns": None,
                        "stalled": False,
                    }
                self._json(200, report)
            elif route == "/api/recording":
                self._json(200, service.recording_status())
            elif route == "/api/session/notes":
                # Optional ?since=<seq> returns the seq-tagged note log that
                # agents use (each note gains a monotonic seq). Without since,
                # keep the legacy buffered-notes shape (back-compat).
                qs = parse_qs(urlsplit(self.path).query)
                since = qs.get("since")
                if since:
                    seq = int(since[0] or 0)
                    if _AGENT is not None:
                        self._json(200, {
                            "notes": _AGENT.notes_since(seq),
                            "last_seq": _AGENT.notes.last_seq,
                            "recording": bool(getattr(
                                service.recorder, "recording", False)),
                        })
                        return
                self._json(200, {"notes": service.list_session_notes(),
                                 "recording": bool(getattr(
                                     service.recorder, "recording", False))})
            elif route == "/api/preset-for-symbol":
                qs = parse_qs(urlsplit(self.path).query)
                symbol = (qs.get("symbol") or [""])[0].strip()
                self._json(200, preset_carriers(symbol or "mrac.roll.u_ad"))
            elif route == "/slots":
                # Slot inventory — keys in service._streams plus what the bridge
                # currently knows about (auto-subscribed + manually subscribed).
                slots = []
                snap = service.snapshot()
                for slot_key, data in (getattr(snap, "streams", {}) or {}).items():
                    slots.append({
                        "slot": slot_key,
                        "tag": data.get("tag") if isinstance(data, dict) else None,
                        "sequence": data.get("sequence") if isinstance(data, dict) else None,
                        "received": data.get("received", 0) if isinstance(data, dict) else 0,
                        "dropped": data.get("dropped", 0) if isinstance(data, dict) else 0,
                        "loss_pct": data.get("loss_pct", 0.0) if isinstance(data, dict) else 0.0,
                        "var_count": len(data.get("values", {}) or {}) if isinstance(data, dict) else 0,
                        "last_update_ns": data.get("last_update_ns") if isinstance(data, dict) else None,
                    })
                # ``subscribable`` mirrors SUBSCRIBE_MAX_SLOTS = 4
                # (API/subscribe.h). Slots 9..12 were a host-side design
                # idea; the firmware rejects them with "E:bad slot". See
                # the slot-manager-panel comment for the matching fix.
                self._json(200, {"slots": slots, "subscribable": [0, 1, 2, 3]})
            elif route == "/state":
                # Bug 1 (AUDIT_2026-09-21 §Bug 1): this route used to prefer
                # hub.latest_state(), but the hub was only ever written by the
                # gateway result-polling thread (command results / expiries).
                # Normal telemetry ingest never published, so the first
                # command froze /state on that one cached snapshot forever:
                # the browser page stopped updating while /health — computed
                # live from the service — kept reporting rising samples.
                # Serve a fresh snapshot on every request; snapshot() is cheap
                # and the /health handler already computes one per poll.
                self._json(200, service.snapshot().__dict__)
            # GET /sessions — list all sessions
            elif route == "/sessions":
                try:
                    rows = service.store._db.execute(
                        "SELECT id,started_ns,ended_ns,schema_id,source FROM sessions"
                    ).fetchall()
                    self._json(200, [{"id": r[0], "started_ns": r[1],
                                      "ended_ns": r[2], "schema_id": r[3],
                                      "source": r[4]} for r in rows])
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /sessions/<id> — session detail
            elif route.startswith("/sessions/") and "/records" not in route:
                parts = route.split("/")
                session_id = parts[2] if len(parts) >= 3 else None
                if not session_id:
                    self._json(404, {"error": "missing session id"})
                    return
                try:
                    detail = service.store.session(session_id)
                    self._json(200, detail)
                except KeyError:
                    self._json(404, {"error": "session not found"})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /sessions/<id>/records — records for a session, paged
            elif route.startswith("/sessions/") and route.endswith("/records"):
                parts = route.split("/")
                session_id = parts[2] if len(parts) >= 3 else None
                if not session_id:
                    self._json(404, {"error": "missing session id"})
                    return
                try:
                    limit, offset = self._paging(RECORDS_DEFAULT_LIMIT)
                    records = list(service.store.iter_records(
                        session_id, limit=limit, offset=offset))
                    self._json(200, {
                        "session_id": session_id,
                        "records": records,
                        "count": len(records),
                        "offset": offset,
                        "limit": limit,
                        "truncated": limit is not None and len(records) == limit,
                    })
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /experiments — list active experiment runs
            elif route == "/experiments":
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                active = experiment_runtime.active
                # Only return runs that are actively settling/measuring
                if active is None or active.state.value in ("complete", "aborted"):
                    self._json(200, [])
                    return
                self._json(200, [{
                    "name": active.name,
                    "state": active.state.value,
                    "tick": active.tick,
                    "samples": len(active.samples),
                }])
            # GET /experiments/<name> — experiment detail
            elif route.startswith("/experiments/"):
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                parts = route.split("/")
                name = parts[2] if len(parts) >= 3 else None
                active = experiment_runtime.active
                if active is None or active.name != name:
                    self._json(404, {"error": "experiment not found"})
                    return
                self._json(200, {
                    "name": active.name,
                    "state": active.state.value,
                    "tick": active.tick,
                    "settle_ticks": active.settle_ticks,
                    "measure_ticks": active.measure_ticks,
                    "samples": active.samples,
                    "events": [(e.tick, e.name, e.detail) for e in active.events],
                    "parameters_before": active.parameters_before,
                    "parameters_after": active.parameters_after,
                })
            # GET /artifacts — indexed artifacts
            elif route == "/artifacts":
                from ground_station.analysis.artifacts import index_artifacts
                try:
                    artifacts = index_artifacts()
                    self._json(200, {"artifacts": artifacts})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /analysis/compare — compare two sessions
            elif route.startswith("/analysis/compare"):
                from ground_station.analysis.session import compare_sessions
                try:
                    qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
                    a = qs.get("a", [None])[0]
                    b = qs.get("b", [None])[0]
                    stream_str = qs.get("stream", [None])[0]
                    key = qs.get("key", [None])[0]
                    if not a or not b or not stream_str or not key:
                        self._json(400, {"error": "missing a, b, stream, or key query params"})
                        return
                    try:
                        stream_id = int(stream_str)
                    except ValueError:
                        self._json(400, {"error": "stream must be an integer stream id"})
                        return
                    result = compare_sessions(service.store, a, b, stream_id, key)
                    self._json(200, result)
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /analysis/jitter?session_id=...&stream=<int> — jitter analysis
            elif route.startswith("/analysis/jitter"):
                from ground_station.analysis.session import compute_jitter
                try:
                    qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
                    sid = qs.get("session_id", [None])[0]
                    stream_str = qs.get("stream", [None])[0]
                    if not sid:
                        self._json(400, {"error": "missing session_id"})
                        return
                    stream_id = int(stream_str) if stream_str else None
                    result = compute_jitter(service.store, sid, stream_id)
                    self._json(200, result)
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /analysis/gaps?session_id=...&stream=<int> — gap analysis
            elif route.startswith("/analysis/gaps"):
                from ground_station.analysis.session import compute_gaps
                try:
                    qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
                    sid = qs.get("session_id", [None])[0]
                    stream_str = qs.get("stream", [None])[0]
                    if not sid:
                        self._json(400, {"error": "missing session_id"})
                        return
                    stream_id = int(stream_str) if stream_str else None
                    result = compute_gaps(service.store, sid, stream_id)
                    self._json(200, result)
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /analysis/effective-rate?session_id=...&stream=<int> — source clock rate
            elif route.startswith("/analysis/effective-rate"):
                from ground_station.analysis.session import compute_effective_rate
                try:
                    qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
                    sid = qs.get("session_id", [None])[0]
                    stream_str = qs.get("stream", [None])[0]
                    if not sid:
                        self._json(400, {"error": "missing session_id"})
                        return
                    stream_id = int(stream_str) if stream_str else None
                    result = compute_effective_rate(service.store, sid, stream_id)
                    self._json(200, result)
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            elif route == "/api/diagnostics/bundle":
                # GET /api/diagnostics/bundle — evidence and provenance snapshot
                bundle = _build_diagnostics_bundle(service)
                self._json(200, bundle)
            # GET /api/contract — firmware contract manifest (WP1)
            elif route == "/api/contract":
                from ground_station.platform.firmware_contract import current as contract
                self._json(200, contract.to_dict())
            # GET /api/symbols — DWARF symbol names for the slot picker.
            # Pure read: static analysis of the firmware ELF, sends nothing.
            elif route == "/api/symbols":
                qs = parse_qs(urlsplit(self.path).query)
                prefix = qs.get("prefix", [""])[0]
                parent = qs.get("parent", [""])[0]
                status, payload = _symbols_payload(
                    service, prefix, parent,
                    qs.get("limit", [SYMBOLS_DEFAULT_LIMIT])[0],
                )
                self._json(status, payload)
            # GET /api/manifest — full system capability manifest
            elif route == "/api/manifest":
                manifest_path = Path(__file__).parents[2] / "docs" / "dashboard-platform" / "capability_manifest.json"
                if manifest_path.exists():
                    try:
                        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                        self._json(200, payload)
                    except Exception as exc:
                        self._json(500, {"error": f"failed to load capability manifest: {exc}"})
                else:
                    try:
                        from ground_station.platform.capability_manifest import generate_manifest
                        payload = generate_manifest()
                        self._json(200, payload)
                    except Exception as exc:
                        self._json(503, {"error": f"capability manifest unavailable: {exc}"})
            # GET /api/routes — route + UI selector map for agents
            elif route == "/api/routes":
                self._json(200, _ROUTE_MAP)
            # GET /api/view-model — browser-renderable state snapshot for agents
            elif route == "/api/view-model":
                snap = service.snapshot()
                now_ns = time.time_ns()
                ttl_ns = getattr(service, "_slot_freshness_ttl_ns", 30 * 10**9)
                # Build per-slot freshness detail.
                slot_freshness = {}
                for slot_key, data in (getattr(snap, "streams", {}) or {}).items():
                    if not isinstance(data, dict):
                        continue
                    last = data.get("last_update_ns") or 0
                    age_ns = (now_ns - last) if last else None
                    key_ts = data.get("_key_ts") or {}
                    values = data.get("values") or {}
                    fresh_keys = 0
                    stale_keys = 0
                    unknown_keys = 0
                    if isinstance(values, dict) and isinstance(key_ts, dict):
                        for k in values:
                            kt = key_ts.get(k)
                            if kt is None:
                                unknown_keys += 1
                            elif (now_ns - kt) < ttl_ns:
                                fresh_keys += 1
                            else:
                                stale_keys += 1
                    slot_freshness[str(slot_key)] = {
                        "tag": data.get("tag"),
                        "status": _slot_status(data, now_ns, ttl_ns),
                        "age_ns": age_ns,
                        "stale": age_ns is not None and age_ns > ttl_ns,
                        "key_count": len(values) if isinstance(values, dict) else 0,
                        "fresh_keys": fresh_keys,
                        "stale_keys": stale_keys,
                        "unknown_keys": unknown_keys,
                        "received": data.get("received", 0),
                        "dropped": data.get("dropped", 0),
                        "loss_pct": data.get("loss_pct", 0.0),
                        "crc_errors": data.get("crc_errors", 0),
                        "sequence": data.get("sequence"),
                        "source_time_ms": data.get("source_time_ms"),
                        "last_update_ns": last,
                    }
                # Request states from bridge.
                request_states = {}
                if service.bridge is not None:
                    for rid, state in getattr(service.bridge, "_request_states", {}).items():
                        meta = getattr(service.bridge, "_request_metadata", {}).get(rid, {})
                        request_states[str(rid)] = {
                            "state": state,
                            "slot": getattr(service.bridge, "_request_to_slot", {}).get(rid),
                            # WP1 full request metadata
                            "created_ns": meta.get("created_ns"),
                            "sent_ns": meta.get("sent_ns"),
                            "response_ns": meta.get("response_ns"),
                            "retry_count": meta.get("retry_count", 0),
                            "failure_reason": meta.get("failure_reason"),
                            "batch_index": meta.get("batch_index"),
                            "total_batches": meta.get("total_batches"),
                            "range_count": meta.get("range_count"),
                            "divider": meta.get("divider"),
                        }
                # S6: session telemetry stats (jitter + effective rate) if session exists
                session_stats = {}
                qs = parse_qs(urlsplit(self.path).query)
                want_stats = qs.get("stats", ["0"])[0] == "1"
                if want_stats and service.session_id and service.store is not None:
                    try:
                        from ground_station.analysis.session import (
                            telemetry_stats, compute_jitter, compute_effective_rate,
                        )
                        sid = service.session_id
                        stats = telemetry_stats(service.store, sid)
                        jitter = compute_jitter(service.store, sid)
                        eff_rate = compute_effective_rate(service.store, sid)
                        for sid_key, st in stats.items():
                            k = str(sid_key)
                            session_stats[k] = {
                                "count": st.count,
                                "rate_hz": round(st.rate_hz, 3) if st.rate_hz is not None else None,
                                "loss_events": st.loss_events,
                                "jitter_mean_ns": round(st.jitter_mean_ns, 1) if st.jitter_mean_ns is not None else None,
                                "jitter_std_ns": round(st.jitter_std_ns, 1) if st.jitter_std_ns is not None else None,
                                "jitter_max_ns": st.jitter_max_ns,
                                "effective_rate_hz": round(st.effective_rate_hz, 3) if st.effective_rate_hz is not None else None,
                                "source_clock_drift_ppm": round(st.source_clock_drift_ppm, 3) if st.source_clock_drift_ppm is not None else None,
                                # jitter detail
                                "jitter": jitter.get(sid_key, {}),
                                # effective rate detail
                                "effective_rate": eff_rate.get(sid_key, {}),
                            }
                    except Exception:
                        pass  # stats are best-effort
                view_model = {
                    "schema_id": snap.schema_id,
                    "telemetry_schema_id": getattr(snap, "telemetry_schema_id", None),
                    "adapter_version": getattr(snap, "adapter_version", None),
                    "session_id": snap.session_id,
                    "connected": snap.connected,
                    "samples": snap.samples,
                    "last_update_ns": snap.last_update_ns,
                    "slot_freshness_ttl_ns": getattr(snap, "slot_freshness_ttl_ns", ttl_ns),
                    "slots": slot_freshness,
                    "request_states": request_states,
                    "last_transaction_result": snap.last_transaction_result,
                    "command_results": list(snap.command_results) if snap.command_results else [],
                    # Action journal (WP6): most recent first.
                    "actions": list(reversed(service.action_journal()[-10:])),
                    "fault_count": len(service.fault_log()),
                    # S6: telemetry quality stats (jitter, effective rate)
                    "session_stats": session_stats,
                }
                self._json(200, view_model)
            # GET /api/events — session event journal
            elif route == "/api/events":
                limit_str = self.path.split("?")[1] if "?" in self.path else ""
                limit = 100
                if limit_str.startswith("limit="):
                    try:
                        limit = int(limit_str.split("=")[1].split("&")[0])
                    except (ValueError, IndexError):
                        pass
                events = service.events_for_session(limit=limit)
                self._json(200, {"events": events, "count": len(events)})
            # GET /api/faults — fault log
            elif route == "/api/faults":
                faults = service.fault_log()
                self._json(200, {"faults": faults, "count": len(faults)})
            # GET /api/actions — command action journal
            elif route == "/api/actions":
                actions = list(reversed(service.action_journal()))
                self._json(200, {"actions": actions, "count": len(actions)})
            # GET /replay/<session_id> — deterministic replay records
            elif route.startswith("/replay/") and "/records" not in route:
                parts = route.split("/")
                session_id = parts[2] if len(parts) >= 3 else None
                if not session_id:
                    self._json(404, {"error": "missing session id"})
                    return
                try:
                    # Verify the session exists before returning its records.
                    service.store.session(session_id)
                    limit, offset = self._paging(RECORDS_DEFAULT_LIMIT)
                    records = list(service.store.iter_records(
                        session_id, limit=limit, offset=offset))
                    self._json(200, {"session_id": session_id, "count": len(records),
                                      "records": records,
                                      "offset": offset, "limit": limit,
                                      "truncated": limit is not None
                                      and len(records) == limit})
                except KeyError:
                    self._json(404, {"error": "session not found"})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # ── agent control layer (GET) ──────────────────────────────────
            elif route == "/api/agent/stream":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                q = _AGENT.subscribe()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    sent_comment = False
                    while True:
                        try:
                            frame = q.get(timeout=SSE_HEARTBEAT_S)
                        except queue.Empty:
                            try:
                                self.wfile.write(b": heartbeat\n\n")
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                break
                            sent_comment = True
                            continue
                        if frame is None:
                            break
                        try:
                            self.wfile.write(frame)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            break
                        sent_comment = True
                finally:
                    _AGENT.unsubscribe(q)
                return
            elif route == "/api/agent/control":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                self._json(200, _AGENT.control_state())
            elif route == "/api/agent/actions":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                self._json(200, {"actions": _AGENT.action_specs()})
            elif route == "/api/agent/plans":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                self._json(200, {"plans": _AGENT.list_plans()})
            elif route.startswith("/api/agent/plans/"):
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                plan_id = route.split("/")[-1]
                detail = _AGENT.plan_detail(plan_id)
                if detail is None:
                    self._json(404, {"error": "plan not found"})
                    return
                self._json(200, detail)
            elif route == "/api/agent/approvals":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                self._json(200, {"approvals": _AGENT.pending_approvals()})
            elif route == "/api/agent/state":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                self._json(200, _AGENT.agent_state())
            elif route == "/api/agent/messages/wait":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                qs = parse_qs(urlsplit(self.path).query)
                since = int(qs.get("since", [0])[0] or 0)
                timeout = float(qs.get("timeout", ["30"])[0] or 30)
                notes = _AGENT.wait_messages(since, timeout)
                self._json(200, {"notes": notes,
                                 "last_seq": _AGENT.notes.last_seq})
            elif route == "/api/agent/history":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                qs = parse_qs(urlsplit(self.path).query)
                try:
                    since = int(qs.get("since", ["0"])[0] or 0)
                except ValueError:
                    since = 0
                limit = int(qs.get("limit", ["100"])[0] or 100)
                limit = min(max(0, limit), 2000)
                kind = qs.get("kind", [None])[0] or None
                source = qs.get("source", [None])[0] or None
                rows = _AGENT.journal_history(since=since, limit=limit,
                                              kind=kind, source=source)
                self._json(200, {"entries": rows,
                                 "since": since,
                                 "count": len(rows)})
            elif static_root is not None:
                # Static file serving
                from urllib.parse import unquote, urlsplit
                path = unquote(urlsplit(self.path).path)
                # Map / to index.html
                if path == "/":
                    path = "/index.html"
                # Strip /static/ or /plugins/ prefix if present
                if path.startswith("/static/"):
                    path = path[8:]  # remove /static/
                elif path.startswith("/plugins/"):
                    path = "plugins/" + path[9:]  # map /plugins/foo.js -> plugins/foo.js
                root = static_root.resolve()
                file_path = (root / path.lstrip("/")).resolve()
                if root not in file_path.parents and file_path != root:
                    self._json(403, {"error": "forbidden"})
                elif file_path.is_file():
                    try:
                        body = file_path.read_bytes()
                        self._static(200, body, _mime_type(str(file_path)))
                    except Exception:
                        self._json(500, {"error": "failed to read file"})
                else:
                    self._json(404, {"error": "file not found"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            from urllib.parse import parse_qs, urlsplit
            route = urlsplit(self.path).path
            # Validate headers
            bound_host = self.server.server_address[0]
            err = _validate_request_headers(self.headers, bound_host)
            if err is not None:
                self._drain_body()
                self._json(err[0], {"error": err[1]})
                return

            # Reject an unreadable or oversized body once, here, rather than in
            # each of the thirteen routes that read one.  We deliberately do not
            # drain first: the header we are rejecting is the only thing that
            # says how much there is to drain, so trusting it to clean up would
            # reintroduce exactly the unbounded read we are refusing.  Close the
            # connection instead of leaving an unread body on a keep-alive socket.
            try:
                self._content_length()
            except ValueError as exc:
                self.close_connection = True
                status = 413 if "too large" in str(exc) else 400
                self._json(status, {"error": str(exc)})
                return


            if route == "/commands":
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    txid = service.submit_command(int(body["command_id"]),
                                                  int(body.get("index", 0)),
                                                  float(body.get("value", 0.0)),
                                                  int(body.get("flags", 0)))
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(202, {"transaction_id": txid})
            # POST /subscribe — typed 0x21 envelope (slot subscription).
            # The /commands endpoint routes command_id 33 (0x21) through the
            # generic submit_command path, which sends a 0xCC 0xDD frame the
            # firmware does not recognise. The 0x21 envelope is built and
            # shipped by WifiBridge.subscribe_slot() instead. Slot 0 is
            # wired today (via _request_slot0_schema); slots 1..3 require
            # an explicit `ranges` list of DWARF names or pre-built
            # StreamRange tuples. Slots 9..12 are retired -- the firmware
            # only accepts 0..3 (SUBSCRIBE_MAX_SLOTS = 4).
            elif route == "/subscribe":
                if service.bridge is None:
                    self._json(503, {"error": "bridge unavailable"})
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    slot = int(body.get("slot", 0))
                    divider = int(body.get("divider", 1))
                    ranges = body.get("ranges", []) or []
                    service.bridge.subscribe_slot(
                        slot=slot, divider=divider, ranges=ranges,
                    )
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(202, {"slot": slot, "divider": divider, "ranges": ranges})
            # POST /api/recording/start — opt-in recording. Optional JSON
            # {reason, requested_by: "operator"|"agent:<name>", label}. A start
            # while already recording is a no-op returning the current state.
            # Recording never sends drone commands and never touches gates.
            elif route == "/api/recording/start":
                length = self._content_length()
                body = {}
                if length:
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except Exception:
                        self._json(400, {"error": "invalid JSON body"})
                        return
                if not isinstance(body, dict):
                    self._json(400, {"error": "body must be a JSON object"})
                    return
                requested_by = str(body.get("requested_by") or "operator")
                if requested_by not in ("operator",) \
                        and not str(requested_by).startswith("agent:"):
                    self._json(400, {"error": "requested_by must be operator "
                                             "or agent:<name>"})
                    return
                result = service.start_recording(
                    label=(str(body["label"]) if body.get("label") else None),
                    requested_by=requested_by,
                    reason=(str(body["reason"]) if body.get("reason") else None),
                )
                status = 202 if result.get("recording") else 200
                self._json(status, result)
            # POST /api/recording/stop — stop the active recording (idempotent).
            elif route == "/api/recording/stop":
                self._json(200, service.stop_recording())
            # POST /api/session/note — append an operator note
            # {text, kind: "note"|"goal"|"marker", source}. Seed of the
            # operator <-> agent communication session.
            elif route == "/api/session/note":
                length = self._content_length()
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    self._json(400, {"error": "invalid JSON body"})
                    return
                if not isinstance(body, dict) or not str(body.get("text") or "").strip():
                    self._json(400, {"error": "note requires a non-empty 'text'"})
                    return
                kind = str(body.get("kind") or "note")
                if kind not in ("note", "goal", "marker"):
                    self._json(400, {"error": "kind must be note|goal|marker"})
                    return
                source = str(body["source"]) if body.get("source") else None
                result = service.add_session_note(
                    str(body["text"]), kind=kind, source=source)
                # Feed the agent note log (seq + message broadcast) and record
                # the note as a session event for replay.
                if _AGENT is not None:
                    entry = _AGENT.receive_operator_note(
                        str(body["text"]), kind, source)
                    _AGENT._log_event("note", {
                        "text": str(body["text"]),
                        "kind": kind, "source": source, "seq": entry["seq"]})
                self._json(201, result)
            # POST /subscribe/preview — pure-validation echo of /subscribe.
            # Resolves DWARF names against the firmware ELF without sending
            # any bytes to the FC. Lets the dashboard show the user what
            # the slot will emit BEFORE they click Subscribe (PLANNING
            # PROMPT §8 Pattern 3). Response carries ``ranges`` (resolved
            # names), ``unresolved`` (names that did not resolve against
            # the ELF), ``var_count``, and ``expected_rate_hz``.
            elif route == "/subscribe/preview":
                if service.bridge is None:
                    self._json(503, {"error": "bridge unavailable"})
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    slot = int(body.get("slot", 0))
                    divider = int(body.get("divider", 1))
                    ranges = body.get("ranges", []) or []
                    preview = service.bridge.subscribe_preview(
                        slot=slot, divider=divider, ranges=ranges,
                    )
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, preview)
            # POST /experiments — start an experiment
            elif route == "/experiments":
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    name = body["name"]
                    settle_ticks = int(body.get("settle_ticks", 0))
                    measure_ticks = int(body.get("measure_ticks", 100))
                    parameters = dict(body.get("parameters", {}))
                    run = experiment_runtime.start(name, parameters,
                                                  settle_ticks, measure_ticks)
                    self._json(201, {"run_id": run.name})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
            # POST /experiments/<name>/abort — abort experiment
            elif route.startswith("/experiments/") and route.endswith("/abort"):
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                parts = route.split("/")
                name = parts[2] if len(parts) >= 3 else None
                active = experiment_runtime.active
                if active is None or active.name != name:
                    self._json(404, {"error": "experiment not found"})
                    return
                try:
                    experiment_runtime.abort("http_abort")
                    self._json(200, {"aborted": name})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
            # POST /replay/<session_id>/play — push stored telemetry onto the
            # live bus (no storage write, nothing sent to the drone).
            elif route.startswith("/replay/") and route.endswith("/play"):
                parts = route.split("/")
                session_id = parts[2] if len(parts) >= 4 else None
                try:
                    service.store.session(session_id)
                except (KeyError, TypeError):
                    self._json(404, {"error": "session not found"})
                    return
                try:
                    count = service.replay_to_bus(session_id)
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return
                self._json(200, {"session_id": session_id, "replayed": count})
            elif route.startswith("/sessions/") and route.endswith("/export"):
                from ground_station.analysis.session import export_session_csv
                parts = route.split("/")
                session_id = parts[2] if len(parts) >= 3 else None
                if not session_id:
                    self._json(404, {"error": "missing session id"})
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    output_path = body["output_path"]
                    stream_id = body.get("stream_id")
                    export_session_csv(service.store, session_id, output_path,
                                       stream_id if stream_id is not None else None)
                    self._json(200, {"exported": output_path})
                except KeyError:
                    self._json(404, {"error": "session not found"})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # ── agent control layer (POST) ─────────────────────────────────
            elif route == "/api/agent/control":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                if _AGENT.mode == "off":
                    self._drain_body()
                    self._agent_disabled()
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict) or "source" not in body:
                        self._json(400, {"error": "body requires a 'source'"})
                        return
                    result = _AGENT.set_control(body)
                except PermissionError as exc:
                    self._json(403, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return
                self._json(200, result)
            elif route == "/api/agent/plans":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                if _AGENT.mode == "off":
                    self._drain_body()
                    self._agent_disabled()
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    queue_if_busy = bool(body.get("queue", False))
                    plan = _AGENT.create_plan(body, queue_if_busy=queue_if_busy)
                except AgentDisabledError:
                    self._agent_disabled()
                    return
                except PlanBusyError:
                    self._json(409, {"error": "plan already running; pass "
                                             "queue:true to enqueue"})
                    return
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return
                self._json(201, plan.to_detail())
            elif route.startswith("/api/agent/plans/") \
                    and route.endswith("/cancel"):
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                if _AGENT.mode == "off":
                    # The five sibling 423 paths drain first; this one did not,
                    # so the client's body was still in flight when the socket
                    # closed -- ConnectionAbortedError [WinError 10053] on the
                    # client's send.  Reproduced with a 512 KB body in
                    # test_mode_off_cancel_with_a_body_does_not_break_the_connection;
                    # it is a race, ~1 failure in 5 per attempt without this drain.
                    self._drain_body()
                    self._agent_disabled()
                    return
                plan_id = route.split("/")[4]
                try:
                    plan = _AGENT.cancel_plan(plan_id)
                except KeyError:
                    self._json(404, {"error": "plan not found"})
                    return
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return
                self._json(200, plan.to_summary())
            elif route.startswith("/api/agent/approvals/") \
                    and ("/approve" in route or "/reject" in route):
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                if _AGENT.mode == "off":
                    self._drain_body()
                    self._agent_disabled()
                    return
                parts = route.split("/")
                # parts: ['', 'api', 'agent', 'approvals', plan_id, step_id, 'approve']
                try:
                    plan_id = parts[4]
                    step_id = parts[5]
                    verb = parts[6]
                except IndexError:
                    self._json(400, {"error": "malformed approval route"})
                    return
                length = self._content_length()
                body = {}
                if length:
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except Exception:
                        body = {}
                source = str(body.get("source") or "operator")
                try:
                    item = _AGENT.decide_approval(
                        plan_id, step_id, (verb == "approve"), source)
                except KeyError:
                    self._json(404, {"error": "approval not found"})
                    return
                except PermissionError as exc:
                    self._json(403, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return
                self._json(200, item.to_dict())
            elif route == "/api/agent/ui-ack":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    ok = bool(body.get("ok", True))
                    sent = _AGENT.confirm_ui_ack(
                        str(body.get("plan_id", "")),
                        str(body.get("step_id", "")),
                        ok, (str(body["error"]) if body.get("error") else None))
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, {"acked": sent})
            elif route == "/api/agent/ui-state":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    state = _AGENT.set_ui_state(body)
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, state)
            elif route == "/api/agent/message":
                if _AGENT is None:
                    self._json(503, {"error": "agent layer unavailable"})
                    return
                if _AGENT.mode == "off":
                    self._drain_body()
                    self._agent_disabled()
                    return
                try:
                    length = self._content_length()
                    body = json.loads(self.rfile.read(length) or b"{}")
                    text = str(body.get("text") or "").strip()
                    if not text:
                        self._json(400, {"error": "message requires non-empty text"})
                        return
                    source = str(body.get("source") or "agent:http")
                    entry = _AGENT.add_agent_message(text, source=source)
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return
                self._json(201, entry)
            else:
                self._json(404, {"error": "not found"})

        def _content_length(self) -> int:
            """Return the declared body length, or raise ``ValueError``.

            Every POST handler used to inline ``int(headers["Content-Length"])``
            and hand the result straight to ``rfile.read``.  That trusted the
            client twice: a non-numeric header raised out of the handler, and a
            huge one made the server read until the socket ran dry.  Both
            checks live here now so a handler only has to ask for the length.
            """
            raw = self.headers.get("Content-Length", "0")
            try:
                length = int(raw)
            except (TypeError, ValueError):
                raise ValueError("invalid Content-Length")
            if length < 0:
                raise ValueError("negative Content-Length")
            if length > _MAX_BODY_BYTES:
                raise ValueError(
                    "body too large (max %d bytes)" % _MAX_BODY_BYTES)
            return length

        def _drain_body(self) -> None:
            """Read and discard the request body before sending an error.

            On Windows, sending a response while the socket still has unread
            data causes ``ConnectionAbortedError [WinError 10053]``.  Drain
            the body so the connection stays clean.

            This runs on the *rejection* path, so it must never raise and must
            never be talked into a large read by the header it is reacting to:
            a rejected request is exactly the one whose Content-Length we have
            already decided not to trust.
            """
            try:
                length = min(int(self.headers.get("Content-Length", "0")),
                             _MAX_BODY_BYTES)
            except (TypeError, ValueError):
                return
            if length > 0:
                try:
                    self.rfile.read(length)
                except Exception:
                    pass

        def _agent_disabled(self) -> None:
            self._json(423, {"ok": False, "error": {"code": "agent_disabled"}})

        def log_message(self, *_args):
            return

    return Handler


class ApiServer:
    def __init__(self, service, host: str = "127.0.0.1", port: int = 0,
                 static_root: Path | None = None, experiment_runtime=None,
                 shell_root: str | None = None,
                 copilot: Copilot | None = None):
        self.service = service
        self.static_root = static_root
        self.experiment_runtime = experiment_runtime
        self.copilot = copilot
        self.agent = build_agent_manager(
            service,
            shell_root=shell_root if shell_root is not None
            else str(Path(__file__).parents[2]
                      / "docs" / "dashboard-platform" / "shell"),
            copilot=copilot,
        )
        self.hub = StateHub()
        # Bug 1 (AUDIT_2026-09-21 §Bug 1): the hub cache was refreshed only
        # on command results, so any hub consumer saw a frozen view after
        # the first command. Mirror every service notification (telemetry
        # ingest, command results) into the hub so hub.latest_state()
        # tracks the live service state.
        service.add_listener(self.hub.publish)
        self._stop_event = threading.Event()
        self.server = ThreadingHTTPServer(
            (host, port),
            make_handler(service, hub=self.hub, static_root=static_root,
                         experiment_runtime=experiment_runtime,
                         agent=self.agent,
                         copilot=copilot),
        )
        self._poll_thread = None
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="ground_station_api", daemon=True)

    @property
    def address(self):
        return self.server.server_address

    def start(self) -> None:
        self.thread.start()
        self.agent.start()
        # Start the gateway polling thread so command result frames
        # (0x30/0x31/0x32) are drained from the bridge queue and published
        # to the hub — making them visible in the next /state poll from the browser.
        self._poll_thread = threading.Thread(
            target=_start_gateway_polling,
            args=(self.service, self.hub, self._stop_event),
            name="gateway_poll", daemon=True,
        )
        self._poll_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.agent.stop()
        self.server.shutdown()
        self.thread.join(timeout=2)
        self._poll_thread.join(timeout=2) if self._poll_thread else None
        self.server.server_close()
