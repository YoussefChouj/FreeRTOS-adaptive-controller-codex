#!/usr/bin/env python3
"""Prove a firmware refactor is behaviour-preserving: compare a file at a git ref with the working tree (WP-37).

Both versions are built by host gcc with the Keil include path and defines (USER/JX_FLY.uvprojx), the base one
inside a `git archive` of the ref. The RVDS portmacro.h (armcc __asm) is shadowed by tools/fw_equiv_stubs.
Per file it reports:
  tokens     the preprocessed token streams (comments, layout and macro names gone): `same` proves a
             layout/naming-only change, whatever the compiler.
  code       x86 object code per function at -O2, one section per function: `same` proves the split or
             reordered source still compiles to the same instructions. `only base`/`only new` list functions
             that appeared or disappeared (a helper gcc did not inline).
  data       initialized data per variable (one section each): `same` proves the tables kept their values.
Exit 0 when tokens or (code and data) are the same for every file; 1 otherwise. Never touches OBJ/.

    python tools/fw_equiv.py TASK/StabilizerTask.c API/mrac.c          # vs HEAD
    python tools/fw_equiv.py --base wp/36 TASK/send_data.c --show 40   # diff of each differing function
"""
from __future__ import annotations

import argparse
import difflib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIRS = ["USER", "stm32_lib", "TASK", "Global_file", "BSP", "API", "firmware", "FreeRTOS/include",
        "FreeRTOS/portable/RVDS/ARM_CM4F"]                          # uvprojx IncludePath, same order
ARCHIVE = ["USER", "TASK", "Global_file", "BSP", "API", "firmware"]  # the project's own headers and sources
DEFS = ["-DSTM32F40_41xxx", "-DUSE_STDPERIPH_DRIVER"]
CFLAGS = ["-std=gnu99", "-O2", "-fno-strict-aliasing", "-ffunction-sections", "-fdata-sections",
          "-fno-asynchronous-unwind-tables", "-w"]

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_]\w*|(?:\d|\.\d)(?:[eEpP][+-]|[\w.])*'
                   r'|->|\+\+|--|<<=|>>=|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|\S')


def flags(root: Path) -> list[str]:
    stubs = [f"-I{REPO / 'tools' / 'fw_equiv_stubs'}"]
    return [*stubs, *(f"-I{root / d}" for d in DIRS if (root / d).exists()),
            *(f"-I{REPO / d}" for d in DIRS), *DEFS]


def tokens(gcc: str, root: Path, rel: str) -> list[str]:
    res = subprocess.run([gcc, "-E", "-P", *flags(root), str(root / rel)], capture_output=True, text=True,
                         errors="replace")
    if res.returncode != 0:
        raise SystemExit(f"preprocess {root / rel}:\n{res.stderr[-2000:]}")
    return TOKEN.findall(res.stdout)


SUFFIX = re.compile(r"(\w)\.\d+\b")          # gcc's per-TU counter on static locals: s_prev_ready.8643


def section_bytes(objdump: str, obj: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    cur = None
    for line in subprocess.run([objdump, "-s", str(obj)], capture_output=True, text=True).stdout.splitlines():
        m = re.match(r"^Contents of section (\S+):$", line)
        if m:
            cur = SUFFIX.sub(r"\1", m.group(1))
            out[cur] = b""
        elif cur and re.match(r"^ [0-9a-f]+ ", line):
            out[cur] += bytes.fromhex("".join(line[6:41].split()))
    return out


def pool_width(insn: str) -> int:
    """Bytes a literal-pool operand covers: x87 's'/'l'/'t' suffix, SSE ss/sd; default 4."""
    op = insn.split()[0] if insn.split() else ""
    if op.endswith(("sd", "pd")) or (op.startswith("f") and op.endswith("l")):
        return 8
    if op.startswith("f") and op.endswith("t"):
        return 10
    return 4


def sections(gcc: str, objdump: str, root: Path, rel: str, out: Path) -> tuple[dict, dict]:
    """(function -> normalized disassembly, data variable -> hex bytes) of the -O2 object.

    Normalized: addresses dropped, static-local counters dropped, and every operand that points into a
    read-only pool (.rdata/.rodata + offset) replaced by the bytes it points at, so adding or removing an
    unrelated constant elsewhere in the file does not show up as a code difference."""
    obj = out / (rel.replace("/", "_") + ".o")
    res = subprocess.run([gcc, "-c", *CFLAGS, *flags(root), str(root / rel), "-o", str(obj)],
                         capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        raise SystemExit(f"compile {root / rel}:\n{res.stderr[-2000:]}")
    secs = section_bytes(objdump, obj)
    code: dict[str, list[str]] = {}
    cur = None
    dis = subprocess.run([objdump, "-dr", "--no-show-raw-insn", str(obj)], capture_output=True, text=True).stdout
    for line in dis.splitlines():
        m = re.match(r"^[0-9a-f]+ <(.+)>:$", line)
        if m:
            cur = SUFFIX.sub(r"\1", m.group(1))
            code[cur] = []
        elif cur and line.strip() and not line.startswith("Disassembly"):
            text = SUFFIX.sub(r"\1", re.sub(r"^\s*[0-9a-f]+:\s*", "", line).strip())
            rel_m = re.match(r"^(?:dir32|R_X86_64_\w+|DISP32|IMAGE_REL_\w+)\s+(\.r(?:o)?data\S*)$", text)
            if rel_m and code[cur] and rel_m.group(1) in secs:
                insn = code[cur][-1]
                offs = re.findall(r"0x([0-9a-f]+)", insn)
                if offs:
                    off = int(offs[0], 16)
                    val = secs[rel_m.group(1)][off:off + pool_width(insn)].hex()
                    code[cur][-1] = insn.replace("0x" + offs[0], "<" + val + ">", 1)
                continue
            code[cur].append(text)
    data = {k: v.hex() for k, v in secs.items() if re.match(r"^\.(?:data|rdata|rodata)[$.]", k)}
    return code, data


def compare(name: str, a: dict, b: dict, show: bool) -> bool:
    same = [k for k in a if k in b and a[k] == b[k]]
    diff = [k for k in a if k in b and a[k] != b[k]]
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    ok = not diff and not only_a and not only_b
    print(f"  {name:5} {'same' if ok else 'DIFFER'}: {len(same)} same"
          + (f", differ {diff}" if diff else "") + (f", only base {only_a}" if only_a else "")
          + (f", only new {only_b}" if only_b else ""))
    if show:
        for k in diff:
            lines = list(difflib.unified_diff(a[k], b[k], "base", "new", n=2, lineterm=""))
            print(f"--- {k}: base {len(a[k])} lines, new {len(b[k])} lines, {len(lines)} diff lines")
            print("\n".join("    " + x for x in lines[:show]))
    return ok


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--show", type=int, default=0, metavar="N", help="print the first N unified-diff lines of each differing function")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args(argv)
    gcc, objdump = shutil.which("gcc"), shutil.which("objdump")
    if gcc is None or objdump is None:
        print("FAIL fw_equiv: gcc/objdump not on PATH")
        return 1
    all_ok = True
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        tar = subprocess.run(["git", "archive", args.base, *ARCHIVE], cwd=REPO, capture_output=True, check=True)
        tarfile.open(fileobj=io.BytesIO(tar.stdout)).extractall(base)
        for rel in args.files:
            rel = Path(rel).as_posix()
            print(rel)
            tok_same = tokens(gcc, base, rel) == tokens(gcc, REPO, rel)
            print(f"  tokens {'same' if tok_same else 'DIFFER'}")
            ca, da = sections(gcc, objdump, base, rel, base)
            cb, db = sections(gcc, objdump, REPO, rel, Path(tmp))
            code_ok = compare("code", ca, cb, args.show)
            data_ok = compare("data", da, db, args.show)
            all_ok &= tok_same or (code_ok and data_ok)
    print("FW-EQUIV OK" if all_ok else "FW-EQUIV DIFFER")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
