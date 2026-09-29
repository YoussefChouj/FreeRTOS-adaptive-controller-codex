"""VOFA Studio server: python -m ground_station.vofa_studio [--port 8090] [--no-browser]"""
import argparse
import asyncio
import threading
import webbrowser
from pathlib import Path

from aiohttp import web

from ground_station.livewatch.symbols import SymbolResolver
from ground_station.vofa_studio import core, vofa_config

STATIC = Path(__file__).with_name("static")

_resolver = None
_resolver_lock = threading.Lock()
_session = None


def resolver():
    global _resolver
    with _resolver_lock:
        # Reload after a rebuild: addresses and stream limits both come from
        # the ELF, and the planner must describe the image now on the board.
        mtime = core.ELF.stat().st_mtime
        if _resolver is None or _resolver[0] != mtime:
            _resolver = (mtime, SymbolResolver(core.ELF))
        return _resolver[1]


def _err(msg, status=400):
    return web.json_response({"error": str(msg)}, status=status)


async def _off(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


# ------------------------------------------------------------------ presets

async def presets_list(request):
    if request.query.get("info"):
        return web.json_response(await _off(core.preset_info))
    return web.json_response(core.list_presets())


async def presets_merge(request):
    try:
        names = (await request.json()).get("names") or []
        presets = [core.load_preset(n) for n in names]
        return web.json_response(await _off(
            lambda: core.merge_presets(presets, core.slot_fits(resolver()))))
    except (ValueError, FileNotFoundError) as exc:
        return _err(exc, 404)


async def preset_get(request):
    try:
        return web.json_response(core.load_preset(request.match_info["name"]))
    except (ValueError, FileNotFoundError) as exc:
        return _err(exc, 404)


async def preset_save(request):
    try:
        return web.json_response(core.save_preset(await request.json()))
    except ValueError as exc:
        return _err(exc)


async def preset_delete(request):
    try:
        core.delete_preset(request.match_info["name"])
        return web.json_response({"ok": True})
    except (ValueError, FileNotFoundError) as exc:
        return _err(exc, 404)


# ----------------------------------------------------------- symbols/budget

def _complete(q, limit=40):
    r = resolver()
    q = q.strip()
    cut = max(q.rfind("."), q.rfind("["))
    if cut < 0:
        low = q.lower()
        names = r.names()
        hits = [n for n in names if n.lower().startswith(low)]
        hits += [n for n in names if low in n.lower() and n not in hits]
        return hits[:limit]
    parent = q[:cut]
    try:
        fields = r.fields_of(parent)
    except Exception:
        return []
    out = []
    for f in fields:
        full = parent + (f if f.startswith("[") else "." + f)
        if full.lower().startswith(q.lower()):
            out.append(full)
    return out[:limit]


async def symbols(request):
    return web.json_response(await _off(_complete, request.query.get("q", "")))


async def budget(request):
    body = await request.json()
    return web.json_response(await _off(core.plan_budget, resolver(),
                                        body.get("slots", [])))


async def next_name(request):
    return web.json_response(
        {"name": core.next_session_name(request.query.get("prefix", "flight"))})


# ------------------------------------------------------------------ session

async def session_start(request):
    global _session
    if _session is not None and _session.state in ("connecting", "streaming"):
        return _err("a session is already running", 409)
    body = await request.json()
    preset = body.get("preset") or {}
    plan = await _off(core.plan_budget, resolver(), preset.get("slots", []))
    if not plan["ok"]:
        return _err("plan not valid: " + "; ".join(
            plan["errors"] + [e for s in plan["slots"] for e in s["errors"]]))
    try:
        _session = core.Session(
            preset, body.get("name", ""), mode=body.get("mode", "timed"),
            seconds=float(body.get("seconds") or 60),
            window_s=float(body.get("window_s") or 120),
            vofa_addr=body.get("vofa_addr", "127.0.0.1:1347"),
            vofa_channels=body.get("vofa"), notes=body.get("notes", ""),
            data_port=body.get("data_port", "udp:14550"))
        _session.start()
    except ValueError as exc:
        return _err(exc)
    return web.json_response({"ok": True, "name": _session.name})


async def session_stop(request):
    if _session is None:
        return _err("no session")
    _session.stop()
    await _off(_session.join, 5.0)
    return web.json_response(_session.status())


async def session_mark(request):
    if _session is None or _session.state != "streaming":
        return _err("not streaming")
    body = await request.json() if request.can_read_body else {}
    return web.json_response(_session.mark(body.get("note", "")))


async def session_status(request):
    if _session is None:
        return web.json_response({"state": "idle"})
    return web.json_response(_session.status())


# --------------------------------------------------------------------- vofa

async def vofa_names(request):
    try:
        names = await _off(vofa_config.read_names)
        running = await _off(vofa_config.is_running)
    except Exception as exc:
        return _err(exc, 500)
    return web.json_response({"names": names, "running": running})


async def vofa_legend(request):
    try:
        tabs = await _off(vofa_config.read_tabs)
        saved = vofa_config.TABVIEWS.stat().st_mtime
    except Exception as exc:
        return _err(exc, 500)
    return web.json_response({"tabs": tabs, "saved": saved})


async def index(request):
    return web.FileResponse(STATIC / "index.html",
                            headers={"Cache-Control": "no-store"})


def make_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/presets", presets_list)
    app.router.add_get("/api/presets/{name}", preset_get)
    app.router.add_post("/api/presets", preset_save)
    app.router.add_post("/api/presets/merge", presets_merge)
    app.router.add_delete("/api/presets/{name}", preset_delete)
    app.router.add_get("/api/symbols", symbols)
    app.router.add_post("/api/budget", budget)
    app.router.add_get("/api/next_name", next_name)
    app.router.add_post("/api/session/start", session_start)
    app.router.add_post("/api/session/stop", session_stop)
    app.router.add_post("/api/session/mark", session_mark)
    app.router.add_get("/api/session/status", session_status)
    app.router.add_get("/api/vofa/names", vofa_names)
    app.router.add_get("/api/vofa/legend", vofa_legend)
    return app


def main():
    ap = argparse.ArgumentParser(prog="vofa_studio")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.port == 8081:
        ap.error("8081 belongs to the dashboard service")
    threading.Thread(target=resolver, daemon=True).start()  # warm the DWARF index
    url = "http://127.0.0.1:%d/" % args.port
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    print("VOFA Studio on", url)
    web.run_app(make_app(), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
