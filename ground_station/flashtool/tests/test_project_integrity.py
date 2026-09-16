from pathlib import Path

import pytest

from ground_station.flashtool.project_integrity import (
    missing_compilation_units,
    require_complete_project,
)


def _project(path: Path, source: str) -> Path:
    project = path / "JX_FLY.uvprojx"
    project.write_text(
        "<Project><File><FileName>unit.c</FileName><FileType>1</FileType>"
        "<FilePath>{}</FilePath></File></Project>".format(source),
        encoding="utf-8",
    )
    return project


def test_missing_compilation_unit_is_reported(tmp_path):
    project = _project(tmp_path, "missing.c")

    missing = missing_compilation_units(project)

    assert len(missing) == 1
    assert missing[0].name == "unit.c"
    assert missing[0].path == (tmp_path / "missing.c").resolve()


def test_complete_project_passes(tmp_path):
    (tmp_path / "unit.c").write_text("int value;", encoding="utf-8")
    project = _project(tmp_path, "unit.c")

    require_complete_project(project)


def test_missing_project_source_blocks_build(tmp_path):
    project = _project(tmp_path, "missing.c")

    with pytest.raises(RuntimeError, match="missing compiled source"):
        require_complete_project(project)
