"""Build a symbol image of the current firmware sources: an ARM ELF whose DWARF names every global, for the soak.

The lab names streams out of OBJ/JX_FLY.axf. That file is Keil output and only exists where Keil runs; a cloud or
Linux checkout carries whatever axf was last committed, which can predate the variables today's lab code asks for
(the preflight then fails "unknown symbol 'g_wfb_status'"). This module compiles the Keil project's own C file list
(USER/JX_FLY.uvprojx) with clang for the Cortex-M4 target and links it with ld.lld. The result is never flashed and
never runs: it only gives `SymbolResolver` the current names, types and a consistent set of addresses, which is all
ground_station/lab/fake_drone.py and the 8081 service need to agree on (both read it through LAB_FIRMWARE_ELF).

Differences from Keil are kept out of the layout: same target, float ABI, defines and include order as the project,
`-fshort-enums` like armcc. `check_layout` compares every struct both images define against an older Keil axf.
A file clang rejects as written (RVDS `__asm` functions and `__asm{}` blocks, armcc's `static` after `extern`) is
preprocessed and recompiled with only those constructs rewritten (`desugar`): asm bodies become empty, no data
declaration changes. That file is listed in desugared.txt. A file that still fails is skipped and listed in
skipped.txt; a name the lab needs from one of those files still fails the preflight by name, so a gap cannot pass
silently.

    python -m ground_station.lab.symbol_image [--out DIR] [--check OBJ/JX_FLY.axf]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "USER" / "JX_FLY.uvprojx"
DEFAULT_OUT = ROOT / ".cache" / "symbol_image"
ENV = "LAB_FIRMWARE_ELF"

CFLAGS = ["--target=arm-none-eabi", "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16",
          "-fshort-enums", "-std=gnu99", "-ffreestanding", "-fno-common", "-gdwarf-4", "-O0", "-w",
          # armcc accepts these; the image never runs, so clang may too.
          "-Wno-error=implicit-function-declaration", "-Wno-error=int-conversion",
          "-Wno-error=incompatible-pointer-types", "-Wno-error=implicit-int",
          "-D__CORE_CMFUNC_H",          # CMSIS core_cmFunc.h: armcc register asm clang cannot parse; no data in it
          "-D__TARGET_FPU_VFP"]         # armcc predefines it for --fpu=FPv4-SP (the RVDS port.c #errors without it)

# The RVDS port header is armcc asm; these are its types and the macros headers expand (portable/RVDS/ARM_CM4F).
PORTMACRO = """#ifndef PORTMACRO_H
#define PORTMACRO_H
#include <stdint.h>
#define portCHAR char
#define portFLOAT float
#define portDOUBLE double
#define portLONG long
#define portSHORT short
#define portSTACK_TYPE uint32_t
#define portBASE_TYPE long
typedef uint32_t StackType_t;
typedef long BaseType_t;
typedef unsigned long UBaseType_t;
typedef uint32_t TickType_t;
#define portMAX_DELAY (TickType_t)0xffffffffUL
#define portTICK_TYPE_IS_ATOMIC 1
#define portSTACK_GROWTH (-1)
#define portTICK_PERIOD_MS ((TickType_t)1000 / configTICK_RATE_HZ)
#define portBYTE_ALIGNMENT 8
#define portYIELD()
#define portEND_SWITCHING_ISR(x)
#define portYIELD_FROM_ISR(x)
#define portENTER_CRITICAL()
#define portEXIT_CRITICAL()
#define portDISABLE_INTERRUPTS()
#define portENABLE_INTERRUPTS()
#define portSET_INTERRUPT_MASK_FROM_ISR() 0
#define portCLEAR_INTERRUPT_MASK_FROM_ISR(x) (void)(x)
#define portTASK_FUNCTION_PROTO(f, p) void f(void *p)
#define portTASK_FUNCTION(f, p) void f(void *p)
#define portNOP()
#define portINLINE inline
#define portFORCE_INLINE inline
#define portRECORD_READY_PRIORITY(p, r) ((r) |= (1UL << (p)))
#define portRESET_READY_PRIORITY(p, r) ((r) &= ~(1UL << (p)))
#define portGET_HIGHEST_PRIORITY(t, r) ((t) = 31UL - (uint32_t)__builtin_clz(r))
#define portSUPPRESS_TICKS_AND_SLEEP(x)
#define portASSERT_IF_INTERRUPT_PRIORITY_INVALID()
#endif
"""
# No libc for the target here: headers only need to exist (calls become implicit declarations; nothing runs).
LIBC = {"stdio.h": "typedef struct __FILE FILE;\n#define EOF (-1)\n", "time.h": "typedef long time_t;\n"}

LINKER_SCRIPT = """SECTIONS {
  . = 0x08000000; .text : { *(.text*) *(.rodata*) }
  . = 0x10000000; MRAC_CCM (NOLOAD) : { *(MRAC_CCM) }
  . = 0x20000000; .data : { *(.data*) } .bss (NOLOAD) : { *(.bss*) *(COMMON) }
}
"""


@dataclass
class Build:
    elf: Path
    compiled: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)     # source -> first error line
    cached: bool = False
    desugared: list[str] = field(default_factory=list)        # compiled from a desugared preprocessed copy


def _block_end(text: str, i: int) -> int:
    """Index just past the brace block that opens at text[i] == '{'."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    raise ValueError("unbalanced braces")


_ASM_FUNC = re.compile(r"\b__asm\b(\s+[A-Za-z_][\w\s\*]*\([^()]*\))\s*\{")    # __asm void f(u32 a) { ... }
_ASM_BLOCK = re.compile(r"\b__asm\s*\{")                                        # __asm{NOP;} statement


def desugar(text: str) -> str:
    """Rewrite the armcc-only constructs of preprocessed C into what clang accepts, leaving every data declaration
    as it is: an embedded-asm function keeps its signature with an empty body, an `__asm{}` statement becomes `;`."""
    while m := _ASM_FUNC.search(text):
        text = text[:m.start()] + m.group(1).strip() + " {}" + text[_block_end(text, m.end() - 1):]
    while m := _ASM_BLOCK.search(text):
        text = text[:m.start()] + ";" + text[_block_end(text, m.end() - 1):]
    return text


_STATIC_AFTER_EXTERN = re.compile(r":(\d+):\d+: error: static declaration of '(\w+)' follows non-static")


def _drop_static(text: str, err: str) -> str | None:
    """armcc keeps `extern T x;` then `static T x;` (one object, internal linkage); clang rejects it. Drop that
    `static` on the reported line so the object still gets its DWARF entry. None when nothing applies."""
    lines = text.split("\n")
    hit = False
    for ln, name in _STATIC_AFTER_EXTERN.findall(err):
        k = int(ln) - 1
        new = re.sub(r"\bstatic\s+(?=[^;=]*\b" + name + r"\b)", "", lines[k], count=1)
        hit |= new != lines[k]
        lines[k] = new
    return "\n".join(lines) if hit else None


def project_sources(project: Path = PROJECT) -> tuple[list[Path], list[Path], list[str]]:
    """(C files, include dirs, defines) of the Keil target, in project order. Paths in the project are ..\\-relative
    to USER/."""
    tree = ET.parse(project)
    base = project.parent

    def rel(p: str) -> Path:
        return (base / p.replace("\\", "/")).resolve()

    sources = [rel(e.text) for e in tree.iter("FilePath") if e.text and e.text.lower().endswith(".c")]
    inc = next((e.text for e in tree.iter("IncludePath") if e.text and e.text.strip()), "")
    includes = [rel(p) for p in inc.split(";") if p.strip()]
    dfn = next((e.text for e in tree.iter("Define") if e.text and e.text.strip()), "")
    defines = [d.strip() for d in dfn.split(",") if d.strip()]
    return list(dict.fromkeys(sources)), includes, defines


_LLVM_WIN = r"C:\Program Files\LLVM\bin"  # the LLVM Windows installer does not add itself to PATH


def _tool(name: str) -> str:
    path = shutil.which(name) or (os.name == "nt" and shutil.which(name, path=_LLVM_WIN))
    if not path:
        raise RuntimeError(f"{name} not found: the symbol image needs clang and ld.lld "
                           f"(apt install clang lld, or LLVM for Windows in {_LLVM_WIN})")
    return path


def _fingerprint(sources: list[Path], includes: list[Path]) -> str:
    """Hash of every source and header the build can read, plus this module (flags, shims)."""
    h = hashlib.sha256(Path(__file__).read_bytes())
    files = set(sources)
    for d in includes:
        if d.is_dir():
            files.update(d.glob("*.h"))
    for f in sorted(files):
        if f.exists():
            h.update(str(f.relative_to(ROOT) if f.is_relative_to(ROOT) else f).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _casefold_dir(includes: list[Path], shim: Path, err: str) -> bool:
    """Keil on Windows finds `#include "GlobalUse_Basic_Function.h"` for globaluse_basic_function.h. Link the
    miss into a shim dir under the name asked for. False when the file does not exist in any case."""
    m = re.search(r"'([^']+)' file not found", err)
    if not m:
        return False
    want = m.group(1)
    for d in includes:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.name.lower() == Path(want).name.lower():
                (shim / want).parent.mkdir(parents=True, exist_ok=True)
                (shim / want).unlink(missing_ok=True)
                (shim / want).symlink_to(f)
                return True
    return False


_CCM_MACRO = re.compile(r'#\s*define\s+(\w+)\s+__attribute__\s*\(\(\s*section\s*\(\s*"MRAC_CCM"')


def keep_ccm(text: str) -> str:
    """Give the non-armcc branch of each MRAC_CCM tag macro the same section, so USER/JX_FLY.sct's CCM placement
    holds in the image too. The armcc branch adds zero_init, which only NOLOAD placement needs here. Only empty
    `#define NAME` lines are rewritten, one for one, so line numbers stay put."""
    for name in set(_CCM_MACRO.findall(text)):
        text = re.sub(rf"^([ \t]*#[ \t]*define[ \t]+{name})[ \t]*$", r'\1 __attribute__((section("MRAC_CCM")))',
                      text, flags=re.M)
    return text


def build(out: Path = DEFAULT_OUT, project: Path = PROJECT, force: bool = False) -> Build:
    """Compile and link the image into out/JX_FLY_sym.elf; reuse it when no input changed."""
    clang, lld = _tool("clang"), _tool("ld.lld")
    sources, includes, defines = project_sources(project)
    # The RVDS port dir goes after the shim: its portmacro.h is armcc asm.
    includes_c = [d for d in includes if "RVDS" not in d.parts]
    out.mkdir(parents=True, exist_ok=True)
    fp = _fingerprint(sources, includes)
    elf = out / "JX_FLY_sym.elf"
    stamp = out / "fingerprint.txt"
    report = out / "skipped.txt"
    if not force and elf.exists() and stamp.exists() and stamp.read_text().strip() == fp:
        skipped = dict(line.split("\t", 1) for line in report.read_text().splitlines() if "\t" in line) \
            if report.exists() else {}
        sugar = out / "desugared.txt"
        return Build(elf, [], skipped, cached=True,
                     desugared=sugar.read_text().split() if sugar.exists() else [])

    shim = out / "shim"
    if shim.exists():
        shutil.rmtree(shim)
    (shim / "fold").mkdir(parents=True)
    (shim / "port").mkdir()
    (shim / "libc").mkdir()
    (shim / "port" / "portmacro.h").write_text(PORTMACRO)
    for name, text in LIBC.items():
        (shim / "libc" / name).write_text(text)
    objs = out / "obj"
    if objs.exists():
        shutil.rmtree(objs)
    objs.mkdir()

    flags = CFLAGS + [f"-D{d}" for d in defines] + ["-I" + str(shim / "port"), "-I" + str(shim / "fold")]
    flags += ["-I" + str(d) for d in includes_c] + ["-I" + str(shim / "libc")]
    result = Build(elf)
    for src in sources:
        name = str(src.relative_to(ROOT)) if src.is_relative_to(ROOT) else str(src)
        obj = objs / (re.sub(r"[^A-Za-z0-9_]", "_", name) + ".o")
        raw = src.read_text(errors="replace")
        if (ccm := keep_ccm(raw)) != raw:                   # compile a CCM-tagged copy; "x.h" still resolves
            flags_src = flags + ["-iquote", str(src.parent)]
            src = obj.with_suffix(".ccm.c")
            src.write_text(ccm)
        else:
            flags_src = flags
        for _ in range(20):
            p = subprocess.run([clang, *flags_src, "-c", str(src), "-o", str(obj)], capture_output=True, text=True)
            if p.returncode == 0:
                break
            if "file not found" in p.stderr:
                missing = re.search(r"'([^']+)' file not found", p.stderr)
                if _casefold_dir(includes, shim / "fold", p.stderr):
                    continue
                if missing and "/" not in missing.group(1):       # a libc header this target lacks
                    (shim / "libc" / missing.group(1)).write_text("")
                    continue
            break
        if p.returncode != 0 and "file not found" not in p.stderr:
            # -P: no line markers, so clang's error lines index the desugared text itself.
            pp = subprocess.run([clang, *flags_src, "-E", "-P", str(src)], capture_output=True, text=True)
            if pp.returncode == 0:
                text = desugar(pp.stdout)
                ifile = obj.with_suffix(".i")
                for _ in range(20):
                    ifile.write_text(text)
                    q = subprocess.run([clang, *CFLAGS, "-x", "cpp-output", "-c", str(ifile), "-o", str(obj)],
                                       capture_output=True, text=True)
                    if q.returncode == 0 or (text := _drop_static(text, q.stderr)) is None:
                        break
                if q.returncode == 0:
                    p = q
                    result.desugared.append(name)
        if p.returncode == 0:
            result.compiled.append(name)
        else:
            first = next((ln for ln in p.stderr.splitlines() if "error" in ln), p.stderr.strip()[:200])
            result.skipped[name] = first.replace(str(ROOT) + "/", "").strip()
    if not result.compiled:
        raise RuntimeError("symbol image: no source compiled")
    (out / "link.ld").write_text(LINKER_SCRIPT)
    tmp = elf.with_suffix(".tmp")
    p = subprocess.run([lld, "-nostdlib", "--unresolved-symbols=ignore-all", "--noinhibit-exec", "-e", "0",
                        "-T", str(out / "link.ld"), "-o", str(tmp), *sorted(str(o) for o in objs.glob("*.o"))],
                       capture_output=True, text=True)
    if not tmp.exists():
        raise RuntimeError(f"symbol image link failed: {p.stderr.strip()[:500]}")
    tmp.replace(elf)
    report.write_text("".join(f"{k}\t{v}\n" for k, v in result.skipped.items()))
    (out / "desugared.txt").write_text("".join(f"{k}\n" for k in result.desugared))
    stamp.write_text(fp + "\n")
    return result


def _struct_sizes(elf: Path) -> dict[str, int]:
    """Byte size of every named struct/union/typedef-of-struct in the image's DWARF."""
    from elftools.elf.elffile import ELFFile
    sizes: dict[str, int] = {}
    with open(elf, "rb") as fh:
        dwarf = ELFFile(fh).get_dwarf_info()
        for cu in dwarf.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type", "DW_TAG_typedef",
                                   "DW_TAG_enumeration_type"):
                    continue
                name = die.attributes.get("DW_AT_name")
                if not name:
                    continue
                target = die
                if die.tag == "DW_TAG_typedef":
                    t = die.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in die.attributes else None
                    if t is None or t.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type",
                                                  "DW_TAG_enumeration_type"):
                        continue
                    target = t
                size = target.attributes.get("DW_AT_byte_size")
                if size is not None:
                    sizes.setdefault(name.value.decode(errors="replace"), size.value)
    return sizes


def check_layout(image: Path, reference: Path) -> tuple[int, list[tuple[str, int, int]]]:
    """(types compared, [(type, image size, reference size)] that differ). A type that changed in the sources since
    the reference was built differs for a real reason; the check is for whole-image drift (enum width, packing)."""
    a, b = _struct_sizes(image), _struct_sizes(reference)
    common = sorted(set(a) & set(b))
    return len(common), [(n, a[n], b[n]) for n in common if a[n] != b[n]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="rebuild even when no input changed")
    ap.add_argument("--check", type=Path, help="compare struct sizes against this Keil axf")
    args = ap.parse_args(argv)
    b = build(args.out, force=args.force)
    print(f"{b.elf}{' (cached)' if b.cached else ''}: {len(b.compiled) or '?'} files compiled, "
          f"{len(b.desugared)} of them desugared, {len(b.skipped)} skipped")
    for k in b.desugared:
        print(f"  desugared {k}")
    for k, v in b.skipped.items():
        print(f"  skipped {k}: {v}")
    if args.check:
        n, diff = check_layout(b.elf, args.check)
        print(f"layout vs {args.check}: {n} types compared, {len(diff)} differ")
        for name, x, y in diff:
            print(f"  {name}: image {x} B, reference {y} B")
    print(f"use it: {ENV}={b.elf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
