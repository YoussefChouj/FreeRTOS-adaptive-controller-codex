"""docs/lab/COMMANDS.md stays true: every command, flag, subcommand, key and repo path it names exists in the code.

A lab command changed without its doc line fails here, so the doc can be typed by hand and trusted by agents.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "lab" / "COMMANDS.md"
CAMERA_SH = ROOT / "ground_station" / "lab" / "camera_truth.sh"
TRACKER = ROOT / "ground_station" / "analysis" / "live_tracker.py"
# .\X launchers at the repo root -> the module whose flags they take (cam passes extra args to the tracker)
LAUNCHERS = {"labday": "ground_station/lab/labday.py", "fly": "ground_station/service/campaign_fly.py",
             "lab": "ground_station/lab/__main__.py", "cam": str(TRACKER.relative_to(ROOT)),
             "cfab": "ground_station/service/campaign_fly.py"}  # cfab.cmd calls fly.cmd per ABBA leg (86dea26e)
CMD = re.compile(r"python -m ([a-z_][\w.]*)|\.\\([a-z]+)\b|(camera_truth\.sh)")
FLAG = re.compile(r"(?<![\w-])--[a-z][\w-]*")
PATH = re.compile(r"(?:ground_station|docs)/[\w./-]+\.(?:py|sh|json|md|yaml|csv|cmd)\b")


def module_source(mod: str) -> str:
    p = ROOT.joinpath(*mod.split("."))
    files = sorted(p.glob("*.py")) if p.is_dir() else [p.with_suffix(".py")]
    return "".join(f.read_text(encoding="utf-8") for f in files if f.is_file())


def camera_subcommands() -> set[str]:
    src = CAMERA_SH.read_text(encoding="utf-8")
    return set(re.findall(r"^([a-z]+)\)", src, re.M))


def problems(text: str) -> tuple[list[str], int]:
    """(what is wrong, how many command/flag/key/path checks ran)."""
    bad, n, subs = [], 0, camera_subcommands()
    for line in text.splitlines():
        for m in CMD.finditer(line):
            rest = re.split(r"[`#|]", line[m.end():], maxsplit=1)[0]
            mod, launcher, sh = m.groups()
            if mod:
                src = module_source(mod)
                n += 1
                if not src:
                    bad.append(f"python -m {mod}: no such module")
                    continue
            else:
                if launcher:
                    n += 1
                    if not (ROOT / f"{launcher}.cmd").is_file() or launcher not in LAUNCHERS:
                        bad.append(f".\\{launcher}: no {launcher}.cmd launcher")
                        continue
                if launcher == "cam" or sh:
                    sub = rest.split()[0] if rest.split() else ""
                    if not sub or not sub.isalpha():
                        continue
                    n += 1
                    if sub not in subs:
                        bad.append(f"camera subcommand {sub!r} not in camera_truth.sh")
                    src = TRACKER.read_text(encoding="utf-8")
                else:
                    src = (ROOT / LAUNCHERS[launcher]).read_text(encoding="utf-8")
            for flag in FLAG.findall(rest):
                n += 1
                if not any(f"{quote}{flag}{quote}" in src for quote in ('"', "'")):
                    bad.append(f"{m.group(0)}: flag {flag} not in its code")
    keys = re.search(r"^### 2\.3.*?(?=^###)", text, re.M | re.S)
    tracker = TRACKER.read_text(encoding="utf-8")
    for k in re.findall(r"^\| `(\w)` \|", keys.group(0) if keys else "", re.M):
        n += 1
        if f'ord("{k}")' not in tracker:
            bad.append(f"tracker key {k!r} not handled in live_tracker.py")
    for p in set(PATH.findall(text)):
        n += 1
        if not (ROOT / p).exists():
            bad.append(f"path {p} does not exist")
    return bad, n


def test_commands_doc_matches_the_code():
    bad, n = problems(DOC.read_text(encoding="utf-8"))
    assert not bad, "docs/lab/COMMANDS.md is out of date:\n" + "\n".join(bad)
    assert n >= 60, f"only {n} checks ran: the parser no longer finds the commands"


def test_checker_catches_a_wrong_line():
    text = DOC.read_text(encoding="utf-8") + (
        "\n`.\\fly x --no-such-flag` `.\\cam warp f1` `.\\nolauncher go` `python -m ground_station.nope`"
        " `docs/lab/no-such-file.md`\n")
    bad, _ = problems(text)
    assert len(bad) == 5, bad


@pytest.mark.parametrize("quote", ['"', "'"])
def test_checker_accepts_both_python_flag_quote_styles(monkeypatch, quote):
    source = f"parser.add_argument({quote}--output{quote}, required=True)"
    monkeypatch.setitem(globals(), "module_source", lambda _: source)
    bad, checked = problems("python -m ground_station.lab.z_cf_sim --output result")
    assert not bad and checked == 2
    bad, _ = problems("python -m ground_station.lab.z_cf_sim --missing result")
    assert len(bad) == 1 and "--missing" in bad[0]
