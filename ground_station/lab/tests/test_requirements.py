"""requirements.txt stays true: every package the lab commands import (lazy imports included) is listed there.

2026-10-19: campaign_fly needed psutil to start or check 8081 and no file listed it; a fresh PC failed at the first fly.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENTRY = ["ground_station.lab.labday", "ground_station.service.campaign_fly", "ground_station.lab.__main__",
         "ground_station.service.__main__"]
PIP = {"cv2": "opencv-python", "elftools": "pyelftools", "serial": "pyserial", "yaml": "pyyaml"}  # import -> pip name
CATCH = ("ImportError", "ModuleNotFoundError", "Exception")
NOT_LISTED = {"torch"}  # NN training only, named in requirements.txt's header with its CPU wheel


def _file(mod: str) -> Path | None:
    p = ROOT.joinpath(*mod.split("."))
    return next((f for f in (p.with_suffix(".py"), p / "__init__.py") if f.is_file()), None)


def _guarded(tree: ast.AST) -> set[int]:
    """ids of nodes inside a try whose handler catches ImportError (optional imports such as winpty)."""
    out = set()
    for t in ast.walk(tree):
        if isinstance(t, ast.Try) and any(h.type is None or any(e in ast.unparse(h.type) for e in CATCH)
                                          for h in t.handlers):
            out |= {id(n) for s in t.body for n in ast.walk(s)}
    return out


def third_party() -> dict[str, str]:
    """top-level import name -> first file importing it, over the repo modules reachable from ENTRY."""
    found, seen, todo = {}, set(), list(ENTRY)
    while todo:
        mod = todo.pop()
        f = _file(mod)
        if f is None or f in seen:
            continue
        seen.add(f)
        pkg = mod.split(".")[:-1] if f.name != "__init__.py" else mod.split(".")
        tree = ast.parse(f.read_text(encoding="utf-8"))
        optional = _guarded(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                base = ".".join(pkg[:len(pkg) - n.level + 1] + [n.module or ""]).strip(".") if n.level else n.module
                names = [base] + [f"{base}.{a.name}" for a in n.names]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in ("ground_station", "tools"):
                    todo.append(name)
                elif top not in sys.stdlib_module_names and top != "__future__" and id(n) not in optional:
                    found.setdefault(top, f.relative_to(ROOT).as_posix())
    return found


def test_lab_imports_are_in_requirements():
    listed = {ln.strip().lower() for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
              if ln.strip() and not ln.startswith("#")}
    used = third_party()
    assert "psutil" in used and "numpy" in used  # the walk reaches campaign_fly and the analysis
    missing = {f"{PIP.get(m, m)} ({src})" for m, src in used.items()
               if m not in NOT_LISTED and PIP.get(m, m) not in listed}
    assert not missing, f"add to requirements.txt: {sorted(missing)}"
