"""Handoff helper.

python -m ground_station.agent_handoff refresh          # rewrite the AUTO block in docs/agent/HANDOFF.md
python -m ground_station.agent_handoff prompt --for agy # paste-ready prompt for another harness
"""
import argparse
import datetime
import pathlib
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "docs" / "agent" / "HANDOFF.md"
BEGIN, END = "<!-- AUTO:BEGIN -->", "<!-- AUTO:END -->"

ROLES = {
    "claude": "supervisor",
    "codex": "worker",
    "agy": "worker",
    "opencode": "worker",
    "generic": "worker",
}


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "?"


def dashboard_state():
    # GET only: safe against the live service. Bypass any system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8081/api/routes", timeout=1.5) as r:
            return "UP (HTTP %d)" % r.status
    except Exception:
        return "DOWN or unreachable"


def auto_block():
    dirty = [l for l in git("status", "--porcelain", "--", ".", ":!OBJ").splitlines() if l]
    ahead = git("rev-list", "--count", "@{u}..HEAD") or "?"
    lines = [
        BEGIN,
        "Refreshed: %s (mechanical, no LLM)" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "- Branch / HEAD: %s @ %s; unpushed commits: %s" % (git("branch", "--show-current"), git("rev-parse", "--short", "HEAD"), ahead),
        "- Dirty paths outside OBJ/: %d" % len(dirty),
    ]
    lines += ["  - " + l for l in dirty[:12]]
    if len(dirty) > 12:
        lines.append("  - ... +%d more" % (len(dirty) - 12))
    lines.append("- Dashboard 8081: %s" % dashboard_state())
    lines.append("- Last commits:")
    lines += ["  - " + l for l in git("log", "-5", "--format=%h %s").splitlines()]
    lines.append(END)
    return "\n".join(lines)


def refresh():
    text = HANDOFF.read_text(encoding="utf-8")
    block = auto_block()
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        tail = rest.split(END, 1)[1]
        text = head + block + tail
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    HANDOFF.write_text(text, encoding="utf-8")
    print("refreshed", HANDOFF)


def prompt(harness):
    role = ROLES.get(harness, "worker")
    parts = [
        "You are taking over an in-progress session as a %s agent (harness: %s). Read everything below before acting." % (role, harness),
        "Role rules: a worker never flashes, never contacts the probe or the live dashboard, never arms. "
        "A supervisor may act within AGENTS.md > Authorizations. Anything not listed there is NOT authorized.",
    ]
    for rel in ("AGENTS.md", "docs/agent/HANDOFF.md", "docs/agent/memory/rules.md"):
        parts.append("===== %s =====\n%s" % (rel, (ROOT / rel).read_text(encoding="utf-8")))
    out = "\n\n".join(parts)
    sys.stdout.write(out)
    sys.stderr.write("\n[handoff prompt ~%d tokens]\n" % (len(out) // 4))


def main():
    ap = argparse.ArgumentParser(prog="agent_handoff")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh")
    p = sub.add_parser("prompt")
    p.add_argument("--for", dest="harness", default="generic", choices=sorted(ROLES))
    a = ap.parse_args()
    refresh() if a.cmd == "refresh" else prompt(a.harness)


if __name__ == "__main__":
    main()
