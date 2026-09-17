"""Build identity stamping and verification.

Every relink moves RAM symbol addresses — measured 2026-07-26:
``DroneStatus`` shifted from ``0x20016138`` to ``0x200161d8`` (160 B). livewatch
resolves names to addresses out of ``OBJ/JX_FLY.axf``, and the safety gate in
``safe_flash.py`` reads ``DroneStatus.ARM_Status`` through that resolution. If
the local ELF is a fresh build that has not been flashed, the gate's own
decision would be made on garbage.

The build identity is a 4-word tuple stamped into the firmware image itself:

  ``build_id[0] = MAGIC (0xB10DCAFE)``       — recognisable prefix
  ``build_id[1] = build_counter``            — monotonic, incremented per build
  ``build_id[2] = build_epoch_seconds``      — when this build was emitted
  ``build_id[3] = source_fingerprint``       — SHA-256 of source-file mtimes/sizes

A build counter avoids the chicken-and-egg problem a content hash would have:
counter N+1's ELF always carries counter N+1 as ``build_id[1]``, regardless of
whether the source changed or only the link order moved. A source fingerprint
catches "the developer edited something but forgot to rebuild" cases. Together
they prove "this ELF was produced by build N+1 at time T, from sources with
fingerprint F" — enough to catch every stale-ELF scenario the 2026-07-26
12-byte .bss drift would have silently corrupted.

Stamping works by:

1. Generate ``OBJ/build_id.c`` containing ``volatile uint32_t build_id[4]``
   with the four constants above.
2. Add the file to ``USER/JX_FLY.uvprojx`` under a dedicated ``<Group>``
   named ``BUILDTOOLS``.
3. Build (UV4 picks the file up; the symbol resolves in DWARF).
4. Remove the ``<Group>`` and delete the file on context exit, byte-exact.

At runtime ``build_id[0..3]`` lives in ``.data`` (initialised to the literal
at startup, then RAM-resident). ``check_identity()`` reads it through the
existing ``LiveReader`` and compares against the locally-recorded identity
for the ELF on disk. A match proves the local ELF describes the firmware
actually flashed on the target.

The mechanism is intentionally simple — no compiler/linker trickery, no new
firmware C source change (out of scope → spec 3). The stamp is the build
artifact itself; the host needs no separate "build_id.txt" alongside.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


#: First word of ``build_id`` — magic prefix so the host can tell the symbol
#: was stamped by this pipeline rather than being a coincidental match. 0xB10DCAFE
#: ("build, dawg") — unambiguous and unlikely to collide with any real address.
MAGIC = 0xB10DCAFE

#: Names of the four-element symbol that ends up in DWARF.
BUILD_ID_SYMBOL = "build_id"

#: Group name added to ``uvprojx`` during the build. Kept under a single,
#: obviously-not-user code name so the entry is trivially identifiable when
#: restoring the file (and so a stale context exit cannot leave the user's
#: project tree contaminated).
_BUILD_ID_GROUP_NAME = "BUILDTOOLS"

#: FileName / FilePath values written into the temporary ``<File>`` entry.
_BUILD_ID_FILENAME = "build_id.c"
_BUILD_ID_FILEPATH_RELATIVE = "..\\OBJ\\build_id.c"

#: On-disk file that holds the next build counter to be emitted. Lives next
#: to the build artifacts so a checkout of the repo on a fresh machine starts
#: from zero (not from a stale CI value). Gitignored alongside the rest of
#: ``OBJ/`` (the project-wide gitignore excludes the directory wholesale).
_BUILD_COUNTER_FILENAME = ".build_counter"

# ARMCC/Keil AXF files are not ELF files, so the initialized identity cannot
# be recovered from the artifact with pyelftools. Keep the exact identity
# allocated for the build in a small sidecar next to the AXF. This is written
# atomically before UV4 starts and is covered by artifact custody.
_BUILD_ID_METADATA_FILENAME = ".build_identity.json"

#: Source roots walked to compute the source fingerprint. Order matters only
#: for hash determinism, not correctness — two passes of the same tree at
#: the same mtimes produce the same hash.
_SOURCE_ROOTS = ("USER", "API", "BSP", "TASK", "Global_file", "FreeRTOS")


@dataclass(frozen=True)
class Identity:
    """The four 32-bit words that get stamped into the firmware image."""

    magic: int
    build_counter: int       # monotonic; ``read_counter()`` writes then reads this
    build_epoch: int         # unix seconds when this build was emitted
    source_fingerprint: int  # truncated SHA-256 of the source tree at build time

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.magic, self.build_counter, self.build_epoch, self.source_fingerprint)

    def short_label(self) -> str:
        """A short, human-readable label for log lines: ``counter=epoch=fingerprint``."""
        return f"counter={self.build_counter} epoch={self.build_epoch} " \
               f"fingerprint=0x{self.source_fingerprint:08X}"


# ---- source tree fingerprint ----------------------------------------------

def _source_fingerprint(root: Path) -> int:
    """SHA-256 of (relative-path, mtime, size) for every file under each
    source root, truncated to 32 bits. Two source trees at the same mtimes
    produce the same fingerprint; any edit moves it."""
    h = hashlib.sha256()
    for sub in _SOURCE_ROOTS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            rel = str(path.relative_to(root)).replace(os.sep, "/")
            h.update(rel.encode("utf-8"))
            h.update(struct.pack("<QQ", int(st.st_mtime), st.st_size))
    return struct.unpack("<I", h.digest()[:4])[0]


# ---- counter persistence --------------------------------------------------

def _read_counter(obj_dir: Path) -> int:
    """Read the next build counter from disk; default to 0 if absent."""
    p = obj_dir / _BUILD_COUNTER_FILENAME
    try:
        text = p.read_text().strip()
    except OSError:
        return 0
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _write_counter(obj_dir: Path, value: int) -> None:
    """Persist the next build counter. Atomic via tempfile + rename."""
    p = obj_dir / _BUILD_COUNTER_FILENAME
    fd, name = tempfile.mkstemp(prefix=".build_counter.", suffix=".tmp", dir=obj_dir)
    try:
        with open(fd, "w", encoding="ascii") as f:
            f.write(str(value))
        Path(name).replace(p)
    except OSError:
        Path(name).unlink(missing_ok=True)
        raise


def _write_identity_metadata(obj_dir: Path, identity: Identity) -> None:
    """Persist the identity associated with the next build atomically."""
    p = obj_dir / _BUILD_ID_METADATA_FILENAME
    fd, name = tempfile.mkstemp(prefix=".build_identity.", suffix=".tmp", dir=obj_dir)
    try:
        with open(fd, "w", encoding="ascii", newline="\n") as f:
            json.dump({"words": list(identity.as_tuple())}, f, separators=(",", ":"))
            f.write("\n")
        Path(name).replace(p)
    except OSError:
        Path(name).unlink(missing_ok=True)
        raise


def next_identity(obj_dir: str | Path, root: str | Path | None = None) -> Identity:
    """Allocate the next identity: increment the counter, stamp the time +
    source fingerprint. The counter is persisted before the build so a
    crash mid-link cannot leave the next build thinking it already emitted
    this counter."""
    obj_dir = Path(obj_dir)
    counter = _read_counter(obj_dir) + 1
    _write_counter(obj_dir, counter)
    epoch = int(time.time())
    if root is None:
        root = obj_dir.parent
    fp = _source_fingerprint(Path(root))
    identity = Identity(magic=MAGIC, build_counter=counter, build_epoch=epoch,
                        source_fingerprint=fp)
    _write_identity_metadata(obj_dir, identity)
    return identity


def identity_from_elf(elf_path: str | Path, obj_dir: str | Path | None = None
                      ) -> Identity:
    """Recover the initialized build-id words from an ELF or Keil sidecar.

    ARMCC V5 AXF files are valid Keil images but are not ELF, so parsing them
    as ELF is expected to fail. The sidecar written by :func:`next_identity`
    is authoritative for those artifacts. The counter fallback is retained
    for old workspaces and the lightweight fake-AXF tests.
    """
    elf_path = Path(elf_path)
    if obj_dir is None:
        obj_dir = elf_path.parent
    obj_dir = Path(obj_dir)
    elf_error: Exception | None = None
    try:
        from elftools.elf.elffile import ELFFile
        with elf_path.open("rb") as stream:
            elf = ELFFile(stream)
            symtab = elf.get_section_by_name(".symtab")
            symbols = symtab.get_symbol_by_name(BUILD_ID_SYMBOL) if symtab else None
            if not symbols:
                raise ValueError(f"{BUILD_ID_SYMBOL!r} not found")
            address = int(symbols[0]["st_value"])
            for section in elf.iter_sections():
                start = int(section["sh_addr"])
                size = int(section["sh_size"])
                if start <= address and address + 16 <= start + size:
                    offset = address - start
                    words = struct.unpack_from("<4I", section.data(), offset)
                    return Identity(*words)
    except Exception as exc:
        elf_error = exc

    metadata = obj_dir / _BUILD_ID_METADATA_FILENAME
    try:
        raw = json.loads(metadata.read_text(encoding="ascii"))
        words = raw["words"]
        if len(words) != 4:
            raise ValueError("identity sidecar must contain four words")
        return Identity(*(int(word) & 0xFFFFFFFF for word in words))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass

    # Legacy artifacts predate the sidecar. They only carried the monotonic
    # counter on disk, so preserve the historical best-effort reconstruction.
    counter = _read_counter(obj_dir)
    if counter:
        return Identity(MAGIC, counter, 0, 0)
    detail = f": {elf_error}" if elf_error else ""
    raise ValueError(f"cannot recover {BUILD_ID_SYMBOL} from {elf_path}{detail}")


# ---- generated C source ---------------------------------------------------

# Generated as a single const-qualified global. `volatile` so the compiler
# does not constant-fold the array away — the symbol must end up in .data
# (initialised RAM) and be readable at a fixed address by livewatch.
_GENERATED_C_TEMPLATE = """\
/* AUTO-GENERATED by ground_station.flashtool.build_id. Do not edit.
 * This file is rewritten on every build; the embedded identity is the
 * four-word tuple (magic, build_counter, build_epoch, source_fingerprint)
 * stamped by flashtool.build_id.next_identity() at the moment of build. */
#include <stdint.h>

/* `used` keeps the linker from GC-ing this object even though no C code
 * references it -- the symbol only needs to be live in the ELF so the
 * host-side safety gate can read it through livewatch. ARMCC V5.06
 * honours the attribute. */
__attribute__((used)) volatile uint32_t build_id[4] = {{
    0x{magic:08X}u, 0x{counter:08X}u, 0x{epoch:08X}u, 0x{fingerprint:08X}u
}};
"""


def generate_c_source(identity: Identity) -> str:
    """Render the C source that, once compiled, exposes ``build_id[4]``."""
    return _GENERATED_C_TEMPLATE.format(
        magic=identity.magic,
        counter=identity.build_counter,
        epoch=identity.build_epoch,
        fingerprint=identity.source_fingerprint,
    )


# ---- identity check at runtime -------------------------------------------

@dataclass(frozen=True)
class IdentityCheck:
    """The verdict of comparing local ELF vs. running firmware identity."""

    ok: bool
    expected: Identity
    observed: tuple[int, int, int, int] | None     # raw 4-word read from target
    reasons: list[str]                             # populated on mismatch / read failure

    def report(self) -> str:
        head = "build identity MATCH" if self.ok else "build identity MISMATCH"
        lines = [f"[identity] {head}",
                 f"    expected {self.expected.short_label()}",
                 f"    observed {('n/a' if self.observed is None else self._format_observed())}"]
        for r in self.reasons:
            lines.append(f"    ! {r}")
        return "\n".join(lines)

    def _format_observed(self) -> str:
        if self.observed is None:
            return "n/a"
        _m, counter, epoch, fp = self.observed
        return f"counter={counter} epoch={epoch} fingerprint=0x{fp:08X}"


def check_identity(elf_path: str | Path) -> IdentityCheck:
    """Read ``build_id[0..3]`` from the running target and compare to local ELF.

    Defer-imports ``livewatch`` so this module stays importable in environments
    where pyOCD is not installed (e.g. pure-Python unit tests that only check
    offline behaviour).
    """
    from ground_station.livewatch.reader import LiveReader

    expected = identity_from_elf(elf_path)
    try:
        with LiveReader(elf_path) as lr:
            plan = lr.plan([BUILD_ID_SYMBOL])
            raw = lr.sample(plan)[BUILD_ID_SYMBOL]
    except Exception as exc:
        return IdentityCheck(
            ok=False, expected=expected, observed=None,
            reasons=[f"could not read {BUILD_ID_SYMBOL} from target: {exc}"],
        )
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) != 16:
            return IdentityCheck(
                ok=False, expected=expected, observed=None,
                reasons=[f"{BUILD_ID_SYMBOL} read returned {len(raw)} bytes, expected 16"],
            )
        observed = struct.unpack("<4I", raw)
    else:
        observed = tuple(raw)
    reasons: list[str] = []
    if observed[0] != MAGIC:
        reasons.append(
            f"{BUILD_ID_SYMBOL}[0] on target is 0x{observed[0]:08X}, expected magic "
            f"0x{MAGIC:08X} — symbol was not stamped by this pipeline (or the running "
            f"firmware pre-dates the stamping feature)"
        )
    elif observed != expected.as_tuple():
        reasons.append(
            f"{BUILD_ID_SYMBOL} on target {observed} does not match local ELF "
            f"identity {expected.as_tuple()} — the running firmware does NOT match "
            f"OBJ/JX_FLY.axf on disk; refuse any decision that depends on this ELF."
        )
    return IdentityCheck(ok=not reasons, expected=expected,
                         observed=observed, reasons=reasons)


# ---- transient file helpers -----------------------------------------------

@contextlib.contextmanager
def transient_build_id_source(out_dir: str | Path, identity: Identity):
    """Write ``build_id.c`` to ``out_dir`` for the duration of the build.

    Atomic write via a tempfile + rename so a context-manager failure
    mid-write cannot leave a half-formed file behind. Always deletes the
    file on exit regardless of how the body raised.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / _BUILD_ID_FILENAME
    fd, name = tempfile.mkstemp(prefix=".build_id.", suffix=".c.tmp", dir=out_dir)
    try:
        with open(fd, "w", encoding="ascii", newline="\n") as f:
            f.write(generate_c_source(identity))
        Path(name).replace(target)
        yield target
    finally:
        target.unlink(missing_ok=True)
        Path(name).unlink(missing_ok=True)


# ---- uvprojx mutation (mirrors _browse_info_disabled in safe_flash.py) ----

# The <File> entry we inject follows the project's exact byte-for-byte format:
# leading two-space indent, CRLF line endings (preserved), <FileType>1</FileType>
# for C source, no <FileOption> block (matches every other C source in the
# project). The group block is injected immediately before </Groups> so it
# cannot disturb any sibling group's indices and is trivially removable on
# context exit (no string-search needed — we restore the entire file from
# the bytes captured at context entry).


def _build_file_entry() -> bytes:
    """The exact bytes we splice into ``uvprojx`` for ``build_id.c``.

    Matches the structure used by every C-source ``<File>`` in the project —
    three child tags, no ``<FileOption>``, no ``<FileControl>``. CRLF line
    endings (the file is CRLF on this Windows project — text-mode read would
    rewrite the rest of the file, hence we work in raw bytes throughout).
    """
    return (
        b'        <File>\r\n'
        b'          <FileName>' + _BUILD_ID_FILENAME.encode("ascii") + b'</FileName>\r\n'
        b'          <FileType>1</FileType>\r\n'
        b'          <FilePath>' + _BUILD_ID_FILEPATH_RELATIVE.encode("ascii") + b'</FilePath>\r\n'
        b'        </File>\r\n'
    )


def _build_group_block() -> bytes:
    """The full ``<Group>`` block we inject, complete with the ``<File>`` inside."""
    return (
        b'      <Group>\r\n'
        b'        <GroupName>' + _BUILD_ID_GROUP_NAME.encode("ascii") + b'</GroupName>\r\n'
        b'        <Files>\r\n'
        + _build_file_entry() +
        b'        </Files>\r\n'
        b'      </Group>\r\n'
    )


@contextlib.contextmanager
def uvprojx_with_build_id(project_xml: str | Path, source_path: str | Path):
    """Inject ``build_id.c`` into the project tree, yield, restore byte-exact.

    Operates on raw bytes so the project's existing CRLF line endings are
    preserved untouched. The injected ``<Group>`` is placed immediately
    before the closing ``</Groups>`` so it does not disturb any sibling
    group's indices.

    ``source_path`` is the on-disk location of the generated C file. The
    caller is responsible for writing it before entering the context and
    deleting it after exit (we never touch it inside, to keep the
    file-mutation surface minimal and obvious).
    """
    project_xml = Path(project_xml)
    original = project_xml.read_bytes()
    if _BUILD_ID_GROUP_NAME.encode("ascii") in original:
        # Idempotent: if a previous run crashed mid-build and left the group
        # behind, treat the existing file as our baseline and restore it on
        # exit (preserving whatever stale entry was there is NOT what we
        # want; the caller's context invariant is "file untouched after exit"
        # so we still restore the freshly-captured bytes).
        pass
    sentinel = b"</Groups>"                             # byte-exact anchor
    if sentinel not in original:
        raise ValueError(f"{project_xml} does not contain </Groups>; cannot inject")
    group_block = _build_group_block()
    mutated = original.replace(sentinel, group_block + sentinel, 1)
    try:
        project_xml.write_bytes(mutated)
        yield
    finally:
        project_xml.write_bytes(original)
        # Source file is the caller's to clean up; we never touch it here.
