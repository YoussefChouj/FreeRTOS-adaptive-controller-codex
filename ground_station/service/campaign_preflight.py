"""Workflow B preflight in one call: GET /api/campaign/preflight and the MCP tool campaign_preflight (WP-23).

``run_preflight`` returns ``{ok, instance, checks: [{name, value, pass, fix}]}``. ``value`` is what was read, raw
source fields included; ``fix`` is the one thing to do when the row is not green. ``pass`` is True, False, or None
for "cannot be checked from the stream" (amber: the operator checklist covers it). ``ok`` is True when no row is
False.

``vitals`` is the read-only snapshot behind the rows (GET /api/campaign/vitals): position, RC link and switches,
arm sources, battery, g_wfb_status and per-slot stream freshness in one reply.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any, Callable

from ground_station.livewatch.campaign_capture import POSITION_AXES
from ground_station.service.campaign_live import VBAT_SYMS, check_live_ready
from ground_station.service.instance_guard import INSTANCE_ID

ROOT = Path(__file__).resolve().parents[2]
ELF_PATH = ROOT / "OBJ" / "JX_FLY.axf"
CUSTODY_DIR = ".flashtool-cache"  # flashtool.artifact_custody: present = a build was not flashed

FRESH_S = 2.0          # PROPOSED: older than this is "not streaming" (core.ARM_STALE_NS is also 2 s)
STREAM_STALE_S = 1.0   # PROPOSED: the link row needs at least one slot newer than this
ORIGIN_TOL_M = 0.30    # PROPOSED: horizontal distance from the pad origin that still counts as on the pad

RC_LOST_SYMS = ("status.sbus_lost", "sbus_lost")
FLYMODE_SYMS = ("DroneStatus.FlyMode", "status.flymode")
AUTHORITY_SYMS = ("status.rc_authority", "s_authority")
OF_HOLD_SYMS = ("status.of_hold", "g_of_hold_active")   # ch6 HIGH: velocity loops drive tilt (Des_Att)
WFB_SYMS = ("g_wfb_status.prim_state", "g_wfb_status.safety_trip")
BUILD_ID_SYMS = tuple(f"build_id[{i}]" for i in range(4))


def _num(x: float) -> str:
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def _pick(fresh: dict[str, dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any] | None:
    return next((fresh[n] for n in names if n in fresh), None)


def vitals(service: Any, now_ns: Callable[[], int] = time.time_ns) -> dict[str, Any]:
    """Position, RC, arm, battery, g_wfb_status, build id and per-slot stream freshness, read once.

    Each single reading is ``{key, value, age_s}`` (the raw symbol that answered) or None when nothing fresh
    streams it.
    """
    names = (tuple(a.feedback for a in POSITION_AXES) + RC_LOST_SYMS + FLYMODE_SYMS + AUTHORITY_SYMS
             + OF_HOLD_SYMS + VBAT_SYMS + WFB_SYMS + BUILD_ID_SYMS)
    now = now_ns()
    fresh: dict[str, dict[str, Any]] = {}
    for name, (value, ts) in service.latest_values(names).items():
        age = (now - ts) / 1e9
        if age <= FRESH_S:
            fresh[name] = {"key": name, "value": value, "age_s": round(age, 3)}

    pos = {label: (round(fresh[a.feedback]["value"] * a.to_m, 3) if a.feedback in fresh else None)
           for label, a in zip("xyz", POSITION_AXES)}
    build = [fresh[n]["value"] for n in BUILD_ID_SYMS] if all(n in fresh for n in BUILD_ID_SYMS) else None

    streams: dict[str, dict[str, Any]] = {}
    snap = service.snapshot()
    for slot, data in (getattr(snap, "streams", {}) or {}).items():
        if isinstance(data, dict):
            last = data.get("last_update_ns") or 0
            streams[str(slot)] = {"age_s": round((now - last) / 1e9, 2) if last else None,
                                  "received": data.get("received", 0), "dropped": data.get("dropped", 0),
                                  "loss_pct": data.get("loss_pct", 0.0)}
    sources = getattr(service, "arm_sources", None)
    return {
        "time_ns": now,
        "source": getattr(service, "source", None),
        "connected": bool(getattr(snap, "connected", False)),
        "position_m": pos,
        "rc": {"sbus_lost": _pick(fresh, RC_LOST_SYMS), "flymode": _pick(fresh, FLYMODE_SYMS),
               "rc_authority": _pick(fresh, AUTHORITY_SYMS), "of_hold": _pick(fresh, OF_HOLD_SYMS)},
        "arm": {"state": service.arm_state(), "sources": sources() if callable(sources) else []},
        "battery": _pick(fresh, VBAT_SYMS),
        "wfb": {"prim_state": fresh.get(WFB_SYMS[0]), "safety_trip": fresh.get(WFB_SYMS[1])},
        "build_id": build,
        "streams": streams,
    }


def _row(name: str, value: str, ok: bool | None, fix: str = "") -> dict[str, Any]:
    return {"name": name, "value": value, "pass": ok, "fix": "" if ok is True else fix}


def _service_row(service: Any) -> dict[str, Any]:
    started = getattr(service, "started_at", None)
    up = f", up {time.time() - started:.0f} s" if isinstance(started, (int, float)) else ""
    return _row("service", f"pid {os.getpid()}, instance {INSTANCE_ID}{up}; a second 8081 refuses to start", True)


def _link_row(service: Any, v: dict[str, Any], stream_check: Callable[[], tuple[bool, str]] | None
              ) -> dict[str, Any]:
    if v["source"] == "sim":
        return _row("link", "simulator source (no radio link)", True)
    fix = "power the drone and check the MicoAir WiFi link; if the slots stay silent, restart 8081"
    if getattr(service, "bridge", None) is None:
        return _row("link", "no bridge: 8081 runs without the drone link", False,
                    "restart 8081 with the WiFi bridge: python -m ground_station.service")
    slots = v["streams"]
    parts = [f"slot {s} {d['age_s']} s old, {d['dropped']} dropped ({d['loss_pct']}%)" for s, d in sorted(slots.items())]
    check = stream_check or getattr(getattr(service, "streams", None), "preflight_check", None)
    rate_ok, why = check() if callable(check) else (True, "")
    value = ("connected" if v["connected"] else "NOT connected") + "; " + ("; ".join(parts) or "no slot streaming")
    if why:
        value += f"; stream check: {why}"
    newest = min((d["age_s"] for d in slots.values() if d["age_s"] is not None), default=None)
    ok = v["connected"] and newest is not None and newest <= STREAM_STALE_S and rate_ok
    return _row("link", value, ok, fix)


def _elf_identity(elf: Path) -> tuple[int, ...] | None:
    try:
        from ground_station.flashtool.build_id import identity_from_elf
        return tuple(identity_from_elf(elf).as_tuple())
    except Exception:
        return None


def _firmware_row(service: Any, v: dict[str, Any], elf_path: Path) -> dict[str, Any]:
    if v["source"] == "sim":
        return _row("firmware", "simulator source (no firmware)", True)
    flash = "flash the current build with the flash skill (rebuild_and_flash --yes), then restart 8081"
    elf = Path(elf_path)
    if not elf.exists():
        return _row("firmware", f"{elf} missing", False, flash)
    mtime = elf.stat().st_mtime
    parts = [f"{elf.name} built {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}"]
    problems: list[tuple[str, str]] = []
    if (elf.parent / CUSTODY_DIR).exists():
        problems.append((f"a build was not flashed ({CUSTODY_DIR} present): the axf may not match the drone", flash))
    started = getattr(service, "started_at", None)
    if isinstance(started, (int, float)) and mtime > started:
        problems.append(("the axf changed after 8081 started: 8081 still names streams from the old one",
                         "restart 8081 (it loads the axf at start)"))
    observed, expected = v["build_id"], _elf_identity(elf)
    if observed is None:
        parts.append("target build_id not streamed: checked the flash custody and the 8081 start time only")
    elif expected is not None and tuple(int(x) for x in observed) != expected:
        problems.append((f"target build_id {[int(x) for x in observed]} != axf {list(expected)}", flash))
    else:
        parts.append("target build_id matches the axf")
    value = "; ".join(parts + [p for p, _ in problems])
    return _row("firmware", value, not problems, problems[0][1] if problems else "")


def _wfb_row(service: Any, v: dict[str, Any], ready_check: Callable[[Any], None] | None) -> dict[str, Any]:
    if v["source"] == "sim":
        return _row("wfb_status", "simulator source (FakeDrone status)", True)
    try:
        (ready_check or check_live_ready)(service)
    except Exception as exc:
        return _row("wfb_status", str(exc), False,
                    "check the link and that the flashed firmware has workflow B (g_wfb_status); refresh once")
    prim, trip = v["wfb"]["prim_state"], v["wfb"]["safety_trip"]
    if prim is None:  # streamed inside check_live_ready's prime, after vitals() read the values
        vals = service.latest_values(WFB_SYMS[:1])
        prim = {"value": vals[WFB_SYMS[0]][0], "age_s": None} if WFB_SYMS[0] in vals else None
    if prim is None:
        return _row("wfb_status", "g_wfb_status.prim_state not streaming", False, "refresh once; then check the link")
    value = f"prim_state {_num(prim['value'])}" + (f", safety_trip {_num(trip['value'])}" if trip else "")
    if int(prim["value"]) != 0:
        return _row("wfb_status", value + " (not IDLE)", False,
                    "the firmware is not idle on the ground: land / disarm by RC, then refresh")
    if trip and int(trip["value"]) != 0:
        return _row("wfb_status", value, False,
                    "a safety trip is latched: find its cause (docs/workflow-b/interfaces.md safety_trip) before Go")
    return _row("wfb_status", value, True)


def _rc_row(v: dict[str, Any]) -> dict[str, Any]:
    rc = v["rc"]
    value = ", ".join(f"{d['key']} {_num(d['value'])}" for d in rc.values() if d) or "no RC field streaming"
    lost = rc["sbus_lost"]
    if lost is None:
        return _row("rc_link", value + "; sbus_lost is not on the core stream", None,
                    "cannot check from the stream: the operator confirms the RC link with checklist item rc_ready")
    if int(lost["value"]) != 0:
        return _row("rc_link", value, False, "RC link lost: turn the transmitter on and check it is bound, then refresh")
    return _row("rc_link", value, True)


def _of_hold_row(v: dict[str, Any]) -> dict[str, Any]:
    """ch6 LOW = angle mode: Des_Att (StabilizerTask.c) drops the velocity-loop output and flies level, so a GS
    flight holds no position and drifts (flight 2026-10-06 f01: vx demand +91 vs -43 cm/s, roll/pitch +-1.6 deg)."""
    hold = v["rc"].get("of_hold")
    if hold is None:
        return _row("of_hold", "status.of_hold not streaming", None,
                    "cannot check from the stream: the operator confirms ch6 (OF hold) is HIGH")
    value = f"{hold['key']} {_num(hold['value'])}"
    if int(hold["value"]) == 0:
        return _row("of_hold", value, False,
                    "flip RC ch6 HIGH (OF hold): in angle mode the position and velocity loops never reach tilt")
    return _row("of_hold", value, True)


def _arm_row(v: dict[str, Any]) -> dict[str, Any]:
    state, srcs = v["arm"]["state"], v["arm"]["sources"]
    raw = "; ".join(f"{s['key']} {_num(s['value'])} (slot {s['slot']}, {s['age_s']} s)" for s in srcs)
    disagree = len({s["value"] != 0 for s in srcs}) > 1
    value = f"{state} from {raw or 'no fresh arm field'}" + ("; SOURCES DISAGREE" if disagree else "")
    if state == "disarmed":
        return _row("arm_state", value, True)
    if state == "unknown":
        return _row("arm_state", value, False,
                    "no fresh DroneStatus.ARM_Status: it is on the core log plan, so check the link and refresh")
    if disagree:
        return _row("arm_state", value, False,
                    "arm sources disagree: DroneStatus.ARM_Status decides; after a reflash restart 8081 (stale names)")
    return _row("arm_state", value, False,
                "the drone reports armed: disarm by RC; the operator arms only after the preflight and checklist")


def _position_row(v: dict[str, Any]) -> dict[str, Any]:
    p = v["position_m"]
    missing = [a.feedback for label, a in zip("xyz", POSITION_AXES) if p[label] is None]
    if missing:
        return _row("position", "not streaming: " + ", ".join(missing), False,
                    "position is on the core log plan: check the link and refresh")
    d = math.hypot(p["x"], p["y"])
    value = (f"x {p['x']:+.2f} y {p['y']:+.2f} z {p['z']:+.2f} m, {d:.2f} m from the origin "
             f"(tolerance {ORIGIN_TOL_M} m)")
    return _row("position", value, d <= ORIGIN_TOL_M,
                "put the drone at pad centre, nose to the marked wall: arming re-zeroes the optical-flow origin "
                "where the drone sits, so the pad marker (not this reading) anchors the fence")


def _battery_row(v: dict[str, Any], pack_id: str | None, registry: Any = None) -> dict[str, Any]:
    b = v["battery"]
    if b is None:
        return _row("battery", "battery voltage not streaming (real_voltage)", False,
                    "real_voltage is on the core log plan: check the link and refresh")
    volts = float(b["value"])
    if not pack_id:
        return _row("battery", f"{volts:.2f} V ({b['key']}); no pack given, SoC not checked", None,
                    "pass pack=<id> to check the pack SoC gate")
    try:
        if registry is None:
            from ground_station.analysis.battery_model import PackRegistry
            registry = PackRegistry.load(None)
    except Exception as exc:
        return _row("battery", f"{volts:.2f} V; packs.yaml: {exc}", False, "fix ground_station/analysis/packs.yaml")
    ok, why = registry.next_flight_allowed(pack_id, volts, registry.min_rest_s)
    if why.startswith("INPUT: unknown pack"):
        return _row("battery", f"{volts:.2f} V; {why}", False,
                    f"use a pack id from packs.yaml: {', '.join(registry.pack_ids())}")
    if not ok:
        return _row("battery", f"{volts:.2f} V, pack {pack_id}: {why}", False, "swap to a charged pack")
    soc = registry.predict_soc(pack_id, volts)
    return _row("battery", f"{volts:.2f} V, pack {pack_id}: resting SoC {soc:.0f}%, gate ok", True)


def _runner_row(campaign: Any) -> dict[str, Any]:
    if campaign is None:
        return _row("runner", "campaign runner unavailable", False, "restart 8081")
    st = campaign.state()
    value = f"{st['status']}: {st.get('banner', '')}".rstrip(": ")
    return _row("runner", value, st["status"] != "running",
                "a campaign is flying: wait for it to finish, or Pause / Land it")


def _log_plan_row(campaign_path: str | None, pack_id: str | None) -> dict[str, Any]:
    from ground_station.service.campaign_launch import resolve_campaign_path
    from ground_station.service.campaign_logplan import campaign_log_plans
    from ground_station.service.campaign_schema import load_campaign

    if not campaign_path:
        return _row("log_plan", "no campaign given", False,
                    "pass campaign=<launch copy path> (campaign_launch prints it)")
    try:
        c = load_campaign(resolve_campaign_path(campaign_path))
        plans = campaign_log_plans(c)
    except Exception as exc:
        return _row("log_plan", f"{campaign_path}: {exc}", False,
                    "fix the campaign yaml, then run campaign_launch again")
    value = f"{c.campaign}: " + ", ".join(
        f"{p['experiment']} {p['plan']['rate_hz']:g} Hz/{len(p['subscribe_steps'])} slots" for p in plans)
    if pack_id and c.packs and c.packs[0] != pack_id:
        return _row("log_plan", value + f"; launch copy packs {list(c.packs)}", False,
                    f"the launch copy flies pack {c.packs[0]} first: run campaign_launch again with --pack {pack_id}")
    return _row("log_plan", value, True)


def run_preflight(service: Any, campaign: Any = None, campaign_path: str | None = None, pack_id: str | None = None,
                  *, elf_path: Path = ELF_PATH, stream_check: Callable[[], tuple[bool, str]] | None = None,
                  ready_check: Callable[[Any], None] | None = None, registry: Any = None,
                  now_ns: Callable[[], int] = time.time_ns) -> dict[str, Any]:
    """Every workflow B launch check in one reply; read-only except check_live_ready priming g_wfb_status."""
    v = vitals(service, now_ns)
    checks = [
        _service_row(service),
        _link_row(service, v, stream_check),
        _firmware_row(service, v, elf_path),
        _wfb_row(service, v, ready_check),
        _rc_row(v),
        _of_hold_row(v),
        _arm_row(v),
        _position_row(v),
        _battery_row(v, pack_id, registry),
        _runner_row(campaign),
        _log_plan_row(campaign_path, pack_id),
    ]
    return {"ok": all(c["pass"] is not False for c in checks), "instance": INSTANCE_ID, "checks": checks}
