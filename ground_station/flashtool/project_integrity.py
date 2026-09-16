"""Validate that every Keil compilation unit exists before building."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


@dataclass(frozen=True)
class MissingProjectSource:
    """A compiled Keil project entry whose source path is absent."""

    name: str
    path: Path


def missing_compilation_units(project: str | Path) -> list[MissingProjectSource]:
    """Return absent paths for entries Keil marks as C/ASM compilation units."""
    project_path = Path(project)
    root = ElementTree.parse(project_path).getroot()
    missing: list[MissingProjectSource] = []
    for file_node in root.findall(".//File"):
        file_type = file_node.findtext("FileType")
        file_name = file_node.findtext("FileName")
        file_path = file_node.findtext("FilePath")
        if file_type != "1" or not file_name or not file_path:
            continue
        source = (project_path.parent / file_path).resolve()
        if not source.is_file():
            missing.append(MissingProjectSource(file_name, source))
    return missing


def require_complete_project(project: str | Path) -> None:
    """Raise before UV4 runs if the project references missing C/ASM sources."""
    missing = missing_compilation_units(project)
    if not missing:
        return
    listed = "\n".join("  - {} ({})".format(item.name, item.path) for item in missing)
    raise RuntimeError(
        "Keil project lists missing compiled source file(s); refusing to build:\n"
        + listed
    )
