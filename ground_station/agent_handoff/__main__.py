"""Agent coordination and handoff. Stdlib only, no LLM. Works from any worktree or cwd of this repo.

A stream is one line of work: a name, a tree (the main tree or .worktrees/<name>), a branch and one page.
Live state (claims, locks) is machine-local in <main tree>/.agent_state/coord/ and is shared by every
session, account and harness on this machine. Pages are git-tracked on the stream's own branch.

  start [stream] [--as H] [--takeover]  claim the stream, refresh its page, print board + page
  new <stream> [--base main]            worktree .worktrees/<stream> on branch <stream>, with a page
  refresh [stream]                      rewrite the page's AUTO block, heartbeat the claim
  board [--all] [--workers]             every stream: head, dirty, holder, next action; then locks
  lock <res> [--ttl MIN] [--note T]     machine-wide lease (hw, keil); exit 3 if another stream holds it
  unlock <res> [--force]
  release [stream]                      drop the claim (stream parked or done)
  prompt [stream] [--for H]             paste-ready prompt for a harness that cannot read the repo
"""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parents[2]
BEGIN, END = "<!-- AUTO:BEGIN -->", "<!-- AUTO:END -->"
LIVE_MIN = 45  # a claim with a heartbeat younger than this is treated as a live session
# First one set wins. AGENT_SESSION_ID lets any harness or the operator pin an identity by hand.
ID_VARS = ("AGENT_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_TRACE_ID")

SKELETON = """# Stream {name} - handoff page (overwrite at every task boundary, keep under 3 KB)

Tree: `{rel}` | Branch: `{branch}`. Edit only this page, never another stream's page.

## Goal now
(one or two lines)

## Facts (measured this session, dated)
-

## Next actions (3 lines, most urgent first)
1.

## Do not
-

{begin}
{end}
"""


def run(cmd, cwd=None, timeout=20):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout.rstrip()   # rstrip: keep the leading space of a porcelain " M path" line
    except (OSError, subprocess.SubprocessError):
        return ""


def git(tree, *args):
    return run(["git", "-C", str(tree), *args])


def main_root():
    # The parent of the common .git dir is the main tree, whichever worktree we were called from.
    cmd = ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]
    out = run(cmd, cwd=os.getcwd()) or run(cmd, cwd=HERE)
    return pathlib.Path(out).parent if out else HERE


MAIN = main_root()
COORD = MAIN / ".agent_state" / "coord"


def trees():
    """{stream: (path, branch)}. The main tree is stream 'main'; a worktree is named by its directory."""
    out, cur = {}, {}
    for line in git(MAIN, "worktree", "list", "--porcelain").splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"path": pathlib.Path(line[9:])}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "")
        elif not line and cur:
            p = cur["path"]
            name = "main" if p.exists() and os.path.samefile(p, MAIN) else p.name
            out[name] = (p, cur.get("branch", "(detached)"))
            cur = {}
    return out


def page_of(stream, tree):
    return tree / "docs" / "agent" / ("HANDOFF.md" if stream == "main" else "streams/%s.md" % stream)


def rel(path):
    try:
        return pathlib.Path(path).resolve().relative_to(MAIN.resolve()).as_posix() or "."
    except ValueError:
        return str(path)


def me():
    for v in ID_VARS:
        if os.environ.get(v):
            return os.environ[v][:12]
    return ""


def load(kind, name):
    try:
        return json.loads((COORD / kind / (name + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(kind, name, obj):
    d = COORD / kind
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ("%s.tmp%d" % (name, os.getpid()))
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, d / (name + ".json"))


def names(kind):
    d = COORD / kind
    return sorted(f.stem for f in d.glob("*.json")) if d.is_dir() else []


def age_min(t):
    return int((time.time() - t) / 60)


def my_stream(arg, ts):
    """Explicit name, else the claim this session holds, else the worktree of cwd, else main."""
    if arg:
        return arg
    sid = me()
    if sid:
        for n in names("claims"):
            if (load("claims", n) or {}).get("id") == sid:
                return n
    top = run(["git", "rev-parse", "--show-toplevel"], cwd=os.getcwd())
    for name, (p, _) in ts.items():
        if top and p.exists() and os.path.samefile(top, p):
            return name
    return "main"


def need(stream, ts):
    if stream not in ts:
        sys.exit("no stream %r. Known: %s. Create one with: new <stream>" % (stream, ", ".join(sorted(ts))))
    return ts[stream]


def dashboard_state():
    # GET only: safe against the live service. Bypass any system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8081/api/routes", timeout=1.5) as r:
            return "UP (HTTP %d)" % r.status
    except Exception:
        return "DOWN or unreachable"


def dirty_paths(tree):
    return [l for l in git(tree, "status", "--porcelain", "--", ".", ":!OBJ").splitlines() if l]


def lock_line(res):
    k = load("locks", res) or {}
    left = k.get("until", 0) - time.time()
    state = "%d min left" % max(1, round(left / 60)) if left > 0 else "EXPIRED"
    return "%s: stream %s, %s%s" % (res, k.get("stream", "?"), state, (" - " + k["note"]) if k.get("note") else "")


def auto_block(stream, tree, branch):
    dirty = dirty_paths(tree)
    lines = [
        BEGIN,
        "Refreshed: %s (mechanical, no LLM)" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "- Tree: %s; branch / HEAD: %s @ %s; unpushed commits: %s" % (
            rel(tree), branch, git(tree, "rev-parse", "--short", "HEAD"),
            git(tree, "rev-list", "--count", "@{u}..HEAD") or "no upstream (never pushed)"),
        "- Dirty paths outside OBJ/: %d" % len(dirty),
    ]
    lines += ["  - " + l for l in dirty[:8]]
    if len(dirty) > 8:
        lines.append("  - ... +%d more" % (len(dirty) - 8))
    if stream == "main":
        lines.append("- Dashboard 8081: %s" % dashboard_state())
    held = [lock_line(r) for r in names("locks") if (load("locks", r) or {}).get("stream") == stream]
    if held:
        lines.append("- Locks held: " + "; ".join(held))
    lines.append("- Last commits:")
    lines += ["  - " + l for l in git(tree, "log", "-5", "--format=%h %s").splitlines()]
    lines.append(END)
    return "\n".join(lines)


def refresh(stream, ts, quiet=False):
    tree, branch = need(stream, ts)
    page = page_of(stream, tree)
    if not page.exists():
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(SKELETON.format(name=stream, rel=rel(tree), branch=branch, begin=BEGIN, end=END), encoding="utf-8")
    text = page.read_text(encoding="utf-8")
    block = auto_block(stream, tree, branch)
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        text = head + block + rest.split(END, 1)[1]
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    page.write_text(text, encoding="utf-8")
    c = load("claims", stream)
    if c and (c.get("id") == me() or not c.get("id")):
        c["beat"] = time.time()
        save("claims", stream, c)
    if not quiet:
        print("refreshed", rel(page))
    return page


def next_action(page):
    try:
        lines = page.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "(no page)"
    for i, l in enumerate(lines):
        if l.startswith("## Next"):
            for m in lines[i + 1:]:
                if m.strip():
                    return m.strip()[:110]
    return ""


def board(ts, show_all=False, workers=False):
    print("STREAMS (name | branch@head | unpushed | dirty | holder | next)")
    for name in sorted(ts, key=lambda n: (n != "main", n)):
        tree, branch = ts[name]
        page, c = page_of(name, tree), load("claims", name)
        if not (show_all or name == "main" or c or page.exists()):
            continue
        holder = "free"
        if c:
            a = age_min(c.get("beat", 0))
            holder = "%s%s, beat %d min ago%s" % (c.get("harness", "?"), (" " + c["id"][:6]) if c.get("id") else "",
                                                 a, "" if a < LIVE_MIN else " (stale)")
        print("- %s | %s@%s | %s | %d | %s | %s" % (
            name, branch, git(tree, "rev-parse", "--short", "HEAD"),
            git(tree, "rev-list", "--count", "@{u}..HEAD") or "no upstream",
            len(dirty_paths(tree)), holder, next_action(page)))
    locks = names("locks")
    print("LOCKS: " + ("; ".join(lock_line(r) for r in locks) if locks else "none held"))
    if workers:
        n = run(["wsl", "-d", "Ubuntu", "--", "bash", "-c", "pgrep -fc '[r]un-worker.sh' || true"], timeout=25)
        print("WORKERS: local running = %s (cap 1, enforced by the power guard in agent-ops.ps1)" % (n.splitlines()[-1] if n else "?"))


def start(stream, harness, takeover, ts):
    need(stream, ts)
    c, sid = load("claims", stream), me()
    same = bool(c) and ((sid and c.get("id") == sid) or (not sid and not c.get("id") and c.get("harness") == harness))
    if c and not same and not takeover and age_min(c.get("beat", 0)) < LIVE_MIN:
        print("STOP: stream %r is held by a live session (%s %s, last beat %d min ago)." % (
            stream, c.get("harness", "?"), c.get("id", ""), age_min(c.get("beat", 0))))
        print("Two sessions must not write one tree. Options: `new <name>` for your own worktree; `start <other stream>`;")
        print("or, only if the operator told you to take this stream over: `start %s --takeover`. Read-only work needs no claim.\n" % stream)
        board(ts)
        sys.exit(2)
    if sid:  # one stream per session
        for n in names("claims"):
            if n != stream and (load("claims", n) or {}).get("id") == sid:
                (COORD / "claims" / (n + ".json")).unlink()
    now = time.time()
    save("claims", stream, {"id": sid, "harness": harness, "since": c["since"] if same else now, "beat": now})
    page = refresh(stream, ts, quiet=True)
    print("You hold stream %r (tree %s, branch %s). Page: %s" % (stream, rel(ts[stream][0]), ts[stream][1], rel(page)))
    if c and not same:
        print("Took over from: %s %s (last beat %d min ago). Its uncommitted work is the dirty list below." % (
            c.get("harness", "?"), c.get("id", ""), age_min(c.get("beat", 0))))
    board(ts)
    print("\n===== %s =====" % rel(page))
    print(page.read_text(encoding="utf-8"))


def new(stream, base, harness, ts):
    if stream in ts:
        sys.exit("stream %r already exists: start %s" % (stream, stream))
    tree = MAIN / ".worktrees" / stream
    r = subprocess.run(["git", "-C", str(MAIN), "worktree", "add", "-b", stream, str(tree), base], capture_output=True, text=True)
    if r.returncode:
        sys.exit("git worktree add failed: " + r.stderr.strip())
    start(stream, harness, False, trees())


def lock(res, ttl, note, stream, retry=True):
    cur, now = load("locks", res), time.time()
    if cur and cur.get("stream") != stream and now < cur.get("until", 0):
        print("LOCKED " + lock_line(res) + ". Do other work and ask the operator; do not poll.")
        sys.exit(3)
    rec = {"stream": stream, "id": me(), "since": now, "until": now + ttl * 60, "note": note}
    if cur is None:
        (COORD / "locks").mkdir(parents=True, exist_ok=True)
        try:  # O_EXCL makes the first taker win when two sessions race
            fd = os.open(str(COORD / "locks" / (res + ".json")), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if retry:
                return lock(res, ttl, note, stream, retry=False)
            sys.exit(3)
        os.write(fd, json.dumps(rec).encode("utf-8"))
        os.close(fd)
    else:
        save("locks", res, rec)
    print("lock %s taken by stream %s for %d min. Release it with: unlock %s" % (res, stream, ttl, res))


def unlock(res, stream, force):
    cur = load("locks", res)
    if not cur:
        print("lock %s was not held" % res)
        return
    if cur.get("stream") != stream and time.time() < cur.get("until", 0) and not force:
        sys.exit("lock %s belongs to stream %s; not released (operator may pass --force)" % (res, cur.get("stream")))
    (COORD / "locks" / (res + ".json")).unlink()
    print("lock %s released" % res)


def prompt(stream, harness, ts):
    tree, branch = need(stream, ts)
    parts = [
        "You are taking over stream %r of an in-progress project (harness: %s, tree %s, branch %s). "
        "Read everything below before acting." % (stream, harness, rel(tree), branch),
        "Role: the operator started you on this stream, so you supervise its code, tests, workers and commits. "
        "Hardware grants (flash, probe, dashboard 8081 writes, arming) are NOT yours unless the operator grants "
        "them in this chat. Anything not listed in AGENTS.md > Authorizations is not authorized.",
    ]
    for label, path in (("AGENTS.md", MAIN / "AGENTS.md"), (rel(page_of(stream, tree)), refresh(stream, ts, quiet=True)),
                        ("docs/agent/memory/rules.md", MAIN / "docs/agent/memory/rules.md")):
        parts.append("===== %s =====\n%s" % (label, path.read_text(encoding="utf-8")))
    out = "\n\n".join(parts)
    sys.stdout.write(out)
    sys.stderr.write("\n[handoff prompt ~%d tokens]\n" % (len(out) // 4))


def main():
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="agent_handoff", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "refresh", "release", "prompt"):
        p = sub.add_parser(name)
        p.add_argument("stream", nargs="?")
        if name in ("start", "prompt"):
            p.add_argument("--as", "--for", dest="harness", default="claude" if name == "start" else "generic")
        if name == "start":
            p.add_argument("--takeover", action="store_true")
    p = sub.add_parser("new")
    p.add_argument("stream")
    p.add_argument("--base", default="main")
    p.add_argument("--as", dest="harness", default="claude")
    p = sub.add_parser("board")
    p.add_argument("--all", action="store_true")
    p.add_argument("--workers", action="store_true")
    p = sub.add_parser("lock")
    p.add_argument("res")
    p.add_argument("--ttl", type=int, default=30)
    p.add_argument("--note", default="")
    p.add_argument("--stream")
    p = sub.add_parser("unlock")
    p.add_argument("res")
    p.add_argument("--stream")
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    ts = trees()
    if a.cmd == "start":
        start(my_stream(a.stream, ts), a.harness, a.takeover, ts)
    elif a.cmd == "new":
        new(a.stream, a.base, a.harness, ts)
    elif a.cmd == "refresh":
        refresh(my_stream(a.stream, ts), ts)
    elif a.cmd == "board":
        board(ts, a.all, a.workers)
    elif a.cmd == "lock":
        lock(a.res, a.ttl, a.note, my_stream(a.stream, ts))
    elif a.cmd == "unlock":
        unlock(a.res, my_stream(a.stream, ts), a.force)
    elif a.cmd == "release":
        s = my_stream(a.stream, ts)
        f = COORD / "claims" / (s + ".json")
        if f.exists():
            f.unlink()
        print("released claim on", s)
    else:
        prompt(my_stream(a.stream, ts), a.harness, ts)


if __name__ == "__main__":
    main()
