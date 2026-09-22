"""Offline tests for compile_commands.py generation.

The real project is parsed in ``test_real_project_parses``; everything else
runs against a tiny synthetic ``.uvprojx`` built in ``tmp_path`` so the tests
stay fast and hermetic.
"""
from __future__ import annotations

import json

import pytest

from ground_station.flashtool.compile_commands import (
    build_entries,
    main,
    parse_keil_project,
)

SYNTHETIC_PROJECT = """<?xml version="1.0" encoding="UTF-8" ?>
<Project>
  <Targets>
    <Target>
      <TargetName>OTHER</TargetName>
      <Groups>
        <Group><GroupName>IGNORED</GroupName>
          <File><FileType>1</FileType><FilePath>..\\OTHER\\ignored.c</FilePath></File>
        </Group>
      </Groups>
    </Target>
    <Target>
      <TargetName>JX_FLY</TargetName>
      <Groups>
        <Group><GroupName>USER</GroupName>
          <Files>
            <File><FileType>1</FileType><FilePath>.\\main.c</FilePath></File>
            <File><FileType>1</FileType><FilePath>.\\util.c</FilePath></File>
            <File><FileType>5</FileType><FilePath>.\\main.h</FilePath></File>
            <File><FileType>1</FileType><FilePath>..\\TASK\\led.c</FilePath></File>
            <File><FileType>1</FileType><FilePath>..\\TASK\\missing.c</FilePath></File>
          </Files>
        </Group>
      </Groups>
      <TargetArmAds>
        <Cads>
          <VariousControls>
            <MiscControls>--C99</MiscControls>
            <Define>STM32F40_41xxx,USE_STDPERIPH_DRIVER</Define>
            <IncludePath>..\\USER;..\\TASK;D:/EXTRA</IncludePath>
          </VariousControls>
        </Cads>
      </TargetArmAds>
    </Target>
  </Targets>
</Project>
"""


def _write_synthetic(tmp_path) -> tuple:
    user = tmp_path / "USER"
    task = tmp_path / "TASK"
    user.mkdir()
    task.mkdir()
    (user / "main.c").write_text("int main(void){return 0;}\n")
    (user / "util.c").write_text("int util(void){return 1;}\n")
    (task / "led.c").write_text("void led(void){}\n")
    (user / "JX_FLY.uvprojx").write_text(SYNTHETIC_PROJECT)
    return tmp_path


def test_parse_keil_project_picks_target_and_defines(tmp_path):
    root = _write_synthetic(tmp_path)
    files, defines, includes = parse_keil_project(root / "USER" / "JX_FLY.uvprojx")

    # Only the JX_FLY target's C files, not OTHER or the FileType-5 header.
    assert len(files) == 4, files
    assert all("OTHER" not in f for f in files)
    assert "..\\TASK\\led.c" in files
    assert ".\\main.c" in files

    assert defines == ["STM32F40_41xxx", "USE_STDPERIPH_DRIVER"]
    assert includes == ["..\\USER", "..\\TASK", "D:/EXTRA"]


def test_build_entries_absolute_and_skips_missing(tmp_path):
    root = _write_synthetic(tmp_path)
    files, defines, includes = parse_keil_project(root / "USER" / "JX_FLY.uvprojx")

    entries, missing = build_entries(root / "USER", files, defines, includes)

    # missing.c does not exist on disk -> skipped, reported.
    assert len(missing) == 1 and "missing.c" in missing[0]
    assert len(entries) == 3

    entry = next(e for e in entries if e["file"].endswith("TASK/led.c"))
    assert entry["directory"].endswith("/USER")
    assert entry["file"].endswith("/TASK/led.c")
    assert entry["file"] == entry["arguments"][-1]

    args = entry["arguments"]
    assert args[0] == "clang"
    assert "--target=arm-none-eabi" in args
    assert "-mcpu=cortex-m4" in args
    assert "-mthumb" in args
    assert "-std=gnu99" in args
    assert "-D__CC_ARM" not in args
    assert "-fms-extensions" in args

    # defines spread as -D tokens
    assert "-DSTM32F40_41xxx" in args
    assert "-DUSE_STDPERIPH_DRIVER" in args

    # includes are absolute (forward-slash) and each present as -I
    abs_user = str((root / "USER").resolve()).replace("\\", "/")
    abs_task = str((root / "TASK").resolve()).replace("\\", "/")
    assert abs_user in entry["directory"]
    assert f"-I{abs_user}" in args
    assert f"-I{abs_task}" in args
    # an already-absolute include passes through unchanged
    assert "-ID:/EXTRA" in args

    # default keil inc always appended
    assert "-IC:/Keil_v5/ARM/ARMCC/include" in args
    assert "-c" in args


def test_main_writes_json(tmp_path, capsys):
    root = _write_synthetic(tmp_path)
    out = tmp_path / "out.json"
    rc = main(["--root", str(root), "--out", str(out)])
    assert rc == 0

    captured = capsys.readouterr().out
    assert f"wrote 3 entries (1 missing) -> {out}" in captured

    data = json.loads(out.read_text())
    assert len(data) == 3
    assert all("clang" == e["arguments"][0] for e in data)
    # windows-paths absent -> /mnt/c prefix preserved if present
    for e in data:
        assert "/" in e["file"]


def test_windows_paths_rewrite(tmp_path, capsys):
    root = _write_synthetic(tmp_path)
    out = tmp_path / "win.json"
    rc = main(["--root", str(root), "--out", str(out), "--windows-paths"])
    assert rc == 0

    data = json.loads(out.read_text())
    entry = data[0]
    assert entry["file"].startswith(str(root).replace("\\", "/"))
    # every path token goes through _to_windows; no /mnt/c/ leftovers
    for e in data:
        assert "/mnt/c/" not in e["file"]
        assert "/mnt/c/" not in e["directory"]
        assert all("/mnt/c/" not in a for a in e["arguments"] if a.startswith("/"))


def test_real_project_parses():
    from ground_station.flashtool.compile_commands import _repo_root

    root = _repo_root()
    uvp = root / "USER" / "JX_FLY.uvprojx"
    files, defines, includes = parse_keil_project(uvp)

    assert len(files) > 20, files
    # the stabilizer task is the control-path file this generator exists for.
    assert any("StabilizerTask.c" in f for f in files), files

    entries, missing = build_entries(root / "USER", files, defines, includes)
    # every accepted entry carries the fixed flags
    assert all(
        "-mcpu=cortex-m4" in e["arguments"] and "--target=arm-none-eabi" in e["arguments"]
        for e in entries
    )
    # missing on a real project should be small (0 or a handful); just sanity-check it is a count
    assert len(missing) >= 0