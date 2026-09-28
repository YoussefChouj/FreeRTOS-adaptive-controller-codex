"""Rename VOFA+ channels (I0..In) by patching its config while it is closed.

VOFA+ rewrites vofa+.config.json on exit, so a patch made while it runs is lost.
Sequence: graceful close -> wait -> force after a timeout -> backup -> patch ->
relaunch.
"""
import copy
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

CONFIG = Path(os.path.expandvars(r"%LOCALAPPDATA%\vofa+\100\context\vofa+.config.json"))
SHORTCUT = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\VOFA+\x64\vofa+.lnk"
EXE = "vofa+.exe"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _find_settings(node):
    """Return the settings_ctx list (per-channel entries) anywhere in the tree."""
    if isinstance(node, dict):
        val = node.get("settings_ctx")
        if isinstance(val, list):
            return val
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_settings(child)
        if found is not None:
            return found
    return None


def read_names(path=CONFIG):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ctx = _find_settings(data)
    return [e.get("name", "") for e in ctx] if ctx is not None else []


def patch_names(names, path=CONFIG, backup=True):
    """Name the first len(names) channels; reset the rest to I<i>. Returns backup path."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    ctx = _find_settings(data)
    if ctx is None:
        raise ValueError("settings_ctx not found in %s" % path)
    template = ctx[-1] if ctx else {"is_draw": True, "color": "#ffffff", "scale": 1,
                                    "yoffset": 0, "xoffset": 0, "decimal": -7,
                                    "value": 0, "name": ""}
    while len(ctx) < len(names):
        ctx.append(copy.deepcopy(template))
    for i, entry in enumerate(ctx):
        if i < len(names):
            entry["name"] = str(names[i])
            entry["is_draw"] = True
        else:
            entry["name"] = "I%d" % i
    bak = None
    if backup:
        bak = path.with_name(path.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
        shutil.copy2(path, bak)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return bak


def is_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % EXE, "/NH"],
                         capture_output=True, text=True, creationflags=_NO_WINDOW).stdout
    return EXE.lower() in out.lower()


def _close(timeout=8.0):
    """Ask VOFA+ to close (it saves its config), force-kill if it hangs."""
    if not is_running():
        return "not running"
    subprocess.run(["taskkill", "/IM", EXE], capture_output=True,
                   creationflags=_NO_WINDOW)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if not is_running():
            return "closed"
        time.sleep(0.3)
    subprocess.run(["taskkill", "/F", "/IM", EXE], capture_output=True,
                   creationflags=_NO_WINDOW)
    time.sleep(1.0)
    return "forced"


def apply_names(names, relaunch=True):
    """Close VOFA+, patch names, relaunch. Returns a summary dict."""
    closed = _close()
    if is_running():
        raise RuntimeError("VOFA+ is still running; close it and retry")
    bak = patch_names(names)
    if relaunch:
        os.startfile(SHORTCUT)
    return {"closed": closed, "backup": str(bak), "names": list(names),
            "relaunched": relaunch}
