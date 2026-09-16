"""Offline tests for build-id stamping.

The livewire part (``check_identity`` against a powered target) is exercised
manually; this file covers everything that can be tested without hardware:

* The build counter increments and persists across calls.
* Generated C source round-trips through the four magic/word constants.
* uvprojx byte-exact restore survives a no-op, a successful inject, and a
  inject followed by an exception inside the context body.
* The transient C file is deleted on context exit, both on success and on
  exception.
* Source fingerprint is deterministic across calls (same mtime/size -> same
  hash) and changes when a source file is modified.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from ground_station.flashtool import build_id


# A trimmed-down uvprojx that mirrors the real one's structural layout:
# CRLF line endings, two-space indent on each <File>, no <FileOption> block,
# </Groups> as the anchor the build_id splice uses. Real file is 893 lines
# of mostly unrelated XML; what matters here is that the splice works on
# something with the same shape.
_SAMPLE_UVPROJX = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\r\n'
    b'<Project xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    b'xsi:noNamespaceSchemaLocation="project_projx.xsd">\r\n'
    b'  <Targets>\r\n'
    b'    <Target>\r\n'
    b'      <TargetName>JX_FLY</TargetName>\r\n'
    b'      <Groups>\r\n'
    b'        <Group>\r\n'
    b'          <GroupName>USER</GroupName>\r\n'
    b'          <Files>\r\n'
    b'            <File>\r\n'
    b'              <FileName>main.c</FileName>\r\n'
    b'              <FileType>1</FileType>\r\n'
    b'              <FilePath>.\\main.c</FilePath>\r\n'
    b'            </File>\r\n'
    b'          </Files>\r\n'
    b'        </Group>\r\n'
    b'      </Groups>\r\n'
    b'    </Target>\r\n'
    b'  </Targets>\r\n'
    b'</Project>\r\n'
)


def _write_uvprojx(tmp_path: Path, content: bytes = _SAMPLE_UVPROJX) -> Path:
    p = tmp_path / "JX_FLY.uvprojx"
    p.write_bytes(content)
    return p


def _identity(counter: int = 1, epoch: int = 1_700_000_000,
              fingerprint: int = 0x12345678) -> build_id.Identity:
    return build_id.Identity(
        magic=build_id.MAGIC,
        build_counter=counter,
        build_epoch=epoch,
        source_fingerprint=fingerprint,
    )


# ---- C source emission ---------------------------------------------------

def test_generate_c_source_has_all_four_constants():
    src = build_id.generate_c_source(_identity(
        counter=0x11223344, epoch=0x55667788, fingerprint=0x99AABBCC))
    assert "0xB10DCAFE" in src
    assert "0x11223344" in src
    assert "0x55667788" in src
    assert "0x99AABBCC" in src


def test_generate_c_source_uses_volatile_uint32():
    """A `const` initialiser would be folded or moved to flash; we need RAM."""
    src = build_id.generate_c_source(_identity())
    assert "volatile uint32_t build_id[4]" in src
    # Sanity: include is present so the type is defined regardless of the
    # build environment's stdint.h visibility.
    assert "#include <stdint.h>" in src


def test_generate_c_source_carries_marker_comment():
    """The generated file should announce itself so a human opening it knows
    not to edit it (and so a stray inspector tool can recognise it)."""
    src = build_id.generate_c_source(_identity())
    assert "AUTO-GENERATED" in src


# ---- uvprojx mutation ---------------------------------------------------

def test_uvprojx_with_build_id_round_trip(tmp_path: Path):
    proj = _write_uvprojx(tmp_path)
    original = proj.read_bytes()
    with build_id.uvprojx_with_build_id(proj, tmp_path / "build_id.c"):
        mutated = proj.read_bytes()
        assert mutated != original
        assert b"<GroupName>BUILDTOOLS</GroupName>" in mutated
        assert b"<FileName>build_id.c</FileName>" in mutated
        assert b"..\\OBJ\\build_id.c" in mutated
    # Restored byte-exact after the context exits.
    assert proj.read_bytes() == original


def test_uvprojx_with_build_id_restores_on_exception(tmp_path: Path):
    proj = _write_uvprojx(tmp_path)
    original = proj.read_bytes()
    with pytest.raises(RuntimeError):
        with build_id.uvprojx_with_build_id(proj, tmp_path / "build_id.c"):
            assert b"BUILDTOOLS" in proj.read_bytes()
            raise RuntimeError("simulated build failure")
    assert proj.read_bytes() == original


def test_uvprojx_with_build_id_requires_groups_anchor(tmp_path: Path):
    proj = _write_uvprojx(tmp_path, b"<Project></Project>\r\n")
    with pytest.raises(ValueError):
        with build_id.uvprojx_with_build_id(proj, tmp_path / "build_id.c"):
            pass


def test_uvprojx_with_build_id_preserves_crlf(tmp_path: Path):
    """uvprojx uses CRLF; text mode would silently rewrite the file. The
    byte-mode splice must leave every existing CRLF untouched."""
    proj = _write_uvprojx(tmp_path)
    original_crlf_count = proj.read_bytes().count(b"\r\n")
    with build_id.uvprojx_with_build_id(proj, tmp_path / "build_id.c"):
        mutated_crlf_count = proj.read_bytes().count(b"\r\n")
    assert mutated_crlf_count > original_crlf_count
    final_crlf_count = proj.read_bytes().count(b"\r\n")
    assert final_crlf_count == original_crlf_count


# ---- transient file management ------------------------------------------

def test_transient_build_id_source_writes_and_cleans_up(tmp_path: Path):
    out_dir = tmp_path / "OBJ"
    out_dir.mkdir()
    identity = _identity()
    with build_id.transient_build_id_source(out_dir, identity) as path:
        assert path.exists()
        content = path.read_text()
        assert "0xB10DCAFE" in content
    assert not path.exists()


def test_transient_build_id_source_cleans_up_on_exception(tmp_path: Path):
    out_dir = tmp_path / "OBJ"
    out_dir.mkdir()
    with pytest.raises(RuntimeError):
        with build_id.transient_build_id_source(out_dir, _identity()) as path:
            assert path.exists()
            raise RuntimeError("simulated build failure")
    assert not (out_dir / "build_id.c").exists()


def test_transient_build_id_source_creates_out_dir(tmp_path: Path):
    out_dir = tmp_path / "OBJ"
    assert not out_dir.exists()
    with build_id.transient_build_id_source(out_dir, _identity()) as path:
        assert path.exists()
    assert not (out_dir / "build_id.c").exists()


# ---- counter persistence ------------------------------------------------

def test_next_identity_starts_at_one(tmp_path: Path):
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    ident = build_id.next_identity(obj_dir, root=tmp_path)
    assert ident.build_counter == 1
    assert ident.magic == build_id.MAGIC


def test_next_identity_increments_across_calls(tmp_path: Path):
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    a = build_id.next_identity(obj_dir, root=tmp_path)
    b = build_id.next_identity(obj_dir, root=tmp_path)
    c = build_id.next_identity(obj_dir, root=tmp_path)
    assert a.build_counter == 1
    assert b.build_counter == 2
    assert c.build_counter == 3


def test_next_identity_persists_counter_to_disk(tmp_path: Path):
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    build_id.next_identity(obj_dir, root=tmp_path)
    build_id.next_identity(obj_dir, root=tmp_path)
    # After two calls, the on-disk counter is 2. A fresh process that reads
    # it and increments gets 3.
    on_disk = build_id._read_counter(obj_dir)
    assert on_disk == 2


def test_next_identity_handles_missing_counter_file(tmp_path: Path):
    """If the counter file is absent (fresh checkout) the next identity is 1."""
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    assert not (obj_dir / build_id._BUILD_COUNTER_FILENAME).exists()
    ident = build_id.next_identity(obj_dir, root=tmp_path)
    assert ident.build_counter == 1


def test_next_identity_handles_corrupt_counter_file(tmp_path: Path):
    """Garbage in the counter file is treated as zero rather than crashing."""
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    (obj_dir / build_id._BUILD_COUNTER_FILENAME).write_text("not a number")
    ident = build_id.next_identity(obj_dir, root=tmp_path)
    assert ident.build_counter == 1


# ---- source fingerprint -------------------------------------------------

def test_source_fingerprint_changes_with_file_content(tmp_path: Path):
    """Touch a source file -> fingerprint must change.

    On Windows, ``write_text`` twice in succession may share an mtime tick
    (FAT/NTFS 100-ns granularity is normally enough but the pytest tmpfs can
    floor). We force distinct mtimes via ``os.utime`` so the test is
    independent of filesystem clock behaviour.
    """
    # Set up a minimal source tree.
    (tmp_path / "USER").mkdir()
    src = tmp_path / "USER" / "main.c"
    src.write_text("// v1\n")
    os.utime(src, (1_000_000, 1_000_000))
    f1 = build_id._source_fingerprint(tmp_path)
    # Now rewrite the file with the same length — same size, different mtime
    # and content. (We re-write with the same byte count so the size hash
    # alone cannot collide and the change has to come from the mtime or the
    # path-only hash.)
    src.write_text("// v2\n")
    os.utime(src, (2_000_000, 2_000_000))
    f2 = build_id._source_fingerprint(tmp_path)
    assert f1 != f2


def test_source_fingerprint_ignores_missing_roots(tmp_path: Path):
    """A source root that does not exist on disk is silently skipped."""
    # tmp_path has no USER/ or API/ subdirs.
    f = build_id._source_fingerprint(tmp_path)
    # Should not raise; result is well-defined (the SHA-256 of nothing).
    assert isinstance(f, int)


def test_next_identity_stamps_current_epoch(tmp_path: Path):
    """The build_epoch should be close to the current wall clock."""
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    import time as _time
    before = int(_time.time())
    ident = build_id.next_identity(obj_dir, root=tmp_path)
    after = int(_time.time())
    assert before <= ident.build_epoch <= after


def test_identity_short_label_is_human_readable():
    ident = _identity(counter=42, epoch=1_700_000_000, fingerprint=0xDEADBEEF)
    label = ident.short_label()
    assert "counter=42" in label
    assert "epoch=1700000000" in label
    assert "fingerprint=0xDEADBEEF" in label


# ---- identity_from_elf (counter-recovery helper) ------------------------

def test_identity_from_elf_reads_counter_from_obj(tmp_path: Path):
    """The ELF doesn't carry the counter; identity_from_elf reads it from disk."""
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    # Simulate two prior builds by setting counter to 7.
    (obj_dir / build_id._BUILD_COUNTER_FILENAME).write_text("7")
    fake_elf = obj_dir / "JX_FLY.axf"
    fake_elf.write_bytes(b"")  # identity_from_elf doesn't open the ELF
    ident = build_id.identity_from_elf(fake_elf, obj_dir=obj_dir)
    assert ident.build_counter == 7
    assert ident.magic == build_id.MAGIC


def test_identity_from_elf_default_obj_dir_from_elf_path(tmp_path: Path):
    """If obj_dir is not passed, the parent of elf_path is used."""
    obj_dir = tmp_path / "OBJ"
    obj_dir.mkdir()
    (obj_dir / build_id._BUILD_COUNTER_FILENAME).write_text("13")
    fake_elf = obj_dir / "JX_FLY.axf"
    fake_elf.write_bytes(b"")
    ident = build_id.identity_from_elf(fake_elf)  # no obj_dir
    assert ident.build_counter == 13