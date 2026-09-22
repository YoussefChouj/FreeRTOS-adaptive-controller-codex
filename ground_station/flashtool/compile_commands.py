"""Generate compile_commands.json from the Keil project (for clangd / Serena).

Parses ``USER/JX_FLY.uvprojx`` (the Keil MDK project file) and turns it into a
standard ``compile_commands.json`` so an editor / LSP (clangd, the Serena
frontend) can index the bare-metal Cortex-M4 sources without a real ARMCC
invocation.

What it extracts, for the ``JX_FLY`` target:

* every ``<File>`` whose ``<FileType>`` is 1 (a C source) from inside
  ``<Groups>``;
* the C include directories (``Cads/VariousControls/IncludePath``, ``;``
  separated, each relative to the ``USER/`` directory) and the preprocessor
  defines (``Cads/VariousControls/Define``, comma- or space-separated).

Each C file becomes one entry whose ``arguments`` mirror the fixed flags the
project otherwise gets from ARMCC, plus ``-D`` per define and ``-I`` per include
directory.

Paths are normalised to forward slashes. ``--windows-paths`` rewrites a WSL
``/mnt/c/...`` prefix to ``C:/...`` so a file generated in WSL is usable by a
Windows clangd.

Example::

    python -m ground_station.flashtool.compile_commands --windows-paths

writes ``compile_commands.json`` at the repo root and prints
``wrote N entries (M missing) -> F``.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple
from xml.etree import ElementTree

TARGET_NAME = "JX_FLY"
DEFAULT_KEIL_INC = "C:/Keil_v5/ARM/ARMCC/include"

# clang flags that let clangd parse this ARMCC project. __CC_ARM is left
# undefined on purpose: defining it pulls in ARMCC-only CMSIS inline asm, so
# clang takes the __GNUC__ paths instead. The Keil libc headers still need
# __declspec (-fms-extensions) and __value_in_regs stubbed out.
FIXED_FLAGS = [
    "clang",
    "--target=arm-none-eabi",
    "-mcpu=cortex-m4",
    "-mthumb",
    "-std=gnu99",
    "-ffreestanding",
    "-fms-extensions",
    "-D__value_in_regs=",
    "-ferror-limit=0",
]


def _repo_root() -> Path:
    """Repo root derived from this file (ground_station/flashtool/…)."""
    return Path(__file__).resolve().parent.parent.parent


def _find_target(root: ElementTree.Element, name: str) -> ElementTree.Element:
    """Return the ``<Target>`` whose ``<TargetName>`` is ``name``."""
    for target in root.iter("Target"):
        if target.findtext("TargetName", "").strip() == name:
            return target
    raise LookupError(f"no target named {name!r} in the Keil project")


def _split_defines(raw: str) -> List[str]:
    """Split a Define value on commas or whitespace."""
    return [tok for tok in raw.replace(",", " ").split() if tok]


def _split_includes(raw: str) -> List[str]:
    """Split an IncludePath value on ';'."""
    return [p.strip() for p in raw.split(";") if p.strip()]


def parse_keil_project(
    uvp: Path, target_name: str = TARGET_NAME
) -> Tuple[List[str], List[str], List[str]]:
    """Parse ``uvp`` and return ``(file_paths, defines, includes)``.

    ``file_paths`` are the bare ``..\\TASK\\led.c``-style paths from the project;
    ``defines`` and ``includes`` the raw tokens from ``Cads/VariousControls``.
    """
    tree = ElementTree.parse(str(uvp))
    target = _find_target(tree.getroot(), target_name)

    files = [
        f_el.findtext("FilePath", "").strip()
        for f_el in target.iter("File")
        if f_el.findtext("FileType", "").strip() == "1"
        and f_el.findtext("FilePath", "").strip()
    ]

    # C <VariousControls> lives under TargetArmAds; find it anywhere in the target.
    vc = target.find(".//Cads/VariousControls")
    defines = _split_defines(vc.findtext("Define", "") if vc is not None else "")
    includes = _split_includes(
        vc.findtext("IncludePath", "") if vc is not None else ""
    )
    return files, defines, includes


def build_entries(
    user_dir: Path,
    files: List[str],
    defines: List[str],
    includes: List[str],
    keil_inc: str = DEFAULT_KEIL_INC,
) -> Tuple[List[dict], List[str]]:
    """Return ``(entries, missing)`` for the given C files.

    ``user_dir`` is the ``USER/`` directory each project-relative path is
    resolved against. Files that do not exist on disk are reported, not added.
    """
    abs_includes = [_posix(user_dir / inc) for inc in includes]
    user_dir_str = _posix(user_dir)

    entries: List[dict] = []
    missing: List[str] = []
    for rel in files:
        abs_file = user_dir / rel
        if not abs_file.is_file():
            missing.append(str(abs_file))
            continue
        file_str = _posix(abs_file)
        arguments = list(FIXED_FLAGS)
        arguments.extend(f"-D{d}" for d in defines)
        arguments.extend(f"-I{i}" for i in abs_includes)
        arguments.append(f"-I{keil_inc}")
        arguments.extend(["-c", file_str])
        entries.append(
            {
                "directory": user_dir_str,
                "file": file_str,
                "arguments": arguments,
            }
        )
    return entries, missing


def _posix(p: Path) -> str:
    """Return an absolute forward-slash path (works on WSL and Windows)."""
    return str(p.resolve()).replace("\\", "/")


def _to_windows(p: str) -> str:
    """Rewrite a WSL /mnt/c/... path to a Windows C:/... path."""
    return "C:/" + p[len("/mnt/c/"):] if p.startswith("/mnt/c/") else p


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate compile_commands.json from USER/JX_FLY.uvprojx."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root (default: derived from this file's location)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: <root>/compile_commands.json)",
    )
    parser.add_argument(
        "--keil-inc",
        default=DEFAULT_KEIL_INC,
        help="Keil ARMCC include directory",
    )
    parser.add_argument(
        "--windows-paths",
        action="store_true",
        help="Rewrite /mnt/c/... to C:/... in all output paths",
    )
    args = parser.parse_args(argv)

    root = args.root or _repo_root()
    uvp = root / "USER" / "JX_FLY.uvprojx"
    if not uvp.is_file():
        print(f"error: Keil project not found: {uvp}", file=sys.stderr)
        return 1

    files, defines, includes = parse_keil_project(uvp)
    user_dir = root / "USER"
    entries, missing = build_entries(
        user_dir, files, defines, includes, keil_inc=args.keil_inc
    )

    if args.windows_paths:
        for e in entries:
            e["directory"] = _to_windows(e["directory"])
            e["file"] = _to_windows(e["file"])
            e["arguments"] = [_to_windows(a) for a in e["arguments"]]

    out_path = args.out or (root / "compile_commands.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")

    print(f"wrote {len(entries)} entries ({len(missing)} missing) -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())